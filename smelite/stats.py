"""AllStar Link stats cache.

AllScan colors a favorites list from stats.allstarlink.org, but each browser
polls on its own and the API allows about 30 requests a minute per address.
This cache does the scan once, paces requests, and keeps a short transmit
average so the page can paint activity without connecting to those nodes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Sample:
    node: str
    txtime: int = 0
    keyups: int = 0
    uptime: int = 0
    keyed: bool = False
    links: int = 0
    busy_pct: int | None = None
    active: bool = False
    web: bool = False
    at: float = 0.0
    error: str | None = None


@dataclass
class Activity:
    node: str
    tx_pct: int = 0
    busy_pct: int | None = None
    links: int = 0
    active: bool = False
    web: bool = False
    keyed: bool = False
    error: str | None = None
    updated: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "tx_pct": self.tx_pct,
            "busy_pct": self.busy_pct,
            "links": self.links,
            "active": self.active,
            "web": self.web,
            "keyed": self.keyed,
            "error": self.error,
            "updated": self.updated,
        }


@dataclass
class _Memory:
    txtime: int = 0
    keyups: int = 0
    at: float = 0.0
    average: int = 0


def fold_activity(previous: _Memory | None, sample: Sample, now: float) -> tuple[_Memory, int]:
    """Rolling transmit percent from keyed flag or txtime deltas.

    ``keyed`` is often stuck at 0 on the public stats API, so a rise in
    total transmit time between two samples is treated as activity. A jump
    larger than the elapsed clock is discarded as a counter reset.
    """
    if sample.keyed:
        memory = _Memory(sample.txtime, sample.keyups, now, 100)
        return memory, 100
    if previous is None or sample.txtime < previous.txtime or sample.keyups < previous.keyups:
        memory = _Memory(sample.txtime, sample.keyups, now, 0)
        return memory, 0
    elapsed = max(now - previous.at, 0.001)
    delta_tx = sample.txtime - previous.txtime
    delta_keys = sample.keyups - previous.keyups
    if delta_tx > 2 * elapsed or delta_keys > elapsed / 3:
        delta_tx = 0
    instant = int(min(100, round(100 * delta_tx / elapsed)))
    if previous.average + instant > 2:
        average = int(round(previous.average / 2 + instant / 2))
    else:
        average = 0
    return _Memory(sample.txtime, sample.keyups, now, average), average


def parse_stats_payload(node: str, payload: dict[str, Any], now: float) -> Sample:
    """Normalize the public stats JSON into one sample."""
    stats = payload.get("stats") or {}
    data = stats.get("data") or {}
    info = payload.get("node") or {}
    uptime = int(data.get("apprptuptime") or 0)
    txtime = int(data.get("totaltxtime") or 0)
    links = data.get("links") or []
    busy = None
    if uptime > 60 and txtime > 10:
        busy = int(round(100 * txtime / uptime))
    status = str(info.get("Status") or "")
    return Sample(
        node=str(stats.get("node") or node),
        txtime=txtime,
        keyups=int(data.get("totalkeyups") or 0),
        uptime=uptime,
        keyed=bool(data.get("keyed")),
        links=len(links) if isinstance(links, list) else int(links or 0),
        busy_pct=busy,
        active=status.lower() == "active",
        web=bool(info.get("access_webtransceiver")),
        at=now,
    )


@dataclass
class StatsCache:
    """Remember the latest sample and the smoothed transmit percent."""

    memory: dict[str, _Memory] = field(default_factory=dict)
    latest: dict[str, Activity] = field(default_factory=dict)

    def observe(self, sample: Sample, now: float | None = None) -> Activity:
        now = time.time() if now is None else now
        if sample.error:
            activity = Activity(node=sample.node, error=sample.error, updated=now)
            self.latest[sample.node] = activity
            return activity
        previous = self.memory.get(sample.node)
        memory, tx_pct = fold_activity(previous, sample, now)
        self.memory[sample.node] = memory
        activity = Activity(
            node=sample.node,
            tx_pct=tx_pct,
            busy_pct=sample.busy_pct,
            links=sample.links,
            active=sample.active,
            web=sample.web,
            keyed=sample.keyed or tx_pct > 5,
            updated=now,
        )
        self.latest[sample.node] = activity
        return activity

    def get(self, node: str) -> Activity | None:
        return self.latest.get(node)
