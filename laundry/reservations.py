"""Combine physical device state with public, anonymized reservation intervals."""

from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from copy import deepcopy
import hashlib
import json
import math
import time

from .common import iso_time
from .reservation_source import MACHINE_KEYS, SOURCE_URL, ReservationSourceError, fetch_reservations


_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="laundry-reservation-cache")
_machines = frozenset(MACHINE_KEYS.values())
UNAVAILABLE_WARNING = "预约文档暂时无法更新，是否预约未知"


def normalized_intervals(payload):
    """Validate and retain only the fields needed for an availability decision."""
    if (not isinstance(payload, dict) or not isinstance(payload.get("revision"), str)
            or not payload["revision"] or len(payload["revision"]) > 128
            or not isinstance(payload.get("reservations"), list)):
        raise ReservationSourceError("预约数据格式无效")
    by_machine = {machine: [] for machine in _machines}
    for item in payload["reservations"]:
        if not isinstance(item, dict) or item.get("machine") not in _machines:
            raise ReservationSourceError("预约设备无法确认")
        start, end = item.get("start"), item.get("end")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in (start, end)):
            raise ReservationSourceError("预约时间无效")
        if not 0 < start < end < 253402300800:
            raise ReservationSourceError("预约时间无效")
        by_machine[item["machine"]].append((start, end))
    result = []
    for machine in sorted(by_machine):
        merged = []
        for start, end in sorted(by_machine[machine]):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        result.extend({"machine": machine, "start": start, "end": end} for start, end in merged)
    return {"revision": payload["revision"], "reservations": result}


class ReservationService:
    def __init__(self, store, config=None, fetcher=fetch_reservations, clock=time.time):
        self.store, self.config = store, config or {}
        self.fetcher, self.clock = fetcher, clock

    def configured(self, building_id):
        return isinstance(self.config, dict) and building_id in self.config

    def start(self, building_id):
        # DeviceService can spend up to 12 seconds on its independent cache. Start
        # the bounded (8-second) document read alongside it, not afterwards.
        return _pool.submit(self.get, building_id) if self.configured(building_id) else None

    def resolve(self, pending, building_id, deadline):
        if pending is None:
            return self.get(building_id)
        try:
            return pending.result(timeout=max(0, deadline - time.monotonic()))
        except FutureTimeout:
            pending.cancel()
            config = self.config.get(building_id, {})
            return self.unknown(config.get("sourceUrl") if isinstance(config, dict) else None)

    @staticmethod
    def unknown(source_url=None, row=None):
        return {"status": "unavailable", "fetchedAt": iso_time(row["fetched_at"])
                if row and row.get("fetched_at") is not None else None,
                "sourceUrl": source_url, "warning": UNAVAILABLE_WARNING, "intervals": {}}

    def get(self, building_id):
        if not self.configured(building_id):
            return {"status": "not_applicable", "fetchedAt": None, "sourceUrl": None,
                    "warning": None, "intervals": {}}
        config = self.config[building_id]
        # This reader supports the verified public sheet only; a changed URL
        # requires an explicit source integration rather than silently reusing it.
        if (not isinstance(config, dict) or config.get("sourceUrl") != SOURCE_URL
                or not isinstance(config.get("devices"), dict)):
            return self.unknown()
        source_id = hashlib.sha256(config["sourceUrl"].encode()).hexdigest()
        now = self.clock()
        state, row = self.store.reservation_claim(source_id, now)
        if state in ("busy", "error"):
            return self.unknown(config["sourceUrl"], row)
        try:
            previous = normalized_intervals(json.loads(row["payload"])) if row["payload"] else None
            if state == "claimed":
                payload = normalized_intervals(self.fetcher(previous=previous, deadline=time.monotonic() + 8))
                row = self.store.reservation_success(source_id, payload, row["lease_until"], now=self.clock())
            else:
                payload = previous
            # An expired/reclaimed writer cannot present another lease's stale
            # snapshot as a successful refresh.
            if row["expires_at"] <= self.clock() or row["error"]:
                return self.unknown(config["sourceUrl"], row)
            if state == "claimed":
                payload = normalized_intervals(json.loads(row["payload"]))
        except (ReservationSourceError, ValueError, TypeError, OverflowError):
            if state == "claimed":
                row = self.store.reservation_failure(source_id, row["lease_until"], now=self.clock())
            return self.unknown(config["sourceUrl"], row)
        intervals = {machine: [] for machine in _machines}
        for item in payload["reservations"]:
            intervals[item["machine"]].append((item["start"], item["end"]))
        return {"status": "ok", "fetchedAt": iso_time(row["fetched_at"]),
                "sourceUrl": config["sourceUrl"], "warning": None, "intervals": intervals}

    @staticmethod
    def occupancy(intervals, now):
        index = bisect_right(intervals, (now, float("inf"))) - 1
        current = intervals[index] if index >= 0 and intervals[index][1] > now else None
        next_slot = intervals[index + 1] if index + 1 < len(intervals) else None
        def public(slot):
            return {"startAt": iso_time(slot[0]), "endAt": iso_time(slot[1])} if slot else None
        return public(current), public(next_slot)

    def augment(self, snapshot, reservations=None, now=None):
        result = deepcopy(snapshot)
        bid = str(snapshot["buildingId"])
        reservations = self.get(bid) if reservations is None else reservations
        result["reservations"] = {key: reservations[key] for key in ("status", "fetchedAt", "sourceUrl", "warning")}
        config = self.config.get(bid, {}) if isinstance(self.config, dict) else {}
        mapping = config.get("devices", {}) if isinstance(config, dict) else {}
        now = self.clock() if now is None else now
        for device in result["washers"] + result["dryers"]:
            physical = "available" if device["state"] == 1 else "running" if device["state"] == 2 else "unavailable"
            not_applicable = reservations["status"] == "not_applicable"
            machine = mapping.get(str(device["id"]))
            known = reservations["status"] == "ok" and machine in _machines and not snapshot.get("stale")
            current, next_slot = self.occupancy(reservations["intervals"].get(machine, []), now) if known else (None, None)
            device["reservation"] = {"status": "not_applicable" if not_applicable else "ok" if known else "unknown",
                                     "current": current, "next": next_slot}
            available = physical
            if physical == "available":
                if snapshot.get("stale") or not (known or not_applicable):
                    available = "unknown"
                elif current:
                    available = "reserved"
            device["availability"] = available
            device["availableNow"] = None if available == "unknown" else available == "available"
        return result
