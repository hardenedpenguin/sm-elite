"""Parse app_rpt AMI text into JSON snapshots.

XStat connection lines are fixed-width, from rpt_manager.c:

    Conn: %-10s%-20s%-12d%-11s%-20s%-20s
          name, peer, reconnects, direction, elapsed, state

An empty peer collapses under a whitespace split, which is how EchoLink
lines show up as five fields. SawStat lines are:

    Conn: <node> <ptt> <seconds-since-key> <seconds-since-unkey>
"""

from __future__ import annotations

import html
import re
from typing import Any

_TAG = re.compile(r"<[^>]+>")

MODE_NAMES = {
    "T": "Transceive",
    "R": "Monitor",
    "C": "Connecting",
}

_CONN_PREFIX = "Conn:"


def plain_text(value: str) -> str:
    """Drop markup that node-status scripts sometimes store in channel variables."""
    text = _TAG.sub(" ", value)
    return " ".join(html.unescape(text).split())


_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TEXT_COLOR = re.compile(r"(?<![\w-])color\s*:\s*([^;'\"]+)", re.IGNORECASE)
_SAFE_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})$")
_TEMP = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?\b", re.IGNORECASE)


def styled_lines(value: str) -> list[dict[str, str]]:
    """Split a status variable on <br> into text lines, keeping a safe text color."""
    lines = []
    for segment in _BREAK.split(value):
        text = plain_text(segment)
        if not text:
            continue
        line = {"text": text}
        match = _TEXT_COLOR.search(segment)
        if match and _SAFE_COLOR.match(match.group(1).strip()):
            line["color"] = match.group(1).strip()
        lines.append(line)
    return lines


def temperature(value: str) -> dict[str, str] | None:
    """CPU temperature with the same thresholds supermon-ng's status script uses."""
    text = plain_text(value)
    match = _TEMP.search(text)
    if not match:
        return {"text": text, "level": "unknown"} if text else None
    reading = float(match.group(1))
    unit = (match.group(2) or "C").upper()
    ok, warm = (140, 158) if unit == "F" else (50, 60)
    level = "ok" if reading <= ok else ("warm" if reading <= warm else "hot")
    return {"text": text, "level": level}


def node_status(variables: dict[str, str]) -> dict[str, Any]:
    """The supermon-style status block: alerts, weather, CPU, load, disk."""
    status: dict[str, Any] = {}
    if variables.get("ALERT"):
        status["alert"] = styled_lines(variables["ALERT"])
    if variables.get("WX"):
        status["wx"] = plain_text(variables["WX"])
    if variables.get("cpu_temp"):
        status["temp"] = temperature(variables["cpu_temp"])
    for key, name in (("cpu_up", "uptime"), ("cpu_load", "load"), ("DISK", "disk")):
        if variables.get(key):
            status[name] = plain_text(variables[key])
    return status


def ami_lines(raw: str) -> list[str]:
    """Normalize an AMI packet into content lines.

    ASL3 wraps command output as ``Output:`` lines. Classic manager
    responses put the same text in the body. Either form is accepted.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("output:"):
            line = line.split(":", 1)[1].strip()
        if line:
            lines.append(line)
    return lines


def _parse_conn_body(body: str) -> dict[str, Any] | None:
    """Parse the text after ``Conn:`` on an XStat line."""
    if len(body) >= 73 and not body[:10].strip() == "":
        name = body[0:10].strip()
        peer = body[10:30].strip()
        reconnects = body[30:42].strip()
        direction = body[42:53].strip()
        elapsed = body[53:73].strip()
        state = body[73:].strip()
        if name and direction in {"IN", "OUT"}:
            return {
                "node": name,
                "ip": peer or None,
                "reconnects": _int_or_none(reconnects),
                "direction": direction,
                "elapsed": elapsed,
                "state": state or "UNKNOWN",
            }

    parts = body.split()
    if len(parts) >= 6:
        return {
            "node": parts[0],
            "ip": parts[1] or None,
            "reconnects": _int_or_none(parts[2]),
            "direction": parts[3],
            "elapsed": parts[4],
            "state": parts[5],
        }
    if len(parts) == 5:
        return {
            "node": parts[0],
            "ip": None,
            "reconnects": _int_or_none(parts[1]),
            "direction": parts[2],
            "elapsed": parts[3],
            "state": parts[4],
        }
    return None


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_xstat(raw: str) -> dict[str, Any]:
    """Return vars, direct connections, and linked-node modes."""
    variables: dict[str, str] = {}
    connections: dict[str, dict[str, Any]] = {}
    modes: dict[str, str] = {}
    linked: list[str] = []

    for line in ami_lines(raw):
        if line.startswith("Var:") and "=" in line:
            key, value = line[4:].split("=", 1)
            variables[key.strip()] = value.strip()
            continue
        if line.startswith(_CONN_PREFIX):
            # "Conn:" is five characters. The C format inserts one space
            # before the fixed-width columns, so drop that space only.
            body = line[5:]
            if body.startswith(" "):
                body = body[1:]
            parsed = _parse_conn_body(body)
            if parsed and parsed["node"] and parsed["node"] != "0":
                connections[parsed["node"]] = parsed
            continue
        if line.startswith("LinkedNodes:"):
            payload = line.split(":", 1)[1].strip()
            if not payload or payload == "<NONE>":
                continue
            for item in payload.split(","):
                token = item.strip()
                if len(token) < 2 or not token[0].isalpha():
                    continue
                node = token[1:]
                modes[node] = token[0]
                linked.append(node)

    for node, mode in modes.items():
        if node in connections:
            connections[node]["mode"] = MODE_NAMES.get(mode, "Unknown")

    for conn in connections.values():
        conn.setdefault("mode", "Transceive")

    return {
        "vars": variables,
        "connections": connections,
        "linked": linked,
    }


def parse_sawstat(raw: str) -> dict[str, dict[str, Any]]:
    """Map node id to PTT and key timers."""
    found: dict[str, dict[str, Any]] = {}
    for line in ami_lines(raw):
        if not line.startswith(_CONN_PREFIX):
            continue
        parts = line[5:].split()
        if len(parts) < 4:
            continue
        found[parts[0]] = {
            "ptt": parts[1] == "1",
            "since_key": _int_or_none(parts[2]) if parts[2] != "" else -1,
            "since_unkey": _int_or_none(parts[3]) if parts[3] != "" else -1,
        }
    return found


def parse_voter(raw: str) -> dict[str, Any]:
    """VoterStatus as data, not HTML.

    RSSI 255 is full quieting. Other values use the same bar scale operators
    already know: ``rssi * 0.35 + 10``.
    """
    voted: str | None = None
    clients: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in ami_lines(raw):
        if line.startswith("Client"):
            name = line.split(None, 1)[1].strip() if " " in line else ""
            current = {"name": name, "rssi": 0}
            clients.append(current)
        elif line.startswith("RSSI") and current is not None:
            rssi = _int_or_none(line.split(":", 1)[1].strip()) or 0
            current["rssi"] = rssi
        elif line.lower().startswith("voted"):
            voted = line.split(":", 1)[1].strip() or None
    for client in clients:
        rssi = int(client["rssi"])
        percent = 100 if rssi == 255 else max(0, min(100, int(rssi * 0.35 + 10)))
        name = str(client["name"])
        lowered = name.lower()
        if voted and name == voted:
            role = "voted"
        elif "mix" in lowered:
            role = "mix"
        elif "inactive" in lowered:
            role = "inactive"
        else:
            role = "normal"
        client["percent"] = percent
        client["role"] = role
    return {"voted": voted, "clients": clients}


def describe_node(
    node: str,
    database: dict[str, dict[str, str]] | None = None,
    overrides: dict[str, str] | None = None,
    echolink: dict[str, str] | None = None,
) -> str:
    """Human label for a node number, following the usual AllStar rules."""
    database = database or {}
    overrides = overrides or {}
    echolink = echolink or {}
    if node in overrides and overrides[node].strip():
        return overrides[node].strip()
    row = database.get(node)
    if row:
        return " ".join(
            part for part in (row.get("call", ""), row.get("desc", ""), row.get("location", "")) if part
        ).strip()
    if re.fullmatch(r"3\d{6}", node):
        return echolink.get(node) or "EchoLink"
    if node.endswith("-P"):
        return "AllStar telephone portal"
    if node[:1].isalpha():
        return "Direct client"
    if node.isdigit() and int(node) < 2000:
        return "Private"
    return "Private or unavailable"


def build_snapshot(
    node: str,
    xstat_raw: str,
    saw_raw: str,
    *,
    desc: str,
    database: dict[str, dict[str, str]] | None = None,
    overrides: dict[str, str] | None = None,
    echolink: dict[str, str] | None = None,
    uptime: int | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """One full node snapshot. Clients replace card state from this object."""
    parsed = parse_xstat(xstat_raw)
    saw = parse_sawstat(saw_raw)
    remote_node = None
    conns: list[dict[str, Any]] = []
    for node_id, conn in parsed["connections"].items():
        timers = saw.get(node_id, {})
        ptt = bool(timers.get("ptt", False))
        if ptt and remote_node is None:
            remote_node = node_id
        conns.append({
            "node": node_id,
            "desc": describe_node(node_id, database, overrides, echolink),
            "ip": conn.get("ip"),
            "reconnects": conn.get("reconnects"),
            "direction": conn.get("direction") or "",
            "elapsed": conn.get("elapsed") or "",
            "state": conn.get("state") or "",
            "mode": conn.get("mode") or "",
            "ptt": ptt,
            "since_key": timers.get("since_key", -1),
            "since_unkey": timers.get("since_unkey", -1),
        })

    def sort_key(row: dict[str, Any]) -> tuple:
        since = row.get("since_key")
        if since is None or since < 0:
            return (1, 0, row["node"])
        return (0, int(since), row["node"])

    conns.sort(key=sort_key)
    variables = parsed["vars"]
    interesting = {
        key: plain_text(variables[key])
        for key in ("cpu_temp", "cpu_up", "cpu_load", "ALERT", "WX", "DISK")
        if variables.get(key)
    }
    return {
        "node": str(node),
        "desc": desc,
        "rx_keyed": variables.get("RPT_RXKEYED") == "1",
        "tx_keyed": variables.get("RPT_TXKEYED") == "1",
        "tx_extra": variables.get("RPT_TXEKEYED") == "1",
        "remote_keyed": remote_node is not None,
        "remote_keyed_node": remote_node,
        "uptime": uptime,
        "vars": interesting,
        "status": node_status(variables),
        "conns": conns,
        "direct": len(conns),
        "linked": len(parsed["linked"]),
        "online": error is None,
        "error": error,
    }


def tx_state(snapshot: dict[str, Any]) -> str:
    """local, network, telemetry, playback, or idle."""
    if snapshot.get("rx_keyed"):
        return "local"
    if snapshot.get("remote_keyed") and snapshot.get("tx_keyed"):
        return "network"
    if snapshot.get("tx_keyed"):
        return "telemetry"
    if snapshot.get("remote_keyed"):
        return "playback"
    return "idle"
