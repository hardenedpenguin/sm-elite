"""Last-heard log: who keyed up, on which node, and for how long.

app_rpt reports keying for the local receiver (RPT_RXKEYED) and for each
direct connection (SawStat PTT). A talker several hops away appears as the
direct link their audio arrived on; AMI does not identify them further.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import SQLiteStore

LOCAL = "local"
LOCAL_DESC = "Local radio"
MIN_SECONDS = 0.5
MAX_TIMER = 3600


@dataclass
class Keyup:
    node: str
    source: str
    desc: str
    started: float
    ended: float | None = None
    id: int | None = None
    seen: float = 0.0

    def as_dict(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        end = self.ended if self.ended is not None else now
        return {
            "id": self.id,
            "node": self.node,
            "source": self.source,
            "desc": self.desc,
            "kind": "local" if self.source == LOCAL else "link",
            "started": round(self.started, 1),
            "ended": None if self.ended is None else round(self.ended, 1),
            "seconds": round(max(0.0, end - self.started), 1),
            "live": self.ended is None,
        }


def _timer(value: Any) -> float | None:
    if isinstance(value, (int, float)) and 0 <= value < MAX_TIMER:
        return float(value)
    return None


class KeyTracker:
    """Turn a stream of snapshots into finished key-ups."""

    def __init__(self) -> None:
        self.open: dict[tuple[str, str], Keyup] = {}

    def live(self, node: str) -> list[Keyup]:
        return [keyup for (owner, _source), keyup in self.open.items() if owner == node]

    def observe(self, snapshot: dict[str, Any], now: float | None = None) -> tuple[list[Keyup], bool]:
        """Return key-ups that just ended, and whether the live set changed."""
        now = time.time() if now is None else now
        node = str(snapshot["node"])
        conns = {str(conn.get("node")): conn for conn in snapshot.get("conns") or []}
        keyed: dict[str, tuple[str, float]] = {}
        if snapshot.get("online"):
            if snapshot.get("rx_keyed"):
                keyed[LOCAL] = (LOCAL_DESC, now)
            for source, conn in conns.items():
                if not conn.get("ptt"):
                    continue
                since = _timer(conn.get("since_key"))
                keyed[source] = (conn.get("desc") or "", now - since if since is not None else now)

        finished: list[Keyup] = []
        changed = False
        for key, keyup in list(self.open.items()):
            owner, source = key
            if owner != node or source in keyed:
                continue
            since = _timer((conns.get(source) or {}).get("since_unkey"))
            end = now - since if since is not None else now
            keyup.ended = max(keyup.started, keyup.seen, end)
            del self.open[key]
            changed = True
            if keyup.ended - keyup.started >= MIN_SECONDS:
                finished.append(keyup)
        for source, (desc, start) in keyed.items():
            if (node, source) in self.open:
                self.open[(node, source)].seen = now
                continue
            self.open[(node, source)] = Keyup(node=node, source=source, desc=desc, started=min(start, now), seen=now)
            changed = True
        return finished, changed


class HistoryStore(SQLiteStore):
    tables = ("heard",)

    def __init__(self, path: Path, keep_days: int = 30):
        self.keep_days = keep_days
        super().__init__(path)

    def _init(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS heard (
                id INTEGER PRIMARY KEY,
                node TEXT NOT NULL,
                source TEXT NOT NULL,
                desc TEXT NOT NULL DEFAULT '',
                started REAL NOT NULL,
                ended REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS heard_node_started ON heard (node, started DESC);
            """
        )
        self._db.commit()

    def add(self, keyup: Keyup) -> Keyup:
        if keyup.ended is None:
            raise ValueError("only finished key-ups are stored")
        cur = self._db.execute(
            "INSERT INTO heard (node, source, desc, started, ended) VALUES (?, ?, ?, ?, ?)",
            (keyup.node, keyup.source, keyup.desc, keyup.started, keyup.ended),
        )
        self._db.commit()
        keyup.id = cur.lastrowid
        return keyup

    def recent(self, nodes: list[str], limit: int = 50) -> list[dict[str, Any]]:
        if not nodes:
            return []
        marks = ",".join("?" for _ in nodes)
        rows = self._db.execute(
            f"SELECT * FROM heard WHERE node IN ({marks}) ORDER BY started DESC LIMIT ?",
            (*nodes, max(1, min(limit, 500))),
        ).fetchall()
        return [
            Keyup(
                node=row["node"],
                source=row["source"],
                desc=row["desc"],
                started=row["started"],
                ended=row["ended"],
                id=row["id"],
            ).as_dict()
            for row in rows
        ]

    def prune(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cur = self._db.execute("DELETE FROM heard WHERE started < ?", (now - self.keep_days * 86400,))
        self._db.commit()
        return cur.rowcount
