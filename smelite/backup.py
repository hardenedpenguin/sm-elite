"""Settings backup: one .tar.gz with the config folder and the data files.

The archive holds the AMI and hotspot passwords from the config files and the
password hashes from smelite.db, so only admins can make or restore one.
Login sessions are left out.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .admin import CONFIG_NAME
from .store import SQLiteStore

FORMAT = 1
APP = "sm-elite"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 200

GROUPS = {
    "config": "Config files",
    "accounts": "Users and scheduled actions",
    "history": "Last heard and the action log",
}
DATABASES = {
    "smelite.db": "accounts",
    "schedule.db": "accounts",
    "history.db": "history",
    "actions.db": "history",
}


class BackupError(ValueError):
    """A backup could not be made or read."""


def config_files(config_dir: Path) -> list[Path]:
    found = []
    for path in sorted(config_dir.iterdir()):
        if CONFIG_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink():
            if path.stat().st_size <= MAX_CONFIG_BYTES:
                found.append(path)
    return found


def _add(tar: tarfile.TarFile, name: str, data: bytes, when: float) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(when)
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def create(config_dir: Path, stores: dict[str, SQLiteStore], version: str, host: str, now: float | None = None) -> bytes:
    now = time.time() if now is None else now
    config = {path.name: path.read_bytes() for path in config_files(config_dir)}
    data: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="smelite-backup-") as tmp:
        for name, store in stores.items():
            path = Path(tmp) / name
            store.backup_to(path)
            if name == "smelite.db":
                db = sqlite3.connect(path)
                db.execute("DELETE FROM sessions")
                db.commit()
                db.execute("VACUUM")
                db.close()
            data[name] = path.read_bytes()
    manifest = {
        "app": APP,
        "format": FORMAT,
        "version": version,
        "host": host,
        "created": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "config": sorted(config),
        "data": sorted(data),
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        _add(tar, "manifest.json", json.dumps(manifest, indent=2).encode(), now)
        for name, blob in sorted(config.items()):
            _add(tar, f"config/{name}", blob, now)
        for name, blob in sorted(data.items()):
            _add(tar, f"data/{name}", blob, now)
    return buffer.getvalue()


@dataclass
class Archive:
    manifest: dict[str, Any]
    config: dict[str, bytes]
    data: dict[str, Path]
    users: int = 0
    admins: int = 0
    jobs: int = 0
    _tmp: tempfile.TemporaryDirectory | None = field(default=None, repr=False)

    def __enter__(self) -> Archive:
        return self

    def __exit__(self, *exc) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()

    def groups(self) -> list[str]:
        found = ["config"] if self.config else []
        for group in ("accounts", "history"):
            if any(DATABASES[name] == group for name in self.data):
                found.append(group)
        return found

    def summary(self) -> dict[str, Any]:
        return {
            "version": str(self.manifest.get("version") or ""),
            "host": str(self.manifest.get("host") or ""),
            "created": str(self.manifest.get("created") or ""),
            "config": sorted(self.config),
            "data": sorted(self.data),
            "groups": [{"id": group, "label": GROUPS[group]} for group in self.groups()],
            "users": self.users,
            "admins": self.admins,
            "jobs": self.jobs,
        }


def _check_text(name: str, data: bytes) -> None:
    if b"\x00" in data:
        raise BackupError(f"{name} in the backup contains a NUL byte")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BackupError(f"{name} in the backup is not valid UTF-8") from exc


def _check_database(name: str, path: Path, tables: tuple[str, ...]) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            healthy = db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            found = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError as exc:
            raise BackupError(f"{name} in the backup is not a SQLite file") from exc
        if not healthy:
            raise BackupError(f"{name} in the backup is damaged")
        missing = [table for table in tables if table not in found]
        if missing:
            raise BackupError(f"{name} in the backup has no {', '.join(missing)} table")
    except BaseException:
        db.close()
        raise
    return db


def read(blob: bytes, tables: dict[str, tuple[str, ...]]) -> Archive:
    """Unpack and check a backup. Use the result as a context manager; it owns a temp folder."""
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise BackupError("that file is larger than any settings backup")
    manifest: dict[str, Any] | None = None
    config: dict[str, bytes] = {}
    raw: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            members = tar.getmembers()
            if len(members) > MAX_MEMBERS or sum(member.size for member in members) > MAX_UNPACKED_BYTES:
                raise BackupError("that backup is too large to unpack")
            for member in members:
                if not member.isfile():
                    raise BackupError(f"unexpected entry {member.name!r} in the backup")
                folder, _, name = member.name.partition("/")
                if member.name == "manifest.json" and member.size <= 64 * 1024:
                    manifest = json.loads(tar.extractfile(member).read())
                elif folder == "config" and CONFIG_NAME.fullmatch(name) and member.size <= MAX_CONFIG_BYTES:
                    config[name] = tar.extractfile(member).read()
                elif folder == "data" and name in DATABASES and name in tables:
                    raw[name] = tar.extractfile(member).read()
                else:
                    raise BackupError(f"unexpected entry {member.name!r} in the backup")
    except (tarfile.TarError, EOFError, OSError, zlib.error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupError("that is not an SM-Elite settings backup") from exc
    if not isinstance(manifest, dict) or manifest.get("app") != APP:
        raise BackupError("that is not an SM-Elite settings backup")
    if manifest.get("format") != FORMAT:
        raise BackupError("that backup was made by a newer SM-Elite; upgrade this node first")
    for name, data in config.items():
        _check_text(name, data)

    tmp = tempfile.TemporaryDirectory(prefix="smelite-restore-")
    archive = Archive(manifest=manifest, config=config, data={}, _tmp=tmp)
    try:
        for name, data in raw.items():
            path = Path(tmp.name) / name
            path.write_bytes(data)
            db = _check_database(name, path, tables[name])
            try:
                if name == "smelite.db":
                    archive.users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                    archive.admins = db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
                elif name == "schedule.db":
                    archive.jobs = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            finally:
                db.close()
            archive.data[name] = path
    except BaseException:
        tmp.cleanup()
        raise
    if "smelite.db" in archive.data and archive.admins == 0:
        tmp.cleanup()
        raise BackupError("the accounts in that backup have no admin; it cannot be restored")
    return archive
