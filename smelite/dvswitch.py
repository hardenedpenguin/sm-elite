"""DVSwitch: switch a bridged node between digital modes and talkgroups.

Analog_Bridge takes remote-control messages on a UDP port it publishes in its
``/tmp/ABInfo_<port>.json`` status file. Each message is one byte 0x05, one
length byte, then text such as ``txTg=3100`` or ``ambeMode=DMR``. Switching
mode repoints Analog_Bridge at the MMDVM_Bridge ports for that mode, which
come from ``DVSwitch.ini``.

Talkgroups live in ``dvswitch.ini`` next to ``smelite.ini``. Hotspot passwords
stay in its ``[network ...]`` sections; the browser only sees aliases and
list positions.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("smelite")

MODES = ("DMR", "YSF", "P25", "NXDN", "DSTAR", "ASL", "STFU")
DEFAULT_DVSWITCH_INI = Path("/opt/MMDVM_Bridge/DVSwitch.ini")
ABINFO_GLOB = "ABInfo_*.json"
CUSTOM_TARGET = re.compile(r"[A-Za-z0-9 #*_.:/!-]{1,64}")
PARK_PORT = 30000
STEP_DELAY = 0.05
SETTLE_DELAY = 0.6


class DVSwitchError(ValueError):
    """A DVSwitch request was refused or could not be sent."""


@dataclass
class Talkgroup:
    alias: str
    target: str
    network: str = ""

    @property
    def secret(self) -> bool:
        return "@" in self.target


@dataclass
class Bridge:
    node: str
    abinfo: str = "auto"
    dvswitch_ini: Path = DEFAULT_DVSWITCH_INI
    modes: list[str] = field(default_factory=list)


@dataclass
class Layout:
    bridges: dict[str, Bridge] = field(default_factory=dict)
    talkgroups: dict[str, list[Talkgroup]] = field(default_factory=dict)
    error: str = ""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def parse_layout(text: str) -> Layout:
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",), comment_prefixes=(";", "#"))
    parser.optionxform = str
    parser.read_string(text)
    layout = Layout()
    networks: dict[str, dict[str, str]] = {}
    for section in parser.sections():
        kind, _, name = section.partition(" ")
        name = name.strip()
        entry = parser[section]
        if kind == "node" and re.fullmatch(r"\d{1,8}", name):
            modes = [m.strip().upper() for m in entry.get("modes", "").split(",") if m.strip()]
            unknown = [m for m in modes if m not in MODES]
            if unknown:
                raise DVSwitchError(f"[{section}] lists unknown modes: {', '.join(unknown)}")
            layout.bridges[name] = Bridge(
                node=name,
                abinfo=entry.get("abinfo", "auto").strip() or "auto",
                dvswitch_ini=Path(entry.get("dvswitch_ini", str(DEFAULT_DVSWITCH_INI)).strip()),
                modes=modes,
            )
        elif kind == "network" and name:
            networks[name.lower()] = {key.lower(): _unquote(value) for key, value in entry.items()}
            networks[name.lower()].setdefault("label", name)
    for section in parser.sections():
        kind, _, name = section.partition(" ")
        mode = name.strip().upper()
        if kind != "mode":
            continue
        if mode not in MODES:
            raise DVSwitchError(f"[{section}]: mode must be one of {', '.join(MODES)}")
        rows = []
        for alias, value in parser[section].items():
            rows.append(_talkgroup(alias.strip(), _unquote(value), networks, section))
        layout.talkgroups[mode] = rows
    return layout


def _talkgroup(alias: str, value: str, networks: dict[str, dict[str, str]], section: str) -> Talkgroup:
    if not value.startswith("@"):
        if not value:
            raise DVSwitchError(f"[{section}] {alias}: no talkgroup given")
        return Talkgroup(alias, value)
    ref, _, tg = value[1:].partition(" ")
    net = networks.get(ref.lower())
    if net is None:
        raise DVSwitchError(f"[{section}] {alias}: no [network {ref}] section")
    tg = tg.strip()
    if not tg:
        raise DVSwitchError(f"[{section}] {alias}: add the talkgroup after @{ref}")
    fmt = net.get("format") or "{password}@{server}:{port}!{tg}"
    values = {"password": net.get("password", ""), "server": net.get("server", ""), "port": net.get("port", "62031"), "tg": tg}
    for key, val in values.items():
        if "{" + key + "}" in fmt and not val:
            raise DVSwitchError(f"[network {ref}] needs {key}")
    target = fmt
    for key, val in values.items():
        target = target.replace("{" + key + "}", val)
    return Talkgroup(alias, target, net.get("label", ref))


def base_mode(mode: str) -> str:
    """Analog_Bridge reports YSF as YSFN (narrow) or YSFW (wide)."""
    mode = mode.strip().upper()
    return "YSF" if mode in ("YSFN", "YSFW") else mode


def read_abinfo(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def packet(command: str) -> bytes:
    body = command.encode("utf-8")
    if len(body) > 255:
        raise DVSwitchError("that command is too long for Analog_Bridge")
    return bytes([0x05, len(body)]) + body


def mode_ports(dvswitch_ini: Path, mode: str) -> tuple[int, int]:
    """(TXPort, RXPort) of MMDVM_Bridge for ``mode``."""
    parser = configparser.RawConfigParser(inline_comment_prefixes=(";",), strict=False)
    try:
        parser.read_string(dvswitch_ini.read_text(encoding="utf-8", errors="replace"))
        return int(parser[mode]["TXPort"]), int(parser[mode]["RXPort"])
    except FileNotFoundError as exc:
        raise DVSwitchError(f"{dvswitch_ini} was not found") from exc
    except (KeyError, ValueError, configparser.Error) as exc:
        raise DVSwitchError(f"{dvswitch_ini} has no TXPort/RXPort for {mode}") from exc


class DVSwitch:
    def __init__(self, path: Path, demo: bool = False, demo_node: str = "1999", tmp: Path = Path("/tmp")):
        self.path = path
        self.demo = demo
        self.demo_node = demo_node
        self.tmp = tmp
        self._cached: tuple[float, Layout] | None = None
        self._demo_state: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def layout(self) -> Layout:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return Layout()
        if self._cached and self._cached[0] == mtime:
            return self._cached[1]
        try:
            layout = parse_layout(self.path.read_text(encoding="utf-8"))
        except (OSError, configparser.Error, DVSwitchError) as exc:
            log.warning("dvswitch: %s: %s", self.path, exc)
            layout = Layout(error=f"{self.path.name}: {exc}")
        if self.demo and not layout.bridges and not layout.error:
            layout.bridges[self.demo_node] = Bridge(node=self.demo_node)
        self._cached = (mtime, layout)
        return layout

    def nodes(self) -> list[str]:
        return sorted(self.layout().bridges)

    def _bridge(self, node: str) -> tuple[Bridge, Layout]:
        layout = self.layout()
        bridge = layout.bridges.get(node)
        if bridge is None:
            raise DVSwitchError(layout.error or f"node {node} has no DVSwitch bridge")
        return bridge, layout

    def _modes(self, bridge: Bridge, layout: Layout) -> list[str]:
        if bridge.modes:
            return bridge.modes
        return [mode for mode in MODES if mode in layout.talkgroups]

    def _abinfo_path(self, bridge: Bridge) -> Path | None:
        if bridge.abinfo != "auto":
            return Path(bridge.abinfo)
        found = sorted(self.tmp.glob(ABINFO_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
        return found[0] if found else None

    def status(self, node: str) -> dict:
        bridge, layout = self._bridge(node)
        if self.demo:
            state = self._demo_state.setdefault(node, {"mode": "DMR", "tune": "", "time": time.time()})
            mode, tune, updated, version = state["mode"], state["tune"], state["time"], "demo"
        else:
            path = self._abinfo_path(bridge)
            if path is None:
                return {"online": False, "error": "no /tmp/ABInfo_*.json found; is Analog_Bridge running?"}
            try:
                info = read_abinfo(path)
                updated = path.stat().st_mtime
            except (OSError, ValueError) as exc:
                return {"online": False, "error": f"{path} could not be read: {exc}"}
            tlv = info.get("tlv") if isinstance(info.get("tlv"), dict) else {}
            ab = info.get("ab") if isinstance(info.get("ab"), dict) else {}
            mode = base_mode(str(tlv.get("ambe_mode") or ""))
            tune = str(info.get("last_tune") or "")
            version = str(ab.get("version") or "")
        return {
            "online": True,
            "mode": mode,
            "tune": self._public_tune(layout, mode, tune),
            "updated": updated,
            "version": version,
        }

    @staticmethod
    def _public_tune(layout: Layout, mode: str, tune: str) -> str:
        if not tune:
            return ""
        for row in layout.talkgroups.get(mode, []):
            if row.target == tune:
                return f"{row.alias} ({row.network})" if row.network else row.alias
        if "@" in tune:
            return tune.rsplit("!", 1)[-1] if "!" in tune else "(private)"
        return tune

    def view(self, node: str) -> dict:
        bridge, layout = self._bridge(node)
        modes = []
        for mode in self._modes(bridge, layout):
            rows = layout.talkgroups.get(mode, [])
            modes.append({
                "name": mode,
                "talkgroups": [
                    {"id": index, "alias": row.alias, "network": row.network,
                     "target": row.target.rsplit("!", 1)[-1] if row.secret and "!" in row.target
                     else "" if row.secret else row.target}
                    for index, row in enumerate(rows)
                ],
            })
        return {"node": node, "modes": modes, "status": self.status(node)}

    async def set_mode(self, node: str, mode: str, actor: str) -> dict:
        bridge, layout = self._bridge(node)
        mode = mode.strip().upper()
        if mode not in self._modes(bridge, layout):
            raise DVSwitchError(f"{mode or 'that mode'} is not set up for node {node}")
        log.info("dvswitch: %s set node %s to %s", actor, node, mode)
        if self.demo:
            self._demo_state[node] = {"mode": mode, "tune": "", "time": time.time()}
            return self.status(node)
        tx, rx = mode_ports(bridge.dvswitch_ini, mode)
        await self._send(bridge, [
            f"rxport={PARK_PORT}",
            f"message=Setting mode to {mode}",
            f"ambeMode={mode}",
            f"txport={rx}",
            f"rxport={tx}",
            "info",
        ])
        return self.status(node)

    async def tune(self, node: str, mode: str, actor: str, index: int | None = None, custom: str = "") -> dict:
        bridge, layout = self._bridge(node)
        mode = mode.strip().upper()
        if mode not in self._modes(bridge, layout):
            raise DVSwitchError(f"{mode or 'that mode'} is not set up for node {node}")
        if index is not None:
            rows = layout.talkgroups.get(mode, [])
            if not 0 <= index < len(rows):
                raise DVSwitchError("that talkgroup is no longer in the list; reopen DVSwitch")
            row = rows[index]
            target, label = row.target, row.alias
        else:
            target = custom.strip()
            if not CUSTOM_TARGET.fullmatch(target) or "@" in target:
                raise DVSwitchError("type a talkgroup, reflector, or host:port (letters, numbers, # * . : / ! - and spaces)")
            label = target
        current = self.status(node)
        if self.demo:
            self._demo_state[node] = {"mode": mode, "tune": target, "time": time.time()}
            log.info("dvswitch: %s tuned node %s to %s (%s)", actor, node, label, mode)
            return self.status(node)
        if current.get("online") and current.get("mode") and current["mode"] != mode:
            await self.set_mode(node, mode, actor)
        log.info("dvswitch: %s tuned node %s to %s (%s)", actor, node, label, mode)
        await self._send(bridge, [f"txTg={target}", "info"])
        return self.status(node)

    async def _send(self, bridge: Bridge, commands: list[str]) -> None:
        path = self._abinfo_path(bridge)
        if path is None:
            raise DVSwitchError("no /tmp/ABInfo_*.json found; is Analog_Bridge running?")
        try:
            tlv = read_abinfo(path).get("tlv") or {}
            port = int(tlv["rx_port"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DVSwitchError(f"{path} has no tlv rx_port") from exc
        packets = [packet(command) for command in commands]
        if self._lock.locked():
            raise DVSwitchError("another DVSwitch change is in progress; try again in a moment")
        async with self._lock:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                for data in packets:
                    sock.sendto(data, ("127.0.0.1", port))
                    await asyncio.sleep(STEP_DELAY)
            except OSError as exc:
                raise DVSwitchError(f"could not reach Analog_Bridge on port {port}: {exc.strerror}") from exc
            finally:
                sock.close()
            await asyncio.sleep(SETTLE_DELAY)
