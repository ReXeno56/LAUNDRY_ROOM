from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time

from .common import APIError, iso_time


REMINDER_COLUMNS = (
    "id,openid,building_id,device_id,name,finish_ts,remind_ts,minutes,template_id,"
    "status,attempts,next_attempt,claimed_at,last_error,sent_at,created_at"
)
REMINDER_SCHEMA = """(
    id TEXT PRIMARY KEY, openid TEXT NOT NULL, building_id TEXT NOT NULL,
    device_id TEXT NOT NULL, name TEXT NOT NULL, finish_ts REAL NOT NULL,
    remind_ts REAL NOT NULL, minutes INTEGER NOT NULL, template_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL NOT NULL, claimed_at REAL, last_error TEXT, sent_at REAL,
    created_at REAL NOT NULL
)"""


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
        with self.connection(transaction=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, openid TEXT NOT NULL, expires_at REAL NOT NULL
            )""")
            db.execute("CREATE TABLE IF NOT EXISTS reminders " + REMINDER_SCHEMA)
            # An inline UNIQUE cannot be dropped. Rebuild it inside the same
            # transaction, so sessions and every delivery record survive migration.
            for index in db.execute("PRAGMA index_list(reminders)").fetchall():
                if not index["unique"] or index["partial"]:
                    continue
                name = index["name"].replace('"', '""')
                columns = tuple(row["name"] for row in db.execute(f'PRAGMA index_info("{name}")'))
                if columns == ("openid", "building_id", "device_id", "finish_ts"):
                    db.execute("CREATE TABLE reminders_migration " + REMINDER_SCHEMA)
                    db.execute(f"INSERT INTO reminders_migration ({REMINDER_COLUMNS}) "
                               f"SELECT {REMINDER_COLUMNS} FROM reminders")
                    db.execute("DROP TABLE reminders")
                    db.execute("ALTER TABLE reminders_migration RENAME TO reminders")
                    break
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS reminders_active_unique
                ON reminders(openid,building_id,device_id,finish_ts) WHERE status <> 'cancelled'""")
            db.execute("CREATE INDEX IF NOT EXISTS reminders_due ON reminders(status,next_attempt)")
            db.execute("CREATE INDEX IF NOT EXISTS reminders_owner ON reminders(openid,created_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS device_cache (
                building_id TEXT PRIMARY KEY, payload TEXT, fetched_at REAL,
                expires_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                error TEXT, retry_after REAL NOT NULL DEFAULT 0
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS reservation_cache (
                source_id TEXT PRIMARY KEY, payload TEXT, fetched_at REAL,
                expires_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                error TEXT, retry_after REAL NOT NULL DEFAULT 0
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS worker_heartbeat (
                id INTEGER PRIMARY KEY CHECK(id=1), updated_at REAL NOT NULL
            )""")
            if db.execute("PRAGMA user_version").fetchone()[0] < 1:
                db.execute("PRAGMA user_version=1")

    @contextmanager
    def connection(self, transaction=False):
        db = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if transaction:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if transaction:
                db.commit()
        except BaseException:
            if transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def create_session(self, openid, ttl=7 * 86400):
        token = secrets.token_hex(32)
        expires = time.time() + ttl
        with self.connection(transaction=True) as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                       (hashlib.sha256(token.encode()).hexdigest(), openid, expires))
        return {"token": token, "openid": openid, "expiresAt": iso_time(expires)}

    def get_openid(self, token):
        if not isinstance(token, str) or not token or len(token) > 128:
            raise APIError("登录已过期", 401, "invalid_session")
        with self.connection() as db:
            row = db.execute("SELECT openid FROM sessions WHERE token_hash=? AND expires_at>?",
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if row is None:
            raise APIError("登录已过期", 401, "invalid_session")
        return row["openid"]

    def create_reminder(self, openid, building_id, device_id, name, finish_ts, minutes, template_id):
        now = time.time()
        remind_ts = finish_ts - minutes * 60
        if remind_ts <= now:
            raise APIError("提醒时间已过，请减少提前分钟数", code="too_late")
        with self.connection(transaction=True) as db:
            previous = db.execute("""SELECT * FROM reminders WHERE openid=? AND building_id=?
                AND device_id=? AND finish_ts=? AND status <> 'cancelled'""",
                                  (openid, building_id, device_id, finish_ts)).fetchone()
            if previous:
                # A repeated confirmation reuses the task and its subscription quota.
                return dict(previous), False
            reminder_id = secrets.token_hex(16)
            db.execute("""INSERT INTO reminders
                (id,openid,building_id,device_id,name,finish_ts,remind_ts,minutes,template_id,
                 next_attempt,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (reminder_id, openid, building_id, device_id, name, finish_ts,
                        remind_ts, minutes, template_id, remind_ts, now))
            row = db.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()
        return dict(row), True

    def get_reminder(self, reminder_id, openid):
        with self.connection() as db:
            row = db.execute("SELECT * FROM reminders WHERE id=? AND openid=?",
                             (reminder_id, openid)).fetchone()
        if row is None:
            raise APIError("提醒不存在", 404, "not_found")
        return dict(row)

    def list_reminders(self, openid, now=None):
        now = time.time() if now is None else now
        with self.connection(transaction=True) as db:
            active = db.execute("""SELECT * FROM reminders WHERE openid=?
                AND status IN ('pending','retry','sending') ORDER BY remind_ts,created_at,id""",
                                (openid,)).fetchall()
            history = db.execute("""SELECT * FROM reminders WHERE openid=?
                AND status NOT IN ('pending','retry','sending') AND created_at>=?
                ORDER BY created_at DESC,id DESC LIMIT 50""", (openid, now - 30 * 86400)).fetchall()
        return [dict(row) for row in active], [dict(row) for row in history]

    def cancel_reminder(self, reminder_id, openid):
        # The sender uses the same immediate transaction when claiming a task.
        # Once either transaction wins, the other sees its resulting state.
        with self.connection(transaction=True) as db:
            row = db.execute("SELECT * FROM reminders WHERE id=? AND openid=?",
                             (reminder_id, openid)).fetchone()
            if row is None:
                raise APIError("提醒不存在", 404, "not_found")
            if row["status"] == "cancelled":
                return dict(row)
            if row["status"] not in ("pending", "retry"):
                message = "提醒正在发送，暂时无法取消" if row["status"] == "sending" else "提醒已结束，无法取消"
                raise APIError(message, 409, "reminder_not_cancellable")
            db.execute("UPDATE reminders SET status='cancelled' WHERE id=?", (reminder_id,))
            return dict(db.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone())

    def touch_worker(self, now=None):
        now = time.time() if now is None else now
        with self.connection(transaction=True) as db:
            db.execute("""INSERT INTO worker_heartbeat(id,updated_at) VALUES (1,?)
                ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at""", (now,))

    def health(self, now=None, max_age=90):
        now = time.time() if now is None else now
        try:
            with self.connection() as db:
                db.execute("SELECT 1 FROM reminders LIMIT 1").fetchone()
                heartbeat = db.execute("SELECT updated_at FROM worker_heartbeat WHERE id=1").fetchone()
        except sqlite3.Error:
            return {"database": False, "reminderWorker": False}
        alive = heartbeat is not None and 0 <= now - heartbeat["updated_at"] <= max_age
        return {"database": True, "reminderWorker": alive}

    def overdue_count(self, now=None, grace=60):
        now = time.time() if now is None else now
        with self.connection() as db:
            return db.execute("""SELECT COUNT(*) FROM reminders
                WHERE status IN ('pending','retry') AND next_attempt<=?""", (now - grace,)).fetchone()[0]

    def claim_due(self, now=None):
        now = time.time() if now is None else now
        with self.connection(transaction=True) as db:
            # A crashed sender may have delivered the message. Never blindly replay it.
            db.execute("""UPDATE reminders SET status='uncertain', last_error='sender_interrupted'
                WHERE status='sending' AND claimed_at < ?""", (now - 60,))
            db.execute("""UPDATE reminders SET status='failed', last_error='device_finish_time_passed'
                WHERE status IN ('pending','retry') AND finish_ts<=?""", (now,))
            row = db.execute("""SELECT * FROM reminders WHERE status IN ('pending','retry')
                AND next_attempt<=? AND finish_ts>? ORDER BY next_attempt LIMIT 1""", (now, now)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE reminders SET status='sending', claimed_at=?, attempts=attempts+1 WHERE id=?",
                       (now, row["id"]))
            return dict(db.execute("SELECT * FROM reminders WHERE id=?", (row["id"],)).fetchone())

    def finish_delivery(self, task, status, error=None, now=None):
        now = time.time() if now is None else now
        if status == "retry":
            delay = 10 if task["attempts"] == 1 else 30
            if task["attempts"] >= 3 or now + delay >= task["finish_ts"]:
                status = "failed"
        else:
            delay = 0
        with self.connection(transaction=True) as db:
            db.execute("""UPDATE reminders SET status=?,last_error=?,next_attempt=?,sent_at=?
                WHERE id=? AND status='sending' AND attempts=?""",
                       (status, error, now + delay, now if status == "sent" else None,
                        task["id"], task["attempts"]))
        return status

    def cache_claim(self, building_id, now):
        with self.connection(transaction=True) as db:
            db.execute("INSERT OR IGNORE INTO device_cache(building_id) VALUES (?)", (building_id,))
            row = dict(db.execute("SELECT * FROM device_cache WHERE building_id=?", (building_id,)).fetchone())
            if row["payload"] and row["expires_at"] > now:
                return "fresh", row
            if row["retry_after"] > now:
                return "error", row
            if row["lease_until"] > now:
                return "busy", row
            db.execute("UPDATE device_cache SET lease_until=? WHERE building_id=?", (now + 12, building_id))
            return "claimed", row

    def cache_success(self, building_id, payload):
        now = time.time()
        with self.connection(transaction=True) as db:
            db.execute("""UPDATE device_cache SET payload=?,fetched_at=?,expires_at=?,lease_until=0,
                error=NULL,retry_after=0 WHERE building_id=?""",
                       (json.dumps(payload, ensure_ascii=False), now, now + 10, building_id))

    def cache_failure(self, building_id, error):
        with self.connection(transaction=True) as db:
            db.execute("UPDATE device_cache SET lease_until=0,error=?,retry_after=? WHERE building_id=?",
                       (json.dumps({"message": error.message, "status": error.status, "code": error.code}),
                        time.time() + 2, building_id))

    def reservation_claim(self, source_id, now, lease_seconds=30):
        with self.connection(transaction=True) as db:
            db.execute("INSERT OR IGNORE INTO reservation_cache(source_id) VALUES (?)", (source_id,))
            row = dict(db.execute("SELECT * FROM reservation_cache WHERE source_id=?", (source_id,)).fetchone())
            if row["payload"] and row["expires_at"] > now:
                return "fresh", row
            if row["retry_after"] > now:
                return "error", row
            if row["lease_until"] > now:
                return "busy", row
            row["lease_until"] = now + lease_seconds
            db.execute("UPDATE reservation_cache SET lease_until=? WHERE source_id=?",
                       (row["lease_until"], source_id))
            return "claimed", row

    def reservation_success(self, source_id, payload, lease_until, now=None, ttl=30):
        now = time.time() if now is None else now
        with self.connection(transaction=True) as db:
            db.execute("""UPDATE reservation_cache SET payload=?,fetched_at=?,expires_at=?,
                lease_until=0,error=NULL,retry_after=0 WHERE source_id=? AND lease_until=?""",
                       (json.dumps(payload, separators=(",", ":")), now, now + ttl, source_id, lease_until))
            return dict(db.execute("SELECT * FROM reservation_cache WHERE source_id=?", (source_id,)).fetchone())

    def reservation_failure(self, source_id, lease_until, now=None):
        now = time.time() if now is None else now
        with self.connection(transaction=True) as db:
            # Source errors may include private document data; persist only a fixed code.
            db.execute("""UPDATE reservation_cache SET lease_until=0,error='source_unavailable',retry_after=?
                WHERE source_id=? AND lease_until=?""", (now + 30, source_id, lease_until))
            return dict(db.execute("SELECT * FROM reservation_cache WHERE source_id=?", (source_id,)).fetchone())

    def import_legacy(self, path, template_id):
        # Run explicitly after stopping the old server's background sender.
        source = json.loads(Path(path).read_text())
        if not isinstance(source, dict):
            raise ValueError("Legacy reminders must be an object")
        imported = 0
        with self.connection(transaction=True) as db:
            for openid, items in source.items():
                for item in items:
                    remind_ts = float(item["remind_ts"])
                    minutes = int(item["early_min"])
                    finish_ts = remind_ts + minutes * 60
                    name = str(item["name"])
                    device_id = "legacy:" + hashlib.sha256(name.encode()).hexdigest()[:16]
                    rid = hashlib.sha256(f"{openid}:{device_id}:{finish_ts}".encode()).hexdigest()[:32]
                    cursor = db.execute("""INSERT OR IGNORE INTO reminders
                        (id,openid,building_id,device_id,name,finish_ts,remind_ts,minutes,template_id,
                         next_attempt,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                                        (rid, openid, "", device_id, name, finish_ts, remind_ts, minutes,
                                         template_id, remind_ts, time.time()))
                    imported += cursor.rowcount
        return imported
