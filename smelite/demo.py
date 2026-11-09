"""Synthetic node, voter, and stats data for the dashboard without Asterisk."""

from __future__ import annotations

import time
from typing import Any

from .stats import Activity

DEMO_NODES = ("1998", "1999")

DEMO_DIRECTORY = {
    "1998": {"call": "N0CALL", "desc": "SM-Elite demo repeater", "location": "Local"},
    "1999": {"call": "N0CAL", "desc": "Second demo node", "location": "Local"},
    "27339": {"call": "W2CRA", "desc": "East Coast Reflector", "location": "New York"},
    "28404": {"call": "K6RRR", "desc": "San Diego hangout", "location": "California"},
    "512511": {"call": "GB3CR", "desc": "North Wales", "location": "United Kingdom"},
    "3000001": {"call": "W1ABC-L", "desc": "EchoLink conference", "location": ""},
}


def _phase(now: float) -> int:
    return int(now) % 24


def snapshot(node: str, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    phase = _phase(now)
    if node == "1999":
        return {
            "node": "1999",
            "desc": "N0CAL Second demo node Local",
            "rx_keyed": False,
            "tx_keyed": False,
            "tx_extra": False,
            "remote_keyed": False,
            "remote_keyed_node": None,
            "uptime": 3600 + int(now) % 80000,
            "vars": {"cpu_temp": "46.2", "WX": "68F", "DISK": "41%"},
            "conns": [],
            "direct": 0,
            "linked": 0,
            "online": True,
            "error": None,
        }
    remote_keyed = 6 <= phase < 12 or 18 <= phase < 24
    local = phase < 6
    telemetry = 12 <= phase < 18
    conns = [
        {
            "node": "27339",
            "desc": "W2CRA East Coast Reflector New York",
            "ip": "192.0.2.10",
            "reconnects": 0,
            "direction": "OUT",
            "elapsed": "01:12:04",
            "state": "ESTABLISHED",
            "mode": "Transceive",
            "ptt": remote_keyed,
            "since_key": 2 if remote_keyed else 40,
            "since_unkey": 8 if not remote_keyed else 2,
        },
        {
            "node": "28404",
            "desc": "K6RRR San Diego hangout California",
            "ip": "192.0.2.20",
            "reconnects": 1,
            "direction": "IN",
            "elapsed": "00:08:19",
            "state": "ESTABLISHED",
            "mode": "Monitor",
            "ptt": False,
            "since_key": 600,
            "since_unkey": 180,
        },
        {
            "node": "3000001",
            "desc": "W1ABC-L EchoLink",
            "ip": None,
            "reconnects": 0,
            "direction": "IN",
            "elapsed": "00:02:11",
            "state": "ESTABLISHED",
            "mode": "Transceive",
            "ptt": False,
            "since_key": -1,
            "since_unkey": -1,
        },
    ]
    return {
        "node": "1998",
        "desc": "N0CALL SM-Elite demo repeater Local",
        "rx_keyed": local,
        "tx_keyed": local or remote_keyed or telemetry,
        "tx_extra": telemetry,
        "remote_keyed": remote_keyed,
        "remote_keyed_node": "27339" if remote_keyed else None,
        "uptime": 86400 + int(now) % 40000,
        "vars": {"cpu_temp": "51.4", "cpu_load": "0.18", "WX": "72F", "ALERT": "None", "DISK": "38%"},
        "conns": conns,
        "direct": len(conns),
        "linked": 7,
        "online": True,
        "error": None,
    }


def voter(node: str, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    voted = "North" if int(now) % 10 < 6 else "South"
    clients = [
        {"name": "North", "rssi": 210 if voted == "North" else 120},
        {"name": "South", "rssi": 200 if voted == "South" else 90},
        {"name": "Mix", "rssi": 40},
        {"name": "West Inactive", "rssi": 0},
    ]
    for client in clients:
        rssi = client["rssi"]
        client["percent"] = 100 if rssi == 255 else max(0, min(100, int(rssi * 0.35 + 10)))
        name = client["name"].lower()
        if client["name"] == voted:
            client["role"] = "voted"
        elif "mix" in name:
            client["role"] = "mix"
        elif "inactive" in name:
            client["role"] = "inactive"
        else:
            client["role"] = "normal"
    return {"node": node, "voted": voted, "clients": clients}


def stats_for(node: str, now: float | None = None) -> Activity:
    now = time.time() if now is None else now
    table = {
        "27339": Activity("27339", tx_pct=72 if int(now) % 24 < 12 else 18, busy_pct=14, links=42, active=True, web=True, keyed=int(now) % 24 < 12, updated=now),
        "28404": Activity("28404", tx_pct=8, busy_pct=6, links=9, active=True, web=False, keyed=False, updated=now),
        "512511": Activity("512511", tx_pct=0, busy_pct=3, links=4, active=True, web=False, keyed=False, updated=now),
        "42235": Activity("42235", tx_pct=0, busy_pct=None, links=0, active=False, web=False, keyed=False, updated=now),
        "1998": Activity("1998", tx_pct=20, busy_pct=4, links=3, active=True, web=False, keyed=False, updated=now),
        "1999": Activity("1999", tx_pct=0, busy_pct=1, links=0, active=True, web=False, keyed=False, updated=now),
    }
    return table.get(node, Activity(node, active=False, error="no demo stats", updated=now))


def command_result(command: str) -> str:
    return f"OK: demo accepted `{command}`"
