"""Admin tools: Asterisk reload and restart, server power, config files, log.

Asterisk actions go over the manager connection. Saving a config file,
rebooting, and powering off go through the root helper (deploy/smelite-admin),
which accepts only fixed actions and file names.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("smelite")

CONFIG_FILES = ("rpt.conf", "iax.conf", "extensions.conf")
SMELITE_FILES = ("asdb.txt", "node_info.ini")
DEMO_TEXT = {
    "asdb.txt": "; node|callsign|description|location\n1998|N0CALL|SM-Elite demo repeater|Local\n",
    "node_info.ini": "[general]\nNODE = 1998 1999\nWX_CODE = 00000\nTEMP_UNIT = F\n",
}
ASTERISK_ACTIONS = {
    "reload": ("core reload", "Reloaded Asterisk configuration."),
    "rpt_restart": ("rpt restart", "app_rpt is restarting. Links drop and come back in a few seconds."),
    "asterisk_restart": ("core restart gracefully", "Asterisk is restarting. Links come back in about 20 seconds."),
}
POWER_ACTIONS = ("reboot", "poweroff")
CONFIG_NAME = re.compile(r"(smelite|nodes|commands|dvswitch|node_info|favorites[A-Za-z0-9_-]{0,40})\.ini|asdb\.txt")
MAX_TEXT = 1024 * 1024
STAMP = re.compile(r"\d{8}-\d{6}(\.\d{3})?")


def _stamp_iso(stamp: str) -> str:
    when = datetime.strptime(stamp[:15], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    return when.isoformat()


class AdminError(ValueError):
    """An admin request was refused."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AdminTools:
    def __init__(
        self,
        enabled: bool,
        config_dir: Path,
        log_file: Path,
        helper: str,
        demo: bool = False,
        smelite_dir: Path | None = None,
    ):
        self.enabled = enabled
        self.config_dir = config_dir
        self.smelite_dir = smelite_dir
        self.log_file = log_file
        self.helper = helper
        self.demo = demo
        self._demo_files = {
            name: f"; demo copy of {name}\n[1998]\nrxchannel = dahdi/pseudo\n".encode() for name in CONFIG_FILES
        }
        self._demo_files.update({name: text.encode() for name, text in DEMO_TEXT.items()})
        self._demo_backups: dict[str, list[tuple[str, bytes]]] = {}

    def _path(self, name: str) -> Path:
        if name in CONFIG_FILES:
            return self.config_dir / name
        if name in SMELITE_FILES and self.smelite_dir is not None:
            return self.smelite_dir / name
        raise AdminError("that file is not editable here")

    def _bytes(self, name: str) -> bytes:
        path = self._path(name)
        if self.demo:
            return self._demo_files[name]
        if name in SMELITE_FILES and not path.exists():
            return b""
        return path.read_bytes()

    def files(self) -> list[dict]:
        found = []
        names = CONFIG_FILES + (SMELITE_FILES if self.smelite_dir is not None else ())
        for name in names:
            try:
                size = len(self._bytes(name))
            except OSError:
                continue
            group = "sm-elite" if name in SMELITE_FILES else "asterisk"
            found.append({"name": name, "size": size, "group": group})
        return found

    def read(self, name: str) -> dict:
        data = self._bytes(name)
        return {"name": name, "text": data.decode("utf-8", errors="replace"), "sha256": digest(data)}

    async def write(self, name: str, text: str, expected: str, actor: str) -> str:
        self._path(name)
        data = text.replace("\r\n", "\n").encode("utf-8")
        if len(data) > MAX_TEXT:
            raise AdminError("file is too large")
        if self.demo:
            return self._demo_replace(name, expected, data)
        backup = await self.run_helper(["write", name, expected], data)
        log.warning("admin: %s saved %s (backup %s)", actor, name, backup)
        return backup

    def _demo_replace(self, name: str, expected: str, data: bytes) -> str:
        if digest(self._demo_files[name]) != expected:
            raise AdminError("the file changed since it was loaded; reload it and try again")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}"
        self._demo_backups.setdefault(name, []).insert(0, (stamp, self._demo_files[name]))
        self._demo_files[name] = data
        return f"demo backup {stamp}"

    async def backups(self, name: str) -> list[dict]:
        self._path(name)
        if self.demo:
            rows = [(stamp, len(data)) for stamp, data in self._demo_backups.get(name, [])]
        else:
            out = await self.run_helper(["backups", name])
            rows = []
            for line in out.splitlines():
                stamp, _, size = line.partition("\t")
                if STAMP.fullmatch(stamp) and size.isdigit():
                    rows.append((stamp, int(size)))
        return [{"stamp": stamp, "size": size, "when": _stamp_iso(stamp)} for stamp, size in rows]

    async def backup_compare(self, name: str, stamp: str) -> dict:
        old = await self._backup_bytes(name, stamp)
        current = self._bytes(name)
        diff = difflib.unified_diff(
            current.decode("utf-8", errors="replace").splitlines(),
            old.decode("utf-8", errors="replace").splitlines(),
            fromfile=f"{name} (now)",
            tofile=f"{name} (backup {stamp})",
            lineterm="",
            n=2,
        )
        return {"name": name, "stamp": stamp, "diff": "\n".join(diff), "same": old == current}

    async def restore(self, name: str, stamp: str, expected: str, actor: str) -> str:
        if self.demo:
            return self._demo_replace(name, expected, await self._backup_bytes(name, stamp))
        self._check_stamp(name, stamp)
        backup = await self.run_helper(["restore", name, stamp, expected])
        log.warning("admin: %s restored %s from %s (backup %s)", actor, name, stamp, backup)
        return backup

    def _check_stamp(self, name: str, stamp: str) -> None:
        self._path(name)
        if not STAMP.fullmatch(stamp):
            raise AdminError("unknown backup")

    async def _backup_bytes(self, name: str, stamp: str) -> bytes:
        self._check_stamp(name, stamp)
        if self.demo:
            for found, data in self._demo_backups.get(name, []):
                if found == stamp:
                    return data
            raise AdminError("unknown backup")
        return await self.run_helper_bytes(["backup-show", name, stamp])

    async def node_status(self) -> str:
        if self.demo:
            return "demo: node status update skipped"
        return await self.run_helper(["node-status"])

    async def write_config(self, name: str, data: bytes, actor: str) -> str:
        """Replace one file in SM-Elite's config folder; it takes effect after restart_dashboard."""
        if not CONFIG_NAME.fullmatch(name):
            raise AdminError("that file does not belong in the config folder")
        if self.demo:
            return "demo: not written"
        backup = await self.run_helper(["config-write", name], data)
        log.warning("admin: %s restored %s from a settings backup (backup %s)", actor, name, backup)
        return backup

    async def restart_dashboard(self, actor: str) -> str:
        log.warning("admin: %s restarted SM-Elite", actor)
        if self.demo:
            return "demo: restart skipped"
        return await self.run_helper(["restart-dashboard"])

    async def power(self, action: str, actor: str) -> str:
        if action not in POWER_ACTIONS:
            raise AdminError("unknown power action")
        log.warning("admin: %s requested %s", actor, action)
        if self.demo:
            return f"demo: {action} skipped"
        return await self.run_helper([action])

    async def check(self) -> bool:
        if self.demo:
            return True
        try:
            return await self.run_helper(["check"]) == "ok"
        except AdminError:
            return False

    async def run_helper(self, args: list[str], stdin: bytes | None = None) -> str:
        return (await self.run_helper_bytes(args, stdin)).decode(errors="replace").strip()

    async def run_helper_bytes(self, args: list[str], stdin: bytes | None = None) -> bytes:
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", self.helper, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise AdminError(f"admin helper could not start: {exc}") from exc
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin or b""), timeout=30)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise AdminError("admin helper timed out") from exc
        if proc.returncode != 0:
            message = err.decode(errors="replace").strip() or f"admin helper failed ({proc.returncode})"
            raise AdminError(message)
        return out

    def tail_log(self, lines: int = 300) -> str:
        if self.demo:
            return "[demo] Asterisk log lines appear here on a real node."
        lines = max(20, min(lines, 2000))
        try:
            with self.log_file.open("r", encoding="utf-8", errors="replace") as handle:
                return "".join(deque(handle, maxlen=lines))
        except OSError as exc:
            raise AdminError(f"cannot read {self.log_file}: {exc.strerror}") from exc
