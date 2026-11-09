"""Server facts for the System page, read from /proc, /sys and a few fixed commands."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import socket
from pathlib import Path

PACKAGES = ("asl3", "asl3-asterisk", "asl3-menu", "asl3-tts", "asl3-update-nodelist")
REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "vfat", "f2fs", "zfs"}
DB_LINE = re.compile(r"^/(?P<key>\S.*?)\s*:\s?(?P<value>.*)$")


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _os_name() -> str:
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return platform.system()


def _cpu_model() -> str:
    for line in _read("/proc/cpuinfo").splitlines():
        key, _, value = line.partition(":")
        if key.strip() in {"model name", "Model", "Hardware"} and value.strip():
            return value.strip()
    return platform.processor() or platform.machine()


def _memory() -> dict:
    fields = {}
    for line in _read("/proc/meminfo").splitlines():
        key, _, value = line.partition(":")
        parts = value.split()
        if parts:
            fields[key] = int(parts[0]) * 1024
    total = fields.get("MemTotal", 0)
    available = fields.get("MemAvailable", fields.get("MemFree", 0))
    swap_total = fields.get("SwapTotal", 0)
    return {
        "total": total,
        "used": max(0, total - available),
        "swap_total": swap_total,
        "swap_used": max(0, swap_total - fields.get("SwapFree", 0)),
    }


def _disks() -> list[dict]:
    seen: set[str] = set()
    disks = []
    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] not in REAL_FS or not parts[0].startswith("/dev/"):
            continue
        device, mount = parts[0], parts[1].replace("\\040", " ")
        if device in seen:
            continue
        seen.add(device)
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        disks.append({"mount": mount, "total": usage.total, "used": usage.used, "free": usage.free})
    return disks[:8]


def _temperature() -> float | None:
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        raw = _read(str(zone / "temp")).strip()
        if raw.lstrip("-").isdigit():
            return round(int(raw) / 1000, 1)
    return None


def host_facts() -> dict:
    uptime = _read("/proc/uptime").split()
    return {
        "hostname": socket.gethostname(),
        "os": _os_name(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "cpu": _cpu_model(),
        "cores": os.cpu_count() or 1,
        "uptime": float(uptime[0]) if uptime else None,
        "load": [round(value, 2) for value in os.getloadavg()],
        "memory": _memory(),
        "disks": _disks(),
        "temp_c": _temperature(),
    }


async def _run(*argv: str) -> str:
    if not shutil.which(argv[0]):
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except (OSError, asyncio.TimeoutError):
        return ""
    return out.decode(errors="replace")


async def addresses() -> list[dict]:
    try:
        links = json.loads(await _run("ip", "-j", "-br", "addr") or "[]")
    except json.JSONDecodeError:
        return []
    found = []
    for link in links:
        if link.get("ifname") == "lo":
            continue
        addrs = [f"{a['local']}/{a['prefixlen']}" for a in link.get("addr_info", []) if not a.get("local", "").startswith("fe80:")]
        found.append({"name": link.get("ifname", ""), "state": link.get("operstate", ""), "addrs": addrs})
    return found


async def services(names: list[str]) -> list[dict]:
    if not names:
        return []
    states = (await _run("systemctl", "is-active", *names)).split()
    return [{"name": name, "state": states[i] if i < len(states) else "unknown"} for i, name in enumerate(names)]


async def packages() -> list[dict]:
    out = await _run("dpkg-query", "-W", "-f=${Package}\t${Version}\n", *PACKAGES)
    found = []
    for line in out.splitlines():
        name, _, version = line.partition("\t")
        if version.strip():
            found.append({"name": name, "version": version.strip()})
    return found


def parse_database(raw: str, limit: int = 5000) -> list[dict]:
    rows = []
    for line in raw.splitlines():
        match = DB_LINE.match(line.strip())
        if match:
            rows.append({"key": match["key"], "value": match["value"].strip()})
            if len(rows) >= limit:
                break
    return rows
