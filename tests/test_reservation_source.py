from copy import deepcopy
import json
import time
import unittest

from laundry.reservation_source import (
    MACHINE_KEYS, OPEN_URL, ReservationSourceError, SHEET_ID, fetch_reservations,
)


class Response:
    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        pass

    def json(self):
        return deepcopy(self.value)


def record(machine="washer_1", minutes=50, start=1791442800000):
    # Artificial sentinel data, never copied from the live document.
    return {"1": {
        "start": {"4": str(start), "30": 4},
        "duration": {"2": minutes, "30": 2},
        "machine": {"17": [machine], "30": 17},
        "name": {"1": [{"1": "text", "2": "PRIVATE NAME SENTINEL"}], "30": 1},
        "room": {"2": 5678, "30": 2},
    }}


def row_operation(rows):
    return {"t": 3028, "c": {"1": SHEET_ID, "2": {"1": rows}}}


def schema_operation():
    fields = {
        "start": {"30": "预约开始时间", "31": 4},
        "duration": {"30": "预计使用时间(min)", "31": 2},
        "name": {"30": "使用人姓名", "31": 1},
        "room": {"30": "使用人寝室", "31": 2},
        "machine": {"30": "机器", "31": 17, "17": {"3": [
            {"1": key, "2": label} for label, key in MACHINE_KEYS.items()
        ]}},
    }
    return {"t": 3005, "c": {"1": SHEET_ID, "3": {"3": fields}}}


class SourceFixture:
    def __init__(self, rows=None, initial_count=None):
        self.rows = rows if rows is not None else {"r0": record()}
        self.initial_count = initial_count if initial_count is not None else len(self.rows)
        self.calls = []
        self.final_revision = "7"
        self.page_revision = "7"
        self.missing_page = False
        self.permission = True
        self.schema = schema_operation()
        self.opens = 0

    def __call__(self, url, params, headers, timeout):
        self.calls.append((url, params, headers, timeout))
        if url == OPEN_URL:
            self.opens += 1
            initial = dict(list(self.rows.items())[:self.initial_count])
            return Response({"clientVars": {
                "privilegeAttribute": {"can_read": int(self.permission)},
                "viewId": "public-view", "collab_client_vars": {
                    "maxRow": len(self.rows), "rev": self.final_revision if self.opens > 1 else "7",
                    "padSubId": SHEET_ID, "globalPadId": "public-pad",
                    "initialAttributedText": {"text": [{
                        "smartsheet": json.dumps([[self.schema, row_operation(initial)]]),
                        "end_row_index": self.initial_count - 1,
                    }]},
                },
            }})
        rows = dict(list(self.rows.items())[params["startrow"]:params["endrow"] + 1])
        if self.missing_page:
            rows = {}
        return Response({"retcode": 0, "data": {
            "rev": self.page_revision, "startrow": params["startrow"], "endrow": params["endrow"],
            "maxrow": len(self.rows), "sheetId": SHEET_ID,
            "initialAttributedText": {"text": [{"smartsheet": json.dumps([[row_operation(rows)]])}]},
        }})


class ReservationSourceTests(unittest.TestCase):
    def test_parses_dynamic_field_ids_and_strips_personal_columns(self):
        fixture = SourceFixture({"r0": record("washer_2", 50), "r1": record("dryer", 60)})
        result = fetch_reservations(requester=fixture)
        self.assertEqual(result["revision"], "7")
        by_machine = {slot["machine"]: slot for slot in result["reservations"]}
        self.assertEqual(by_machine["washer_2"], {
            "machine": "washer_2", "start": 1791442800, "end": 1791445800,
        })
        self.assertEqual(by_machine["dryer"]["end"] - by_machine["dryer"]["start"], 3600)
        serialized = json.dumps(result)
        for private in ["PRIVATE NAME SENTINEL", "5678", "r0", "r1"]:
            self.assertNotIn(private, serialized)
        for _, params, headers, timeout in fixture.calls:
            self.assertEqual(headers["Accept-Encoding"], "gzip")
            self.assertNotIn("xsrf", params)
            self.assertLessEqual(sum(timeout), 6)

    def test_invalid_form_records_never_occupy_a_time_slot(self):
        rows = {str(i): record(minutes=value) for i, value in enumerate([0, -1, 71, None, "bad", float("nan")])}
        for field in ["machine", "name", "room", "start", "duration"]:
            value = record()
            del value["1"][field]
            rows[field] = value
        blank_name = record()
        blank_name["1"]["name"]["1"][0]["2"] = "  "
        rows["blank"] = blank_name
        multiple = record()
        multiple["1"]["machine"]["17"] = ["washer_1", "washer_2"]
        rows["multiple"] = multiple
        zero_room = record(minutes=70)
        zero_room["1"]["room"]["2"] = 0
        rows["valid"] = zero_room
        result = fetch_reservations(requester=SourceFixture(rows))
        self.assertEqual(len(result["reservations"]), 1)
        self.assertEqual(result["reservations"][0]["end"] - result["reservations"][0]["start"], 4200)

    def test_reads_every_page_including_record_outside_initial_rows(self):
        fixture = SourceFixture({"a": record("washer_1"), "b": record("washer_3"),
                                 "c": record("washer_4")}, initial_count=1)
        result = fetch_reservations(requester=fixture)
        self.assertEqual(len(result["reservations"]), 3)
        page = [call for call in fixture.calls if call[0] != OPEN_URL][0]
        self.assertEqual((page[1]["startrow"], page[1]["endrow"]), (1, 2))
        self.assertEqual(fixture.opens, 2)

    def test_incomplete_page_is_failure_not_an_empty_reservation_list(self):
        fixture = SourceFixture({"a": record(), "b": record()}, initial_count=1)
        fixture.missing_page = True
        with self.assertRaisesRegex(ReservationSourceError, "完整"):
            fetch_reservations(requester=fixture)

    def test_revision_change_during_page_read_or_after_pages_fails(self):
        for field in ["page_revision", "final_revision"]:
            fixture = SourceFixture({"a": record(), "b": record()}, initial_count=1)
            setattr(fixture, field, "8")
            with self.subTest(field=field), self.assertRaisesRegex(ReservationSourceError, "更新"):
                fetch_reservations(requester=fixture)

    def test_unchanged_revision_reuses_verified_snapshot_without_pages(self):
        fixture = SourceFixture({"a": record(), "b": record()}, initial_count=1)
        previous = fetch_reservations(requester=fixture)
        next_fixture = SourceFixture(fixture.rows, initial_count=1)
        result = fetch_reservations(previous=previous, requester=next_fixture)
        self.assertIs(result["reservations"], previous["reservations"])
        self.assertEqual(len(next_fixture.calls), 1)

    def test_changed_permission_or_schema_refuses_cached_revision(self):
        for change in ["permission", "schema"]:
            fixture = SourceFixture()
            if change == "permission":
                fixture.permission = False
            else:
                fixture.schema["c"]["3"]["3"]["start"]["31"] = 1
            with self.subTest(change=change), self.assertRaises(ReservationSourceError):
                fetch_reservations(previous={"revision": "7", "reservations": []}, requester=fixture)

    def test_changed_cell_type_is_unknown_instead_of_free(self):
        value = record()
        value["1"]["start"]["30"] = 1
        with self.assertRaisesRegex(ReservationSourceError, "类型"):
            fetch_reservations(requester=SourceFixture({"a": value}))

    def test_expired_budget_makes_no_request(self):
        fixture = SourceFixture()
        with self.assertRaisesRegex(ReservationSourceError, "超时"):
            fetch_reservations(deadline=time.monotonic() - 1, requester=fixture)
        self.assertEqual(fixture.calls, [])


if __name__ == "__main__":
    unittest.main()
