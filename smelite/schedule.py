"""Scheduled node actions: link at net time, drop afterwards, play an announcement.

Jobs repeat weekly on the chosen days at a time on the node's own clock. A
job runs with the permissions its owner has at run time, so removing or
restricting the owner stops their jobs.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .commands import NODE_RE

ACTIONS = {
    "connect": "Connect",
    "monitor": "Monitor",
    "local_monitor": "Local monitor",
    "disconnect": "Disconnect",
    "disconnect_all": "Disconnect all",
    "play": "Play sound",
    "command": "Command",
}
LINK_ACTIONS = {"connect", "monitor", "local_monitor", "disconnect"}
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
TIME_RE = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
MAX_JOBS = 100
CATCH_UP_MINUTES = 5


@dataclass
class Job:
    node: str
    action: str
    at: str
    days: list[int]
    owner: str
    target: str = ""
    permanent: bool = False
    sound: str = ""
    scope: str = "local"
    template: str = ""
    note: str = ""
    enabled: bool = True
    id: int | None = None
    created: float = field(default_factory=time.time)
    last_run: float | None = None
    last_result: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node": self.node,
            "action": self.action,
            "action_label": ACTIONS.get(self.action, self.action),
            "at": self.at,
            "days": self.days,
            "days_label": days_label(self.days),
            "owner": self.owner,
            "target": self.target,
            "permanent": self.permanent,
            "sound": self.sound,
            "scope": self.scope,
            "template": self.template,
            "note": self.note,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "last_result": self.last_result,
        }

    def runs_at(self, moment: datetime) -> bool:
        return self.enabled and moment.weekday() in self.days and moment.strftime("%H:%M") == self.at


def days_label(days: list[int]) -> str:
    if sorted(days) == list(range(7)):
        return "Every day"
    if sorted(days) == list(range(5)):
        return "Weekdays"
    if sorted(days) == [5, 6]:
        return "Weekends"
    return " ".join(DAY_NAMES[day] for day in sorted(days))


def validate(job: Job, *, nodes: list[str], commands: set[str], sounds: set[str]) -> Job:
    """Clean a job from the API, raising ValueError with a message the page can show."""
    if job.node not in nodes:
        raise ValueError("pick one of this server's nodes")
    if job.action not in ACTIONS:
        raise ValueError("pick an action")
    if not TIME_RE.fullmatch(job.at):
        raise ValueError("pick a time")
    days = sorted({int(day) for day in job.days if isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6})
    if not days:
        raise ValueError("pick at least one day")
    job.days = days
    job.target = job.target.strip()
    if job.action in LINK_ACTIONS:
        if not NODE_RE.fullmatch(job.target):
            raise ValueError("the target must be a node number")
        if job.target == job.node:
            raise ValueError("a node cannot link to itself")
    elif job.action == "command":
        if job.template not in commands:
            raise ValueError("pick a command from commands.ini")
        if "%" in job.template and not NODE_RE.fullmatch(job.target):
            raise ValueError("this command needs a target node number")
        if "%" not in job.template:
            job.target = ""
    else:
        job.target = ""
    if job.action == "play":
        if job.sound not in sounds:
            raise ValueError("pick a saved sound")
        if job.scope not in {"local", "links"}:
            raise ValueError("pick where to play it")
    else:
        job.sound = ""
        job.scope = "local"
    if job.action != "command":
        job.template = ""
    job.permanent = bool(job.permanent) and job.action in {"connect", "monitor", "local_monitor"}
    job.note = " ".join(job.note.split())[:80]
    return job


def due_minutes(last: datetime, now: datetime) -> list[datetime]:
    """Whole minutes after ``last`` up to ``now``, at most a few, oldest first."""
    start = last.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = now.replace(second=0, microsecond=0)
    found = []
    moment = max(start, end - timedelta(minutes=CATCH_UP_MINUTES - 1))
    while moment <= end:
        found.append(moment)
        moment += timedelta(minutes=1)
    return found


class ScheduleStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                node TEXT NOT NULL,
                action TEXT NOT NULL,
                at TEXT NOT NULL,
                days TEXT NOT NULL,
                owner TEXT NOT NULL,
                target TEXT NOT NULL DEFAULT '',
                permanent INTEGER NOT NULL DEFAULT 0,
                sound TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT 'local',
                template TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created REAL NOT NULL,
                last_run REAL,
                last_result TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def _job(self, row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            node=row["node"],
            action=row["action"],
            at=row["at"],
            days=[int(day) for day in row["days"].split(",") if day],
            owner=row["owner"],
            target=row["target"],
            permanent=bool(row["permanent"]),
            sound=row["sound"],
            scope=row["scope"],
            template=row["template"],
            note=row["note"],
            enabled=bool(row["enabled"]),
            created=row["created"],
            last_run=row["last_run"],
            last_result=row["last_result"],
        )

    def jobs(self) -> list[Job]:
        rows = self._db.execute("SELECT * FROM jobs ORDER BY at, node, id").fetchall()
        return [self._job(row) for row in rows]

    def get(self, job_id: int) -> Job | None:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return None if row is None else self._job(row)

    def add(self, job: Job) -> Job:
        count = self._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        if count >= MAX_JOBS:
            raise ValueError(f"there are already {MAX_JOBS} scheduled actions")
        cur = self._db.execute(
            """
            INSERT INTO jobs (node, action, at, days, owner, target, permanent, sound, scope, template, note, enabled, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.node, job.action, job.at, ",".join(map(str, job.days)), job.owner, job.target,
                int(job.permanent), job.sound, job.scope, job.template, job.note, int(job.enabled), job.created,
            ),
        )
        self._db.commit()
        job.id = cur.lastrowid
        return job

    def set_enabled(self, job_id: int, enabled: bool) -> None:
        self._db.execute("UPDATE jobs SET enabled = ? WHERE id = ?", (int(enabled), job_id))
        self._db.commit()

    def delete(self, job_id: int) -> None:
        self._db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._db.commit()

    def finished(self, job_id: int, when: float, result: str) -> None:
        self._db.execute("UPDATE jobs SET last_run = ?, last_result = ? WHERE id = ?", (when, result[:200], job_id))
        self._db.commit()
