from datetime import datetime
import time

import requests

from .common import APIError, SHANGHAI, http_session, request_json


class DeliveryError(Exception):
    def __init__(self, category, reason):
        super().__init__(reason)
        self.category, self.reason = category, reason


class WeChat:
    def __init__(self, config):
        self.appid, self.secret = config["WX_APPID"], config["WX_SECRET"]
        self.miniprogram_state = config.get("WX_MINIPROGRAM_STATE", "formal")
        self.token, self.expires = None, 0

    def login(self, code):
        if not self.secret:
            raise APIError("订阅提醒服务尚未配置", 503, "wechat_not_configured")
        data = request_json("GET", "https://api.weixin.qq.com/sns/jscode2session", params={
            "appid": self.appid, "secret": self.secret, "js_code": code,
            "grant_type": "authorization_code",
        }, timeout=(2, 5))
        if data.get("errcode", 0) != 0:
            raise APIError("微信登录失败，请重试", 400, "wechat_login_failed")
        if not isinstance(data.get("openid"), str) or not data["openid"]:
            raise APIError("微信登录返回了无效数据", 502, "invalid_upstream_data")
        return data["openid"]

    def get_token(self, force=False):
        if not force and self.token and time.time() < self.expires - 300:
            return self.token
        if not self.secret:
            raise DeliveryError("failed", "wechat_not_configured")
        try:
            data = request_json("GET", "https://api.weixin.qq.com/cgi-bin/token", params={
                "grant_type": "client_credential", "appid": self.appid, "secret": self.secret,
            }, timeout=(2, 5))
        except APIError as exc:
            # No subscription message has been sent at this stage, so retry is safe.
            raise DeliveryError("retry", exc.code) from None
        if not data.get("access_token") or not isinstance(data.get("expires_in"), (int, float)):
            category = "retry" if data.get("errcode") == -1 else "failed"
            raise DeliveryError(category, f"token_error:{data.get('errcode', 'invalid_response')}")
        self.token, self.expires = data["access_token"], time.time() + data["expires_in"]
        return self.token

    def send(self, task):
        end = datetime.fromtimestamp(task["finish_ts"], SHANGHAI)
        body = {
            "touser": task["openid"], "template_id": task["template_id"],
            "page": "pages/index/index" + ("?id=" + task["building_id"] if task["building_id"] else ""),
            "miniprogram_state": self.miniprogram_state,
            "data": {
                "thing19": {"value": task["name"][:20]},
                "time26": {"value": end.strftime("%H:%M")},
                "thing35": {"value": f"提前{task['minutes']}分钟"},
            },
        }
        for attempt in range(2):
            token = self.get_token(force=bool(attempt))
            try:
                response = http_session().post(
                    "https://api.weixin.qq.com/cgi-bin/message/subscribe/send",
                    params={"access_token": token}, json=body, timeout=(2, 5))
                # HTTP errors or a lost response do not establish whether a send occurred.
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict) or type(result.get("errcode")) is not int:
                    raise ValueError("invalid send response")
            except requests.ConnectTimeout:
                raise DeliveryError("retry", "connect_timeout") from None
            except (requests.RequestException, ValueError):
                raise DeliveryError("uncertain", "send_result_unknown") from None
            code = result["errcode"]
            if code == 0:
                return
            if code in (40001, 40014, 42001) and not attempt:
                continue  # The explicit rejection confirms the message was not delivered.
            category = "retry" if code in (-1, 45009, 45011, 43108) else "failed"
            raise DeliveryError(category, f"wechat_error:{code}")
