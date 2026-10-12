from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import time

from .common import APIError, iso_time, parse_finish, request_json

API_URL = "https://yshz-user.haier-ioc.com/position/deviceDetailPage"
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="laundry-upstream")


def normalize_device(raw):
    if not isinstance(raw, dict) or raw.get("id") is None:
        raise APIError("设备服务返回了无效数据", 502, "invalid_upstream_data")
    device = dict(raw)
    device["id"] = str(raw["id"])
    device["name"] = str(raw.get("name") or "未命名设备")
    try:
        device["state"] = int(raw.get("state", 0))
    except (ValueError, TypeError):
        device["state"] = 0
    try:
        dt = parse_finish(raw.get("finishTime"))
        device["finishTime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
        device["finishAt"] = iso_time(dt.timestamp())
    except APIError:
        device["finishTime"], device["finishAt"] = "", None
    return device


def fetch_devices(position_id, category_code="00", deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + 10
    result, seen, page = [], set(), 1
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise APIError("设备服务请求超时", 504, "upstream_timeout")
        data = request_json("POST", API_URL, json={
            "positionId": position_id, "categoryCode": category_code,
            "page": page, "floorCode": "", "pageSize": 20,
        }, timeout=(min(2, remaining), min(4, remaining)))
        payload = data.get("data")
        if data.get("code") != 0 or not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise APIError("设备服务返回了无效数据", 502, "invalid_upstream_data")
        items = payload["items"]
        for item in items:
            device = normalize_device(item)
            if device["id"] in seen:
                raise APIError("设备服务分页数据重复", 502, "invalid_upstream_data")
            seen.add(device["id"])
            result.append(device)
        total = payload.get("total", payload.get("totalCount"))
        if not items or (isinstance(total, int) and len(result) >= total):
            return result
        if len(items) < 20 and not (isinstance(total, int) and len(result) < total):
            return result
        page += 1


class DeviceService:
    def __init__(self, store, fetcher=fetch_devices):
        self.store, self.fetcher = store, fetcher

    @staticmethod
    def response(row, stale=False, warning=None):
        payload = json.loads(row["payload"])
        return {**payload, "fetchedAt": iso_time(row["fetched_at"]), "stale": stale,
                "warning": warning if stale else None}

    def get(self, building_id):
        waiting_deadline = time.monotonic() + 12
        while True:
            state, row = self.store.cache_claim(building_id, time.time())
            if state == "fresh":
                return self.response(row)
            if state in ("busy", "error") and row["payload"]:
                return self.response(row, True, "更新暂不可用，以下为上次采集的数据")
            if state == "error":
                error = json.loads(row["error"])
                raise APIError(error["message"], error["status"], error["code"])
            if state == "claimed":
                break
            if time.monotonic() >= waiting_deadline:
                raise APIError("设备数据更新超时", 504, "upstream_timeout")
            time.sleep(0.05)
        deadline = time.monotonic() + 10
        futures = [_pool.submit(self.fetcher, building_id, category, deadline) for category in ("00", "02")]
        try:
            washers, dryers = [f.result(timeout=max(0, deadline - time.monotonic())) for f in futures]
            payload = {"buildingId": building_id, "washers": washers, "dryers": dryers}
            self.store.cache_success(building_id, payload)
            # Read the exact persisted timestamp instead of inventing one per request.
            _, cached = self.store.cache_claim(building_id, time.time())
            return self.response(cached)
        except (APIError, FutureTimeout) as exc:
            error = exc if isinstance(exc, APIError) else APIError("设备服务请求超时", 504, "upstream_timeout")
            self.store.cache_failure(building_id, error)
            if row["payload"]:
                return self.response(row, True, error.message + "；以下为上次采集的数据")
            raise error
        finally:
            for future in futures:
                future.cancel()
