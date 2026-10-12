from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from laundry.common import SHANGHAI
from laundry.maintenance import backup_database, inspect_health, temporary_restore, verify_backup
from laundry.storage import Store
from laundry.worker import dispatch_once


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "database.sqlite3"
        self.backups = self.root / "daily"
        self.store = Store(self.database)
        self.config = {"DATABASE": str(self.database), "BACKUP_DIR": str(self.backups),
                       "HEALTH_URL": "https://health.invalid/api/health"}
        (self.root / ".env").write_text("WX_SECRET=test-secret\n")
        (self.root / "config.json").write_text(json.dumps({"buildings": []}))
        (self.root / "release.json").write_text(json.dumps({"releaseId": "test-release"}))

    def tearDown(self):
        self.temporary.cleanup()

    def test_online_backup_restores_latest_wal_data_sessions_and_reminders(self):
        reader = sqlite3.connect(self.database)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM sessions").fetchone()
            session = self.store.create_session("user")
            reminder, _ = self.store.create_reminder("user", "27131", "42", "设备", time.time() + 3600, 5, "tmpl")
            self.assertTrue(Path(str(self.database) + "-wal").exists())
            result = backup_database(self.config, root=self.root)
            self.assertEqual(result["sessionCount"], 1)
            self.assertEqual(result["reminderCount"], 1)
            archive = Path(result["backup"])
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.backups.stat().st_mode & 0o777, 0o700)
            with temporary_restore(archive) as restored:
                restored_store = Store(restored / "laundry.sqlite3")
                self.assertEqual(restored_store.get_openid(session["token"]), "user")
                self.assertEqual(restored_store.get_reminder(reminder["id"], "user")["minutes"], 5)
                self.assertEqual((restored / ".env").read_text(), "WX_SECRET=test-secret\n")
                self.assertEqual(json.loads((restored / "release.json").read_text())["releaseId"], "test-release")
        finally:
            reader.close()

    def test_retention_keeps_fourteen_daily_backups_and_manual_files(self):
        self.backups.mkdir()
        manual = self.backups / "backup-manual.tar.gz"
        manual.write_text("manual")
        old_deployment = self.root / "previous-site.tar.gz"
        old_deployment.write_text("old deployment")
        start = datetime(2026, 10, 1, tzinfo=SHANGHAI)
        for day in range(16):
            backup_database(self.config, root=self.root, now=start + timedelta(days=day))
        self.assertEqual(len(list(self.backups.glob("backup-2026*.tar.gz"))), 14)
        self.assertEqual(manual.read_text(), "manual")
        self.assertEqual(old_deployment.read_text(), "old deployment")

    def test_failed_validation_keeps_previous_archive_and_no_partial_publish(self):
        backup_database(self.config, root=self.root)
        before = {p.name for p in self.backups.iterdir()}
        with patch("laundry.maintenance.verify_backup", side_effect=ValueError("corrupt snapshot")):
            with self.assertRaises(ValueError):
                backup_database(self.config, root=self.root, keep=1)
        self.assertEqual({p.name for p in self.backups.iterdir()}, before)

    def test_checksum_tampering_is_detected_on_restore(self):
        result = backup_database(self.config, root=self.root)
        tampered = self.root / "tampered.tar.gz"
        with tarfile.open(result["backup"], "r:gz") as source, tarfile.open(tampered, "w:gz") as target:
            for member in source.getmembers():
                data = source.extractfile(member).read()
                if member.name == ".env":
                    data = b"changed\n"
                member.size = len(data)
                target.addfile(member, io.BytesIO(data))
        with self.assertRaises(ValueError):
            verify_backup(tampered)

    def test_restore_rejects_paths_outside_the_temporary_directory(self):
        invalid = self.root / "invalid.tar.gz"
        with tarfile.open(invalid, "w:gz") as archive:
            member = tarfile.TarInfo("../escaped")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        with self.assertRaises(ValueError):
            verify_backup(invalid)
        self.assertFalse((self.root / "escaped").exists())

    def test_health_detects_stale_worker_and_real_overdue_backlog(self):
        now = time.time()
        self.store.touch_worker(now)
        response = SimpleNamespace(status_code=200, json=lambda: {"ok": True})
        with patch("laundry.maintenance.requests.get", return_value=response):
            self.assertTrue(inspect_health(self.config, now=now, check_services=False)["ok"])
            self.assertFalse(inspect_health(self.config, now=now + 91, check_services=False)["ok"])
            task, _ = self.store.create_reminder("user", "27131", "42", "设备", now + 3600, 5, "tmpl")
            with self.store.connection(transaction=True) as db:
                db.execute("UPDATE reminders SET next_attempt=? WHERE id=?", (now - 61, task["id"]))
            result = inspect_health(self.config, now=now, check_services=False)
            self.assertFalse(result["checks"]["backlog"])
            self.assertEqual(result["overdueCount"], 1)

    def test_health_requires_services_and_public_endpoint(self):
        self.store.touch_worker()
        response = SimpleNamespace(status_code=525, json=lambda: {"ok": False})
        with patch("laundry.maintenance.requests.get", return_value=response), \
                patch("laundry.maintenance.subprocess.run", return_value=SimpleNamespace(returncode=3)):
            result = inspect_health(self.config)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["publicAPI"])
        self.assertFalse(result["checks"]["webService"])

    def test_missing_database_is_not_created_by_health_check(self):
        self.config["DATABASE"] = str(self.root / "missing.sqlite3")
        response = SimpleNamespace(status_code=200, json=lambda: {"ok": True})
        with patch("laundry.maintenance.requests.get", return_value=response):
            result = inspect_health(self.config, check_services=False)
        self.assertFalse(result["ok"])
        self.assertFalse(Path(self.config["DATABASE"]).exists())

    def test_idle_worker_publishes_heartbeat_without_sending(self):
        self.assertFalse(dispatch_once(self.store, None))
        self.assertTrue(self.store.health()["reminderWorker"])


if __name__ == "__main__":
    unittest.main()
