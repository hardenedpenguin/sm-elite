"""AllStar node directory.

The public file is ``node|call|description|location``. A private overlay
uses the same columns and wins on duplicate node numbers, which is how
AllScan's asdb.txt replaces directory text for private nodes.
"""

from __future__ import annotations

import time
from pathlib import Path


def parse_directory(text: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        node = parts[0].strip()
        if not node:
            continue
        found[node] = {
            "call": parts[1].strip(),
            "desc": parts[2].strip(),
            "location": parts[3].strip(),
        }
    return found


def search_directory(
    rows: dict[str, dict[str, str]],
    query: str,
    limit: int = 25,
) -> tuple[list[dict[str, str]], int]:
    """Match every word of the query against node, call, description, and location.

    Ranked: exact node, exact callsign, node or callsign prefix, then the rest
    by node number. Returns the page of matches and the total match count.
    """
    words = query.lower().split()
    if not words:
        return [], 0
    ranked: list[tuple[int, int, str]] = []
    for node, row in rows.items():
        call = row.get("call", "").lower()
        text = " ".join((node, call, row.get("desc", ""), row.get("location", ""))).lower()
        if not all(word in text for word in words):
            continue
        first = words[0]
        if node == first:
            rank = 0
        elif call == first:
            rank = 1
        elif node.startswith(first) or call.startswith(first):
            rank = 2
        else:
            rank = 3
        ranked.append((rank, int(node) if node.isdigit() else 10**9, node))
    ranked.sort()
    page = [{"node": node, **rows[node]} for _rank, _num, node in ranked[: max(1, limit)]]
    return page, len(ranked)


class NodeDirectory:
    def __init__(self, public_file: Path, private_file: Path, url: str, refresh_minutes: int):
        self.public_file = public_file
        self.private_file = private_file
        self.url = url
        self.refresh_seconds = max(60, refresh_minutes * 60)
        self.rows: dict[str, dict[str, str]] = {}
        self.loaded_at = 0.0

    def load_files(self) -> None:
        rows: dict[str, dict[str, str]] = {}
        if self.public_file.is_file():
            rows.update(parse_directory(self.public_file.read_text(encoding="utf-8", errors="replace")))
        if self.private_file.is_file():
            rows.update(parse_directory(self.private_file.read_text(encoding="utf-8", errors="replace")))
        self.rows = rows
        self.loaded_at = time.time()

    def needs_refresh(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.loaded_at) >= self.refresh_seconds

    async def refresh(self, session) -> None:
        """Download the public directory, then re-read the private overlay."""
        if not self.url:
            self.load_files()
            return
        try:
            async with session.get(self.url, timeout=30) as response:
                if response.status != 200:
                    self.load_files()
                    return
                text = await response.text()
        except OSError:
            self.load_files()
            return
        self.public_file.parent.mkdir(parents=True, exist_ok=True)
        self.public_file.write_text(text, encoding="utf-8")
        self.load_files()
