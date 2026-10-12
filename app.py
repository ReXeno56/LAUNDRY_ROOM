"""Web/API entrypoint. Reminder delivery runs in `python -m laundry.worker`."""
from datetime import datetime
import sqlite3
import time

from flask import Flask, Response, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from laundry.calendar import make_calendar
from laundry.common import APIError, SHANGHAI, iso_time, parse_finish, parse_minutes
from laundry.devices import DeviceService
from laundry.reservations import ReservationService
from laundry.settings import public_config, settings
from laundry.storage import Store
from laundry.wechat import WeChat


REMINDER_STATUS_TEXT = {
    "pending": "待发送", "retry": "正在重试", "sending": "发送中", "sent": "已发送",
    "failed": "发送失败", "uncertain": "结果待确认", "cancelled": "已取消",
}


def reminder_response(task, building_names=None):
    return {"reminderId": task["id"], "status": task["status"],
            "statusText": REMINDER_STATUS_TEXT.get(task["status"], "状态未知"),
            "buildingId": task["building_id"],
            "buildingName": (building_names or {}).get(task["building_id"], "历史楼栋"),
            "deviceId": task["device_id"], "name": task["name"],
            "createdAt": iso_time(task["created_at"]),
            "canCancel": task["status"] in ("pending", "retry"),
            "minutes": task["minutes"], "remindAt": datetime.fromtimestamp(task["remind_ts"], SHANGHAI).strftime("%H:%M"),
            "remindAtISO": iso_time(task["remind_ts"]), "finishAt": iso_time(task["finish_ts"])}


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(settings(test_config))
    store = Store(app.config["DATABASE"])
    app.extensions.update(laundry_store=store, laundry_devices=DeviceService(store),
                          laundry_reservations=ReservationService(store, app.config["RESERVATIONS"]),
                          laundry_wechat=WeChat(app.config))
    building_ids = {b["id"] for b in app.config["BUILDINGS"]}
    building_names = {b["id"]: b["name"] for b in app.config["BUILDINGS"]}

    def building_id(value):
        if not isinstance(value, str) or value not in building_ids:
            raise APIError("请选择有效楼栋")
        return value

    def bearer_openid():
        authorization = request.headers.get("Authorization", "")
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        return store.get_openid(token)

    @app.errorhandler(APIError)
    def api_error(error):
        return jsonify(ok=False, error=error.message, code=error.code), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="请求格式错误" if error.code == 400 else error.name,
                           code="http_error"), error.code
        return error

    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.error("Database operation failed: %s", type(error).__name__)
        return jsonify(ok=False, error="服务暂不可用，请稍后重试", code="storage_error"), 503

    @app.route("/api/config")
    def api_config():
        return jsonify(public_config(app.config))

    @app.route("/api/health")
    def api_health():
        checks = store.health()
        healthy = all(checks.values())
        return jsonify(ok=healthy, checks=checks), 200 if healthy else 503

    @app.route("/api/devices")
    def api_devices():
        bid = building_id(request.args.get("id", app.config["BUILDINGS"][0]["id"]))
        reservations = app.extensions["laundry_reservations"]
        deadline = time.monotonic() + 12
        pending = reservations.start(bid)
        snapshot = app.extensions["laundry_devices"].get(bid)
        info = reservations.resolve(pending, bid, deadline)
        return jsonify(reservations.augment(snapshot, info))

    @app.route("/api/wx-login")
    def api_wx_login():
        code = request.args.get("code", "").strip()
        if not code or len(code) > 256:
            raise APIError("缺少有效的微信登录 code")
        openid = app.extensions["laundry_wechat"].login(code)
        return jsonify(store.create_session(openid))

    @app.route("/api/wx-subscribe", methods=["POST"])
    def api_wx_subscribe():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise APIError("请求内容必须为 JSON 对象")
        minutes = parse_minutes(body.get("minutes", 5))
        openid = store.get_openid(body.get("token"))
        bid = building_id(body.get("buildingId"))
        device_id = body.get("deviceId")
        if isinstance(device_id, bool) or not isinstance(device_id, (str, int)) or not str(device_id):
            raise APIError("缺少有效设备 ID")
        device_id = str(device_id)
        finish = parse_finish(body.get("finishAt") or body.get("finishTime"))
        if finish.timestamp() - minutes * 60 <= time.time():
            raise APIError("提醒时间已过，请减少提前分钟数", code="too_late")
        if body.get("templateId", app.config["WX_TEMPLATE_ID"]) != app.config["WX_TEMPLATE_ID"]:
            raise APIError("提醒模板已更新，请重新打开页面", 409, "template_changed")
        snapshot = app.extensions["laundry_devices"].get(bid)
        if snapshot.get("stale"):
            raise APIError("设备数据未更新，请刷新后再设置提醒", 503, "stale_devices")
        matches = [d for d in snapshot["washers"] + snapshot["dryers"] if d["id"] == device_id]
        if len(matches) != 1:
            raise APIError("该楼栋中未找到有效设备")
        device = matches[0]
        if device["state"] != 2 or not device.get("finishAt"):
            raise APIError("设备状态已变化，请刷新后再设置提醒", 409, "device_changed")
        end = parse_finish(device["finishAt"])
        if abs(finish.timestamp() - end.timestamp()) >= 1:
            raise APIError("预计结束时间已变化，请刷新后重试", 409, "device_changed")
        task, created = store.create_reminder(openid, bid, device_id, device["name"],
                                              end.timestamp(), minutes, app.config["WX_TEMPLATE_ID"])
        if task["status"] in ("failed", "uncertain"):
            raise APIError("该次提醒发送失败或结果未确认，请检查订阅消息", 409, "reminder_not_active")
        return jsonify(ok=True, created=created, **reminder_response(task, building_names))

    @app.route("/api/wx-reminders")
    def api_wx_reminders():
        active, history = store.list_reminders(bearer_openid())
        return jsonify(ok=True,
                       active=[reminder_response(task, building_names) for task in active],
                       history=[reminder_response(task, building_names) for task in history])

    @app.route("/api/wx-reminders/<reminder_id>/cancel", methods=["POST"])
    def api_wx_reminder_cancel(reminder_id):
        task = store.cancel_reminder(reminder_id, bearer_openid())
        return jsonify(ok=True, **reminder_response(task, building_names))

    @app.route("/api/wx-reminders/<reminder_id>")
    def api_wx_reminder_status(reminder_id):
        task = store.get_reminder(reminder_id, bearer_openid())
        return jsonify(ok=True, **reminder_response(task, building_names), lastError=task["last_error"])

    @app.route("/api/reminder")
    def api_reminder():
        # Retain the legacy calendar query used by the existing native fallback.
        name = request.args.get("name", "洗衣机")
        if not name.strip() or len(name) > 200:
            raise APIError("设备名称长度须为 1–200 字符")
        minutes = parse_minutes(request.args.get("minutes", "5"))
        finish = parse_finish(request.args.get("finishAt") or request.args.get("finishTime"))
        if finish.timestamp() - minutes * 60 <= time.time():
            raise APIError("提醒时间已过，请减少提前分钟数", code="too_late")
        if request.args.get("buildingId") is not None:
            building_id(request.args["buildingId"])
        return Response(make_calendar(name, finish, minutes), mimetype="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=reminder.ics",
                                 "X-Content-Type-Options": "nosniff"})

    @app.route("/app.apk")
    def download_apk():
        return send_file("/root/laundry/LaundryApp.apk", mimetype="application/vnd.android.package-archive")

    @app.route("/")
    def index():
        return render_template("index.html", public_config=public_config(app.config))

    @app.after_request
    def api_cache_control(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
