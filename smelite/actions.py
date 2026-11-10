"""Action log: who changed what on the node, and when.

Every control action, admin change, and login is one row. Only admins can
read it. Passwords and file contents are never stored.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

MAX_FIELD = 200


def _clip(value: Any, limit: int = MAX_FIELD) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ActionLog:
    def __init__(self, path: Path, keep_days: int = 90):
        self.keep_days = keep_days
        self._pruned_at = 0.0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY,
                at REAL NOT NULL,
                user TEXT NOT NULL,
                peer TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                node TEXT NOT NULL DEFAULT '',
                target TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS actions_at ON actions (at DESC);
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def add(
        self,
        user: str,
        action: str,
        *,
        node: str = "",
        target: str = "",
        detail: str = "",
        peer: str = "",
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        self._db.execute(
            "INSERT INTO actions (at, user, peer, action, node, target, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now, _clip(user, 64), _clip(peer, 64), _clip(action, 64), _clip(node, 32), _clip(target, 64), _clip(detail)),
        )
        self._db.commit()
        self.prune(now)

    def recent(self, limit: int = 200, before: float | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        if before is None:
            rows = self._db.execute("SELECT * FROM actions ORDER BY at DESC, id DESC LIMIT ?", (limit,))
        else:
            rows = self._db.execute(
                "SELECT * FROM actions WHERE at < ? ORDER BY at DESC, id DESC LIMIT ?", (before, limit),
            )
        return [
            {
                "id": row["id"],
                "at": round(row["at"], 1),
                "user": row["user"],
                "peer": row["peer"],
                "action": row["action"],
                "node": row["node"],
                "target": row["target"],
                "detail": row["detail"],
            }
            for row in rows.fetchall()
        ]

    def prune(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        if now - self._pruned_at < 3600:
            return 0
        self._pruned_at = now
        cur = self._db.execute("DELETE FROM actions WHERE at < ?", (now - self.keep_days * 86400,))
        self._db.commit()
        return cur.rowcount
