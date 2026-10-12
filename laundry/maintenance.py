"""Local backup and health commands, run by the systemd maintenance timers."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import uuid

import requests

from .common import SHANGHAI
from .settings import ROOT, settings
from .storage import Store


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database_summary(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"sessions", "reminders", "device_cache"}.issubset(tables):
            raise ValueError("Backup is not a laundry database")
        return {"sessionCount": db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                "reminderCount": db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0],
                "schemaVersion": db.execute("PRAGMA user_version").fetchone()[0]}


@contextmanager
def temporary_restore(archive_path):
    """Restore a known-format backup into a private temporary directory."""
    allowed = {"laundry.sqlite3", ".env", "config.json", "release.json", "manifest.json"}
    with tempfile.TemporaryDirectory(prefix="laundry-restore-") as directory:
        target = Path(directory)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if (len(names) != len(set(names)) or not {"laundry.sqlite3", "manifest.json"}.issubset(names)
                    or any(member.name not in allowed or not member.isfile() for member in members)):
                raise ValueError("Invalid backup members")
            for member in members:
                with archive.extractfile(member) as source, (target / member.name).open("xb") as output:
                    shutil.copyfileobj(source, output)
                (target / member.name).chmod(0o600)
        manifest = json.loads((target / "manifest.json").read_text())
        if manifest.get("formatVersion") != 1 or not isinstance(manifest.get("files"), dict):
            raise ValueError("Unsupported backup manifest")
        if set(manifest["files"]) != set(names) - {"manifest.json"}:
            raise ValueError("Incomplete backup manifest")
        for filename, expected in manifest["files"].items():
            if checksum(target / filename) != expected:
                raise ValueError("Backup checksum mismatch")
        database_summary(target / "laundry.sqlite3")
        yield target


def verify_backup(archive_path):
    with temporary_restore(archive_path) as restored:
        return database_summary(restored / "laundry.sqlite3")


def backup_database(config, *, keep=14, root=ROOT, now=None):
    if type(keep) is not int or keep < 1:
        raise ValueError("At least one backup must be retained")
    database = Path(config["DATABASE"]).resolve()
    if not database.is_file():
        raise FileNotFoundError("Database does not exist")
    destination = Path(config["BACKUP_DIR"])
    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
    destination.chmod(0o700)
    moment = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    filename = "backup-" + moment.strftime("%Y%m%dT%H%M%S%z") + "-" + uuid.uuid4().hex[:8] + ".tar.gz"
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=destination) as directory:
        temporary = Path(directory)
        snapshot = temporary / "laundry.sqlite3"
        deadline = time.monotonic() + 30

        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError("Online backup timed out")

        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=3)) as source:
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target, pages=128, progress=progress, sleep=0.05)
        snapshot.chmod(0o600)
        files = {"laundry.sqlite3": checksum(snapshot)}
        for name in (".env", "config.json", "release.json"):
            path = Path(root) / name
            if path.is_file():
                shutil.copyfile(path, temporary / name)
                (temporary / name).chmod(0o600)
                files[name] = checksum(temporary / name)
        (temporary / "manifest.json").write_text(json.dumps({
            "formatVersion": 1, "createdAt": moment.isoformat(), "files": files,
        }, ensure_ascii=False, indent=2))
        (temporary / "manifest.json").chmod(0o600)
        pending_archive = temporary / filename
        with pending_archive.open("xb") as output:
            pending_archive.chmod(0o600)
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                for name in sorted(set(files) | {"manifest.json"}):
                    archive.add(temporary / name, arcname=name)
        summary = verify_backup(pending_archive)
        final_archive = destination / filename
        os.replace(pending_archive, final_archive)
    # Only daily archives produced by this command are eligible for retention.
    archives = sorted((p for p in destination.glob("backup-*.tar.gz")
                       if re.fullmatch(r"backup-\d{8}T\d{6}\+0800-[0-9a-f]{8}\.tar\.gz", p.name)),
                      key=lambda p: p.name, reverse=True)
    for obsolete in archives[keep:]:
        obsolete.unlink()
    return {"ok": True, "backup": str(final_archive), "verified": True, **summary}


def inspect_health(config, *, now=None, check_services=True):
    checks = {"database": False, "reminderWorker": False, "backlog": False}
    overdue = None
    try:
        if Path(config["DATABASE"]).is_file():
            store = Store(config["DATABASE"])
            checks.update(store.health(now))
            overdue = store.overdue_count(now)
            checks["backlog"] = overdue == 0
    except (sqlite3.Error, OSError, ValueError):
        pass
    if check_services:
        for service, key in (("laundry.service", "webService"),
                             ("laundry-reminders.service", "reminderService")):
            try:
                result = subprocess.run(["systemctl", "is-active", "--quiet", service],
                                        capture_output=True, timeout=5, check=False)
                checks[key] = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                checks[key] = False
    try:
        response = requests.get(config["HEALTH_URL"], timeout=(3, 7))
        checks["publicAPI"] = response.status_code == 200 and response.json().get("ok") is True
    except (requests.RequestException, ValueError, AttributeError):
        checks["publicAPI"] = False
    return {"ok": all(checks.values()), "checks": checks, "overdueCount": overdue}


def main():
    parser = argparse.ArgumentParser(description="洗衣服务备份与运行检查")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="在线备份、恢复校验并保留最近14份")
    backup.add_argument("--keep", type=int, default=14)
    verify = commands.add_parser("verify", help="在临时目录恢复并验证备份")
    verify.add_argument("archive")
    commands.add_parser("health", help="检查公网接口、服务、派发心跳与积压")
    commands.add_parser("migrate", help="初始化或幂等迁移数据库")
    args = parser.parse_args()
    try:
        config = settings()
        if args.command == "backup":
            result = backup_database(config, keep=args.keep)
        elif args.command == "verify":
            result = {"ok": True, "verified": True, **verify_backup(args.archive)}
        elif args.command == "migrate":
            Store(config["DATABASE"])
            result = {"ok": True, **database_summary(config["DATABASE"])}
        else:
            result = inspect_health(config)
    except (OSError, sqlite3.Error, ValueError, tarfile.TarError, TimeoutError) as error:
        result = {"ok": False, "error": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
