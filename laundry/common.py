from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import threading

import requests

SHANGHAI = ZoneInfo("Asia/Shanghai")
_local = threading.local()


class APIError(Exception):
    def __init__(self, message, status=400, code="invalid_params"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def http_session():
    # requests.Session is shared within a thread, never across request threads.
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def iso_time(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def parse_finish(value):
    if not isinstance(value, str) or not value.strip():
        raise APIError("缺少有效的结束时间")
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SHANGHAI)
        return dt.astimezone(SHANGHAI)
    except (ValueError, OverflowError):
        raise APIError("结束时间格式错误") from None


def parse_minutes(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise APIError("提醒分钟数必须为 1–30 的整数")
    try:
        minutes = int(value)
    except ValueError:
        raise APIError("提醒分钟数必须为 1–30 的整数") from None
    if not 1 <= minutes <= 30:
        raise APIError("提醒分钟数必须为 1–30 的整数")
    return minutes


def request_json(method, url, **kwargs):
    try:
        response = http_session().request(method, url, **kwargs)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("expected object")
        return result
    except requests.Timeout:
        raise APIError("外部服务请求超时，请稍后重试", 504, "upstream_timeout") from None
    except (requests.RequestException, ValueError):
        raise APIError("外部服务暂不可用，请稍后重试", 502, "upstream_error") from None
