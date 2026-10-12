from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from app import create_app
from laundry.common import SHANGHAI, iso_time
from laundry.reservation_source import ReservationSourceError, SOURCE_URL
from laundry.reservations import ReservationService
from laundry.settings import settings
from laundry.storage import Store


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "laundry.sqlite3")
        self.store = Store(self.path)
        self.now = 1791410400.0
        self.clock = Mock(return_value=self.now)
        self.config = settings()["RESERVATIONS"]
        self.source_id = hashlib.sha256(SOURCE_URL.encode()).hexdigest()

    def snapshot(self, states=None, stale=False, building="27136"):
        states = states or {}
        ids = list(self.config["27136"]["devices"])
        return {"buildingId": building, "washers": [{"id": key, "name": "洗衣机", "state": states.get(key, 1),
                "finishAt": None} for key in ids[:-1]],
                "dryers": [{"id": ids[-1], "name": "烘干机", "state": states.get(ids[-1], 1)}],
                "stale": stale, "fetchedAt": iso_time(self.now)}

    def service(self, reservations=None, revision="1", fetcher=None):
        payload = {"revision": revision, "reservations": reservations or []}
        return ReservationService(self.store, self.config, fetcher or Mock(return_value=payload), self.clock)

    def slot(self, machine="washer_1", start=None, end=None, **extra):
        return {"machine": machine, "start": self.now - 60 if start is None else start,
                "end": self.now + 60 if end is None else end, **extra}

    @staticmethod
    def devices(result):
        return {item["id"]: item for item in result["washers"] + result["dryers"]}

    def test_mapping_covers_each_machine_and_dryer_without_guessing_names(self):
        for device_id, machine in self.config["27136"]["devices"].items():
            with self.subTest(machine=machine):
                snapshot = self.snapshot()
                for device in snapshot["washers"] + snapshot["dryers"]:
                    device["name"] = "无法识别设备的名称"
                with self.store.connection() as db:
                    db.execute("DELETE FROM reservation_cache")
                result = self.service([self.slot(machine)]).augment(snapshot)
                devices = self.devices(result)
                self.assertEqual(devices[device_id]["availability"], "reserved")
                self.assertFalse(devices[device_id]["availableNow"])
                self.assertEqual(sum(d["availableNow"] for d in devices.values()), 4)
                self.assertEqual(result["reservations"]["status"], "ok")

    def test_future_reservation_keeps_idle_available_and_reports_next(self):
        result = self.service([self.slot(start=self.now + 120, end=self.now + 600)]).augment(self.snapshot())
        device = self.devices(result)["10658704"]
        self.assertTrue(device["availableNow"])
        self.assertIsNone(device["reservation"]["current"])
        self.assertEqual(device["reservation"]["next"]["startAt"], iso_time(self.now + 120))

    def test_end_is_exclusive_and_current_is_recalculated_during_fresh_cache(self):
        fetcher = Mock(return_value={"revision": "1", "reservations": [self.slot(start=self.now, end=self.now + 10)]})
        service = self.service(fetcher=fetcher)
        first = service.augment(self.snapshot())
        self.assertEqual(self.devices(first)["10658704"]["availability"], "reserved")
        self.clock.return_value = self.now + 10
        second = service.augment(self.snapshot())
        self.assertTrue(self.devices(second)["10658704"]["availableNow"])
        self.assertIsNone(self.devices(second)["10658704"]["reservation"]["current"])
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(first["reservations"]["fetchedAt"], second["reservations"]["fetchedAt"])

    def test_overlap_and_adjacent_reservations_merge_without_a_false_gap(self):
        reservations = [self.slot(start=self.now - 60, end=self.now + 10),
                        self.slot(start=self.now + 5, end=self.now + 40),
                        self.slot(start=self.now + 40, end=self.now + 80),
                        self.slot(start=self.now + 100, end=self.now + 200)]
        result = self.service(reservations).augment(self.snapshot())
        reservation = self.devices(result)["10658704"]["reservation"]
        self.assertEqual(reservation["current"], {"startAt": iso_time(self.now - 60), "endAt": iso_time(self.now + 80)})
        self.assertEqual(reservation["next"]["startAt"], iso_time(self.now + 100))

    def test_cross_midnight_uses_absolute_shanghai_times(self):
        start = datetime(2026, 10, 8, 23, 50, tzinfo=SHANGHAI).timestamp()
        self.clock.return_value = start + 20 * 60
        result = self.service([self.slot(start=start, end=start + 40 * 60)]).augment(self.snapshot())
        device = self.devices(result)["10658704"]
        self.assertEqual(device["availability"], "reserved")
        self.assertEqual(device["reservation"]["current"]["endAt"], "2026-10-08T16:30:00Z")

    def test_running_and_abnormal_physical_states_remain_authoritative(self):
        snapshot = self.snapshot({"10658704": 2, "10659038": 0})
        original = json.loads(json.dumps(snapshot))
        result = self.service([self.slot()]).augment(snapshot)
        devices = self.devices(result)
        self.assertEqual(devices["10658704"]["availability"], "running")
        self.assertEqual(devices["10659038"]["availability"], "unavailable")
        self.assertFalse(devices["10658704"]["availableNow"])
        self.assertEqual(snapshot, original)
        self.assertEqual(devices["10658704"]["state"], 2)

    def test_failed_document_does_not_turn_idle_into_available(self):
        fetcher = Mock(side_effect=ReservationSourceError("sensitive upstream text"))
        service = self.service(fetcher=fetcher)
        result = service.augment(self.snapshot({"10659038": 2, "10659215": 0}))
        devices = self.devices(result)
        self.assertEqual(result["reservations"]["status"], "unavailable")
        self.assertIsNone(devices["10658704"]["availableNow"])
        self.assertEqual(devices["10658704"]["reservation"]["status"], "unknown")
        self.assertEqual(devices["10659038"]["availability"], "running")
        self.assertFalse(devices["10659038"]["availableNow"])
        service.augment(self.snapshot())
        self.assertEqual(fetcher.call_count, 1)
        with self.store.connection() as db:
            row = db.execute("SELECT error,retry_after FROM reservation_cache").fetchone()
        self.assertEqual(row["error"], "source_unavailable")
        self.assertEqual(row["retry_after"], self.now + 30)
        self.assertNotIn("sensitive", json.dumps(result))

    def test_failed_refresh_preserves_last_fetch_time_but_marks_unknown(self):
        service = self.service([self.slot()])
        first = service.augment(self.snapshot())
        self.clock.return_value = self.now + 31
        service.fetcher = Mock(side_effect=ReservationSourceError())
        failed = service.augment(self.snapshot())
        self.assertEqual(failed["reservations"]["fetchedAt"], first["reservations"]["fetchedAt"])
        self.assertEqual(failed["reservations"]["status"], "unavailable")
        self.assertIsNone(self.devices(failed)["10658704"]["availableNow"])

    def test_unmapped_or_stale_devices_cannot_be_called_available(self):
        snapshot = self.snapshot()
        snapshot["washers"].append({"id": "unmapped", "name": "一号机", "state": 1})
        service = self.service([])
        devices = self.devices(service.augment(snapshot))
        self.assertEqual(devices["unmapped"]["reservation"]["status"], "unknown")
        self.assertIsNone(devices["unmapped"]["availableNow"])
        stale = self.devices(service.augment(self.snapshot(stale=True)))
        self.assertTrue(all(device["availability"] == "unknown" for device in stale.values()))
        self.assertTrue(all(device["state"] == 1 for device in stale.values()))

    def test_nonparticipating_building_and_no_configuration_make_no_requests(self):
        fetcher = Mock(side_effect=AssertionError("must not access document"))
        for config, building in ((self.config, "27131"), ({}, "27136")):
            with self.subTest(building=building, configured=bool(config)):
                service = ReservationService(self.store, config, fetcher, self.clock)
                self.assertIsNone(service.start(building))
                result = service.augment(self.snapshot(building=building))
                self.assertEqual(result["reservations"]["status"], "not_applicable")
                self.assertTrue(all(device["availableNow"] for device in self.devices(result).values()))
        fetcher.assert_not_called()

    def test_only_anonymous_intervals_are_persisted_or_returned(self):
        slot = self.slot(name="secret-person", room="private-room", studentId="123456789", raw={"row": "private-row"})
        result = self.service([slot]).augment(self.snapshot())
        with self.store.connection() as db:
            payload = db.execute("SELECT payload FROM reservation_cache").fetchone()[0]
        for private in ("secret-person", "private-room", "123456789", "private-row"):
            self.assertNotIn(private, payload)
            self.assertNotIn(private, json.dumps(result))
        self.assertEqual(set(json.loads(payload)["reservations"][0]), {"machine", "start", "end"})
        self.assertNotIn("revision", json.dumps(result))
        self.assertNotIn("intervals", result["reservations"])

    def test_cache_coalesces_across_instances_and_busy_does_not_reuse_expired_data(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def fetcher(**kwargs):
            calls.append(kwargs)
            started.set()
            release.wait(2)
            return {"revision": "1", "reservations": [self.slot()]}
        first = self.service(fetcher=fetcher)
        other = ReservationService(Store(self.path), self.config, fetcher, self.clock)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(first.get, "27136")
            self.assertTrue(started.wait(1))
            busy = other.augment(self.snapshot())
            self.assertEqual(busy["reservations"]["status"], "unavailable")
            self.assertIsNone(self.devices(busy)["10658704"]["availableNow"])
            release.set()
            self.assertEqual(pending.result()["status"], "ok")
        self.assertEqual(other.get("27136")["status"], "ok")
        self.assertEqual(len(calls), 1)
        self.clock.return_value = self.now + 31
        self.store.reservation_claim(self.source_id, self.now + 31)
        self.assertEqual(other.get("27136")["status"], "unavailable")

    def test_revision_refresh_reuses_the_previous_anonymous_snapshot(self):
        fetcher = Mock(return_value={"revision": "1", "reservations": [self.slot()]})
        service = self.service(fetcher=fetcher)
        service.get("27136")
        self.clock.return_value = self.now + 31
        fetcher.side_effect = lambda previous, deadline: previous
        refreshed = service.get("27136")
        self.assertEqual(refreshed["status"], "ok")
        self.assertEqual(refreshed["fetchedAt"], iso_time(self.now + 31))
        self.assertEqual(fetcher.call_count, 2)
        self.assertEqual(fetcher.call_args.kwargs["previous"]["revision"], "1")

    def test_invalid_source_intervals_are_unknown_instead_of_empty_available(self):
        for slot in (self.slot(start=True), self.slot(end=self.now - 600),
                     self.slot(start=float("nan")), self.slot(machine="unknown")):
            with self.subTest(slot=slot):
                with self.store.connection() as db:
                    db.execute("DELETE FROM reservation_cache")
                result = self.service([slot]).augment(self.snapshot())
                self.assertEqual(result["reservations"]["status"], "unavailable")
                self.assertIsNone(self.devices(result)["10658704"]["availableNow"])

    def test_expired_cache_lease_cannot_overwrite_a_new_owner(self):
        _, old = self.store.reservation_claim(self.source_id, self.now)
        _, new = self.store.reservation_claim(self.source_id, self.now + 31)
        payload = {"revision": "new", "reservations": []}
        self.store.reservation_success(self.source_id, payload, new["lease_until"], now=self.now + 32)
        row = self.store.reservation_success(self.source_id, {"revision": "old", "reservations": []},
                                             old["lease_until"], now=self.now + 33)
        self.assertEqual(json.loads(row["payload"])["revision"], "new")
        self.assertEqual(row["fetched_at"], self.now + 32)

    def test_schema_addition_keeps_existing_sessions_reminders_and_device_cache(self):
        token = self.store.create_session("owner")["token"]
        task, _ = self.store.create_reminder("owner", "27136", "10658704", "洗衣机", time.time() + 3600, 5, "template")
        self.store.cache_claim("27136", time.time())
        self.store.cache_success("27136", self.snapshot())
        with self.store.connection() as db:
            db.execute("DROP TABLE reservation_cache")
        migrated = Store(self.path)
        self.assertEqual(migrated.get_openid(token), "owner")
        self.assertEqual(migrated.get_reminder(task["id"], "owner")["status"], "pending")
        with migrated.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM device_cache").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation_cache").fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_api_combines_snapshot_without_changing_reminder_device_service(self):
        app = create_app({"TESTING": True, "DATABASE": self.path, "RESERVATIONS": self.config})
        snapshot = self.snapshot()
        app.extensions["laundry_devices"] = Mock(get=Mock(return_value=snapshot))
        app.extensions["laundry_reservations"] = self.service([self.slot()])
        result = app.test_client().get("/api/devices?id=27136").get_json()
        self.assertEqual(result["buildingId"], "27136")
        self.assertEqual(self.devices(result)["10658704"]["availability"], "reserved")
        self.assertNotIn("availability", snapshot["washers"][0])
        self.assertEqual(result["washers"][0]["state"], snapshot["washers"][0]["state"])

    def test_document_wait_obeys_the_shared_request_deadline(self):
        pending = Future()
        service = self.service()
        started = time.monotonic()
        result = service.resolve(pending, "27136", started - 1)
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(pending.cancelled())
        self.assertLess(time.monotonic() - started, .2)


if __name__ == "__main__":
    unittest.main()
