"""Read the public X16 reservation sheet without storing personal fields."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import math
import time

import requests

from .common import http_session


SOURCE_URL = "https://docs.qq.com/sheet/DT1FqTlV4SWVqWWhs?tab=ss_9ipbgd"
OPEN_URL = "https://docs.qq.com/dop-api/opendoc"
PAGE_URL = "https://docs.qq.com/dop-api/get/sheet"
SHEET_ID = "ss_9ipbgd"
HEADERS = {"Referer": SOURCE_URL, "User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
OPEN_PARAMS = {
    "id": "DT1FqTlV4SWVqWWhs", "tab": SHEET_ID, "startrow": 0, "endrow": 60,
    "normal": 1, "outformat": 1, "wb": 1, "nowb": 0, "noEscape": 1,
    "enableSmartsheetSplit": 1, "needSheetState": 1, "sliceStates": 1,
}
MACHINE_KEYS = {"一号机": "washer_1", "二号机": "washer_2", "三号机": "washer_3",
                "四号机": "washer_4", "烘干机": "dryer"}
_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="laundry-reservations")


class ReservationSourceError(Exception):
    """A source failure must not be interpreted as an empty reservation list."""


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ReservationSourceError("预约文档读取超时")
    return remaining


def _request_json(url, params, deadline, requester):
    remaining = _remaining(deadline)
    try:
        get = requester or http_session().get
        response = get(url, params=params, headers=HEADERS,
                       timeout=(min(2, remaining), min(4, remaining)))
        response.raise_for_status()
        value = response.json()
        _remaining(deadline)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except ReservationSourceError:
        raise
    except (requests.RequestException, ValueError, TypeError):
        # Do not include raw upstream content or identifiers in error messages.
        raise ReservationSourceError("预约文档暂时无法读取") from None


def _operations(texts):
    if not isinstance(texts, list) or not texts:
        raise ReservationSourceError("预约文档数据格式已变化")
    try:
        for text in texts:
            groups = json.loads(text["smartsheet"])
            if not isinstance(groups, list):
                raise ValueError
            for group in groups:
                if not isinstance(group, list):
                    raise ValueError
                for operation in group:
                    if not isinstance(operation, dict) or operation.get("t") not in (3005, 3028):
                        raise ValueError
                    yield operation
    except (KeyError, TypeError, ValueError):
        raise ReservationSourceError("预约文档数据格式已变化") from None


def _open_document(deadline, requester):
    value = _request_json(OPEN_URL, OPEN_PARAMS, deadline, requester)
    try:
        cv = value["clientVars"]
        privilege = cv["privilegeAttribute"]
        if not privilege.get("can_read", privilege.get("canRead")):
            raise ValueError
        collab = cv["collab_client_vars"]
        max_row = collab["maxRow"]
        if isinstance(max_row, bool) or not isinstance(max_row, int) or not 0 <= max_row <= 100000:
            raise ValueError
        revision = str(collab["rev"])
        if not revision.isdigit() or collab["padSubId"] != SHEET_ID:
            raise ValueError
        return cv, collab, max_row, revision
    except (KeyError, TypeError, ValueError):
        raise ReservationSourceError("预约文档不可公开读取或格式已变化") from None


def _schema(operations):
    schemas = [op["c"]["3"] for op in operations if op["t"] == 3005]
    if len(schemas) != 1:
        raise ReservationSourceError("预约文档字段无法确认")
    try:
        fields = schemas[0]["3"]
        labels = {}
        required = {"预约开始时间": 4, "预计使用时间(min)": 2, "机器": 17,
                    "使用人姓名": 1, "使用人寝室": 2}
        for field_id, definition in fields.items():
            label = definition.get("30")
            if label in required:
                if label in labels or definition.get("31") != required[label]:
                    raise ValueError
                labels[label] = field_id
        if set(labels) != set(required):
            raise ValueError
        options = fields[labels["机器"]]["17"]["3"]
        machines = {option["1"]: MACHINE_KEYS[option["2"]]
                    for option in options if option.get("2") in MACHINE_KEYS}
        if set(machines.values()) != set(MACHINE_KEYS.values()):
            raise ValueError
        return labels, machines
    except (KeyError, TypeError, ValueError):
        raise ReservationSourceError("预约文档字段或机器编号已变化") from None


def _merge_rows(operations, rows):
    try:
        for operation in operations:
            if operation["t"] != 3028:
                continue
            if operation["c"]["1"] != SHEET_ID:
                raise ValueError
            for row_id, row in operation["c"]["2"]["1"].items():
                cells = row.get("1", {})
                if not isinstance(cells, dict):
                    raise ValueError
                rows.setdefault(row_id, {}).update(cells)
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ReservationSourceError("预约文档记录不完整") from None


def _normalize(rows, labels, machines):
    normalized = []
    for row in rows.values():
        try:
            start_cell = row.get(labels["预约开始时间"], {})
            duration_cell = row.get(labels["预计使用时间(min)"], {})
            machine_cell = row.get(labels["机器"], {})
            name_cell = row.get(labels["使用人姓名"], {})
            room_cell = row.get(labels["使用人寝室"], {})
            for cell, expected in [(start_cell, 4), (duration_cell, 2), (machine_cell, 17),
                                   (name_cell, 1), (room_cell, 2)]:
                if not isinstance(cell, dict):
                    raise ReservationSourceError("预约文档记录格式已变化")
                if cell and cell.get("30") != expected:
                    raise ReservationSourceError("预约文档记录类型已变化")
            start_ms, duration, room = start_cell.get("4"), duration_cell.get("2"), room_cell.get("2")
            if any(isinstance(value, bool) for value in (start_ms, duration, room)):
                continue
            start, minutes = float(start_ms) / 1000, float(duration)
            selected = machine_cell.get("17", [])
            # Mirrors the sheet's required fields and 0–70 minute validation.
            # A zero-minute record occupies no time and is not an interval.
            has_name = any(run.get("1") == "text" and isinstance(run.get("2"), str)
                           and run["2"].strip() for run in name_cell.get("1", []))
            if not (math.isfinite(start) and start > 0 and math.isfinite(minutes) and 0 < minutes <= 70
                    and has_name and room is not None and math.isfinite(float(room))
                    and isinstance(selected, list) and len(selected) == 1 and selected[0] in machines):
                continue
            normalized.append({"machine": machines[selected[0]], "start": start,
                               "end": start + minutes * 60})
        except ReservationSourceError:
            raise
        except (ValueError, TypeError, AttributeError, OverflowError):
            # Incomplete or invalid form entries are not successful reservations.
            continue
    normalized.sort(key=lambda slot: (slot["start"], slot["machine"], slot["end"]))
    return normalized


def fetch_reservations(previous=None, deadline=None, requester=None):
    """Return verified intervals only; no names, rooms, row IDs or account IDs.

    ``previous`` may be a previously returned snapshot. An unchanged revision
    reuses its intervals after checking public access and the current schema.
    """
    deadline = deadline if deadline is not None else time.monotonic() + 8
    cv, collab, max_row, revision = _open_document(deadline, requester)
    try:
        initial = list(_operations(collab["initialAttributedText"]["text"]))
        labels, machines = _schema(initial)
        if (isinstance(previous, dict) and previous.get("revision") == revision
                and isinstance(previous.get("reservations"), list)):
            return {"revision": revision, "reservations": previous["reservations"]}
        rows = {}
        _merge_rows(initial, rows)
        initial_end = collab["initialAttributedText"]["text"][0]["end_row_index"]
        if isinstance(initial_end, bool) or not isinstance(initial_end, int) or not 0 <= initial_end <= 60:
            raise ReservationSourceError("预约文档分页范围无法确认")
        params = {"padId": collab["globalPadId"], "subId": SHEET_ID, "normal": 1,
                  "outformat": 1, "nowb": 1, "needSheetState": 2, "sliceStates": 1,
                  "viewId": cv["viewId"], "rev": revision}

        def page(start, end):
            result = _request_json(PAGE_URL, {**params, "startrow": start, "endrow": end},
                                   deadline, requester)
            data = result.get("data", {})
            if (result.get("retcode") != 0 or str(data.get("rev")) != revision
                    or data.get("startrow") != start or data.get("endrow") != end
                    or data.get("sheetId") != SHEET_ID or data.get("maxrow") != max_row):
                raise ReservationSourceError("预约文档更新中或分页不完整")
            return list(_operations(data["initialAttributedText"]["text"]))

        futures = [_pool.submit(page, start, min(start + 4999, max_row - 1))
                   for start in range(initial_end + 1, max_row, 5000)]
        try:
            for future in futures:
                _merge_rows(future.result(timeout=_remaining(deadline)), rows)
        except FutureTimeout:
            raise ReservationSourceError("预约文档读取超时") from None
        finally:
            for future in futures:
                future.cancel()
        if len(rows) != max_row:
            raise ReservationSourceError("预约文档记录未读取完整")
        # An edit between pages must not turn a missed reservation into availability.
        _, final_collab, final_count, final_revision = _open_document(deadline, requester)
        if (final_revision != revision or final_count != max_row
                or final_collab["globalPadId"] != collab["globalPadId"]):
            raise ReservationSourceError("预约文档更新中，请稍后查询")
        intervals = _normalize(rows, labels, machines)
        _remaining(deadline)
        return {"revision": revision, "reservations": intervals}
    except ReservationSourceError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ReservationSourceError("预约文档数据格式已变化") from None
