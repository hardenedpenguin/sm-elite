"""Poll AMI, keep the last-heard log, and scan favorites stats once.

Configured nodes are polled continuously while the last-heard log is on, so
key-ups are recorded with no browser open. With the log off, AMI is only
polled while someone is watching.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from datetime import datetime
from typing import Any

import aiohttp

from . import access, demo
from .actions import ActionLog
from .commands import action_command, check_command
from .schedule import ACTIONS, Job, ScheduleStore, due_minutes
from .admin import ASTERISK_ACTIONS, AdminTools
from .dvswitch import DVSwitch
from .sounds import SCOPES, Sounds
from .ami import AmiError, AmiPool
from .astdb import NodeDirectory
from .auth import AuthStore
from .config import Settings
from .favorites import load_file
from .history import HistoryStore, KeyTracker
from .parser import build_snapshot, describe_node, node_status, parse_voter, tx_state
from .stats import Sample, StatsCache, parse_stats_payload

log = logging.getLogger("smelite")

_UPTIME_RE = re.compile(r"(\d+)")


class Hub:
    """One websocket fan-out. Status is public; commands are not on this socket."""

    def __init__(self):
        self.clients: dict[Any, set[str]] = {}
        self.snapshots: dict[str, dict] = {}
        self.voters: dict[str, dict] = {}
        self.activities: dict[str, dict] = {}
        self.live_heard: dict[str, list[dict]] = {}

    def add(self, ws: Any) -> None:
        self.clients[ws] = set()

    def remove(self, ws: Any) -> None:
        self.clients.pop(ws, None)

    def subscribe(self, ws: Any, nodes: set[str]) -> None:
        if ws in self.clients:
            self.clients[ws] = nodes

    def wanted(self) -> set[str]:
        found: set[str] = set()
        for nodes in self.clients.values():
            found |= nodes
        return found

    async def _send(self, ws: Any, payload: dict) -> bool:
        try:
            await ws.send_json(payload)
            return True
        except (ConnectionError, RuntimeError, OSError):
            return False

    async def publish(self, payload: dict, only: str | None = None) -> None:
        dead = []
        for ws, nodes in list(self.clients.items()):
            if only is not None and only not in nodes:
                continue
            if not await self._send(ws, payload):
                dead.append(ws)
        for ws in dead:
            self.clients.pop(ws, None)

    async def publish_node(self, snapshot: dict) -> None:
        self.snapshots[snapshot["node"]] = snapshot
        await self.publish({"type": "snapshot", "data": snapshot}, only=snapshot["node"])

    async def publish_voter(self, node: str, payload: dict) -> None:
        self.voters[node] = payload
        await self.publish({"type": "voter", "node": node, "data": payload}, only=node)

    async def publish_stats(self, payload: dict) -> None:
        self.activities[payload["node"]] = payload
        await self.publish({"type": "stats", "data": payload})

    async def publish_heard(self, node: str, done: list[dict], live: list[dict]) -> None:
        self.live_heard[node] = live
        await self.publish({"type": "heard", "node": node, "done": done, "live": live}, only=node)

    async def replay(self, ws: Any) -> None:
        nodes = self.clients.get(ws, set())
        for node in nodes:
            if node in self.snapshots:
                await self._send(ws, {"type": "snapshot", "data": self.snapshots[node]})
            if self.live_heard.get(node):
                await self._send(ws, {"type": "heard", "node": node, "done": [], "live": self.live_heard[node]})
            if node in self.voters:
                await self._send(ws, {"type": "voter", "node": node, "data": self.voters[node]})
        for payload in self.activities.values():
            await self._send(ws, {"type": "stats", "data": payload})


def _offline(node: str, desc: str, message: str) -> dict:
    return {
        "node": node,
        "desc": desc,
        "rx_keyed": False,
        "tx_keyed": False,
        "tx_extra": False,
        "remote_keyed": False,
        "remote_keyed_node": None,
        "uptime": None,
        "vars": {},
        "status": {},
        "conns": [],
        "direct": 0,
        "linked": 0,
        "online": False,
        "error": message,
        "tx": "idle",
    }


def _parse_uptime(raw: str) -> int | None:
    for line in raw.replace("\r", "").split("\n"):
        if "uptime" not in line.lower():
            continue
        match = _UPTIME_RE.search(line)
        if match:
            return int(match.group(1))
    return None


class Engine:
    def __init__(
        self,
        settings: Settings,
        auth: AuthStore,
        directory: NodeDirectory,
        history: HistoryStore | None = None,
        actions: ActionLog | None = None,
        schedule: ScheduleStore | None = None,
    ):
        self.settings = settings
        self.auth = auth
        self.directory = directory
        self.history = history
        self.actions = actions
        self.schedule = schedule
        self.keys = KeyTracker()
        self._pruned_at = 0.0
        self.admin = AdminTools(
            settings.admin_enabled or settings.demo,
            settings.admin_config_dir,
            settings.admin_log_file,
            settings.admin_helper,
            demo=settings.demo,
            smelite_dir=settings.config_dir,
        )
        self.sounds = Sounds(settings, self.admin.run_helper, demo=settings.demo)
        self.dvswitch = DVSwitch(settings.dvswitch_file, demo=settings.demo)
        self._demo_access: dict[tuple[str, str], dict[str, str]] = {}
        self.hub = Hub()
        self.pool = AmiPool()
        self.stats = StatsCache()
        self.http: aiohttp.ClientSession | None = None
        self.favorite_file = "favorites.ini"
        self.stop = asyncio.Event()
        self._uptime: dict[str, int | None] = {}
        self._ticks: dict[str, int] = {}
        self._echolink: dict[str, tuple[str, float]] = {}
        self._tasks: list[asyncio.Task] = []
        self.login_failures: dict[str, list[float]] = {}

    def describe(self, node: str) -> str:
        names = {key: value for key, (value, _stamp) in self._echolink.items()}
        rows = dict(self.directory.rows)
        if self.settings.demo:
            rows = {**demo.DEMO_DIRECTORY, **rows}
        return describe_node(node, rows, self.settings.overrides, names)

    async def start(self) -> None:
        timeout = aiohttp.ClientTimeout(total=20)
        self.http = aiohttp.ClientSession(timeout=timeout)
        if self.settings.demo:
            self.directory.rows.update(demo.DEMO_DIRECTORY)
        else:
            self.directory.load_files()
            try:
                await self.directory.refresh(self.http)
            except (aiohttp.ClientError, OSError) as exc:
                log.warning("node directory refresh failed: %s", exc)
        self._tasks = [
            asyncio.create_task(self._poll_loop(), name="poll"),
            asyncio.create_task(self._stats_loop(), name="stats"),
            asyncio.create_task(self._directory_loop(), name="directory"),
            asyncio.create_task(self._schedule_loop(), name="schedule"),
        ]

    async def close(self) -> None:
        self.stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.pool.close()
        if self.http is not None:
            await self.http.close()
        if self.history is not None:
            self.history.close()
        if self.actions is not None:
            self.actions.close()
        if self.schedule is not None:
            self.schedule.close()

    def record(self, user: str, action: str, **fields: str) -> None:
        if self.actions is None:
            return
        try:
            self.actions.add(user, action, **fields)
        except sqlite3.Error as exc:
            log.warning("action log: could not record %s: %s", action, exc)

    def _recording(self) -> bool:
        return self.history is not None and self.settings.history_enabled

    async def _poll_loop(self) -> None:
        while not self.stop.is_set():
            watched = self.hub.wanted()
            polled = set(watched)
            if self._recording():
                polled |= set(self.settings.monitored_ids())
            if not polled:
                await asyncio.sleep(1)
                continue
            interval = 1.0
            for node_id in sorted(polled):
                cfg = self.settings.node_by_id(node_id)
                if cfg is not None:
                    interval = min(interval, max(0.4, cfg.poll_interval))
                snapshot = await self._poll_node(node_id)
                await self.hub.publish_node(snapshot)
                await self._record(snapshot)
                if cfg is not None and node_id == cfg.node and node_id in watched:
                    for voter_node in cfg.voters:
                        payload = await self._poll_voter(voter_node)
                        await self.hub.publish_voter(voter_node, payload)
            self._prune()
            await asyncio.sleep(interval)

    async def _record(self, snapshot: dict) -> None:
        if not self._recording():
            return
        now = time.time()
        finished, changed = self.keys.observe(snapshot, now)
        if not changed:
            return
        done = [self.history.add(keyup).as_dict(now) for keyup in finished]
        live = [keyup.as_dict(now) for keyup in self.keys.live(snapshot["node"])]
        await self.hub.publish_heard(snapshot["node"], done, live)

    def _prune(self) -> None:
        now = time.time()
        if not self._recording() or now - self._pruned_at < 3600:
            return
        self._pruned_at = now
        removed = self.history.prune(now)
        if removed:
            log.info("last heard: removed %d entries older than %d days", removed, self.history.keep_days)

    def heard(self, nodes: list[str], limit: int) -> dict:
        if not self._recording():
            return {"enabled": False, "items": [], "live": []}
        now = time.time()
        live = [keyup.as_dict(now) for node in nodes for keyup in self.keys.live(node)]
        return {"enabled": True, "items": self.history.recent(nodes, limit), "live": live}

    async def _poll_node(self, node_id: str) -> dict:
        if self.settings.demo:
            snapshot = demo.snapshot(node_id)
            snapshot["tx"] = tx_state(snapshot)
            snapshot["status"] = node_status(snapshot["vars"])
            return snapshot
        cfg = self.settings.node_by_id(node_id)
        desc = self.describe(node_id)
        if cfg is None:
            return _offline(node_id, desc, "node is not configured")
        self._ticks[node_id] = self._ticks.get(node_id, 0) + 1
        tick = self._ticks[node_id]

        async def read(client):
            xstat = await client.rpt_status(node_id, "XStat")
            saw = await client.rpt_status(node_id, "SawStat")
            uptime = self._uptime.get(node_id)
            if tick % 10 == 1:
                uptime = _parse_uptime(await client.command("core show uptime seconds"))
                self._uptime[node_id] = uptime
            return xstat, saw, uptime

        try:
            xstat, saw, uptime = await self.pool.call(cfg.ami(), read)
        except AmiError as exc:
            log.warning("AMI poll failed for %s: %s", node_id, exc)
            return _offline(node_id, desc, "Asterisk manager is not answering")
        if "response: error" in xstat.lower() and "conn:" not in xstat.lower():
            return _offline(node_id, desc, "node did not return status")
        await self._remember_echolink(cfg, xstat)
        snapshot = build_snapshot(
            node_id,
            xstat,
            saw,
            desc=self.describe(node_id),
            database={**self.directory.rows},
            overrides=self.settings.overrides,
            echolink={key: value for key, (value, _stamp) in self._echolink.items()},
            uptime=uptime,
        )
        snapshot["tx"] = tx_state(snapshot)
        return snapshot

    async def _remember_echolink(self, cfg, xstat: str) -> None:
        now = time.time()
        needed = []
        for line in xstat.splitlines():
            match = re.search(r"\b(3\d{6})\b", line)
            if not match:
                continue
            node = match.group(1)
            cached = self._echolink.get(node)
            if cached and now - cached[1] < 300:
                continue
            needed.append(node)
        for node in needed[:3]:
            el_id = node[-6:].lstrip("0") or "0"

            async def lookup(client, el_id=el_id):
                return await client.command(f"echolink dbget nodename {el_id}")

            try:
                raw = await self.pool.call(cfg.ami(), lookup)
            except AmiError:
                return
            name = "EchoLink"
            for row in raw.splitlines():
                if "|" in row:
                    parts = row.split("|")
                    if len(parts) > 1 and parts[1].strip():
                        name = parts[1].strip() + " · EchoLink"
                        break
            self._echolink[node] = (name, now)

    async def _poll_voter(self, node_id: str) -> dict:
        if self.settings.demo:
            return demo.voter(node_id)
        cfg = self.settings.node_by_id(node_id) or next(
            (item for item in self.settings.nodes if node_id in item.voters),
            None,
        )
        if cfg is None:
            return {"node": node_id, "voted": None, "clients": [], "error": "voter is not configured"}

        async def read(client):
            return await client.request(["ACTION: VoterStatus", f"NODE: {node_id}"])

        try:
            raw = await self.pool.call(cfg.ami(), read)
        except AmiError:
            return {"node": node_id, "voted": None, "clients": [], "error": "voter status unavailable"}
        payload = parse_voter(raw)
        payload["node"] = node_id
        return payload

    async def _stats_loop(self) -> None:
        index = 0
        while not self.stop.is_set():
            if not self.settings.stats_enabled:
                await asyncio.sleep(2)
                continue
            nodes = self._favorite_nodes()
            if not nodes:
                await asyncio.sleep(2)
                continue
            node = nodes[index % len(nodes)]
            index += 1
            activity = await self._fetch_stats(node)
            await self.hub.publish_stats(activity.as_dict())
            await asyncio.sleep(max(1.0, self.settings.stats_gap))

    def _favorite_nodes(self) -> list[str]:
        try:
            items = load_file(self.settings.favorites_dir, self.favorite_file)
        except (OSError, ValueError, FileNotFoundError):
            return []
        found = []
        for item in items:
            if not item.node or not item.node.isdigit():
                continue
            number = int(item.node)
            if self.settings.demo or 2000 <= number < 2_000_000:
                found.append(item.node)
        return found

    async def _fetch_stats(self, node: str):
        now = time.time()
        if self.settings.demo:
            return demo.stats_for(node, now)
        if self.http is None:
            return self.stats.observe(Sample(node=node, error="stats client is not ready", at=now), now)
        url = f"http://stats.allstarlink.org/api/stats/{node}"
        try:
            async with self.http.get(url) as response:
                if response.status == 429:
                    await asyncio.sleep(15)
                    return self.stats.observe(Sample(node=node, error="stats rate limit", at=now), now)
                if response.status != 200:
                    return self.stats.observe(Sample(node=node, error=f"stats HTTP {response.status}", at=now), now)
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
            return self.stats.observe(Sample(node=node, error="stats unavailable", at=now), now)
        return self.stats.observe(parse_stats_payload(node, payload, now), now)

    async def _directory_loop(self) -> None:
        while not self.stop.is_set():
            if not self.settings.demo and self.directory.needs_refresh() and self.http is not None:
                try:
                    await self.directory.refresh(self.http)
                except (aiohttp.ClientError, OSError) as exc:
                    log.warning("node directory refresh failed: %s", exc)
            await asyncio.sleep(30)

    async def _schedule_loop(self) -> None:
        last = datetime.now()
        while not self.stop.is_set():
            await asyncio.sleep(10)
            now = datetime.now()
            minutes = due_minutes(last, now) if now >= last else []
            last = now
            if self.schedule is None or not minutes:
                continue
            for moment in minutes:
                await self.run_due(moment)

    async def run_due(self, moment: datetime) -> None:
        """Run every job set for this minute; a one-time job turns itself off."""
        for job in self.schedule.jobs():
            if not job.runs_at(moment):
                continue
            try:
                await self.run_scheduled(job)
            except Exception:
                log.exception("schedule: job %s failed", job.id)
            finally:
                if job.date:
                    self.schedule.set_enabled(job.id, False)

    async def run_scheduled(self, job: Job, *, by: str = "", peer: str = "schedule") -> str:
        """Run one job as its owner, store the result, and log it."""
        result = await self._run_job(job)
        self.schedule.finished(job.id, time.time(), result)
        what = job.target or job.sound or job.template
        log.info("schedule: job %s (%s %s on %s) %s", job.id, job.action, what, job.node, result)
        self.record(
            by or job.owner,
            f"scheduled {ACTIONS.get(job.action, job.action).lower()}",
            node=job.node,
            target=what,
            detail=" · ".join(filter(None, [job.note, result])),
            peer=peer,
        )
        return result

    async def _run_job(self, job: Job) -> str:
        owner = self.auth.get_user(job.owner)
        if owner is None:
            return f"skipped: the account {job.owner} was removed"
        if job.node not in self.settings.monitored_ids() or not owner.can_command(job.node):
            return f"skipped: {job.owner} may not control {job.node}"
        try:
            if job.action == "play":
                await self.play_sound(job.node, self.sounds.resolve(job.sound), job.scope, job.owner)
                return f"played {job.sound}"
            if job.action == "command":
                if job.template not in {item.command for item in self.settings.commands}:
                    return "skipped: that command is no longer in commands.ini"
                built = action_command("custom", job.node, job.target or None, template=job.template)
            else:
                built = action_command(job.action, job.node, job.target or None, permanent=job.permanent)
            check_command(built)
            output = await self.run_command(built, job.node)
        except (AmiError, ValueError) as exc:
            return f"failed: {exc}"
        first = output.strip().splitlines()[0] if output.strip() else "OK"
        return "failed: " + first[7:] if first.startswith("Error: ") else f"ran {built} ({first})"

    async def access_lists(self, local: str) -> dict:
        if self.settings.demo:
            lists = {
                name: [{"node": node, "comment": comment} for node, comment in sorted(self._demo_access.get((name, local), {}).items())]
                for name in access.LISTS
            }
        else:
            raw = []
            for name in access.LISTS:
                raw.append(await self.run_command(access.show_command(name, local), local))
            lists = access.parse_lists("\n".join(raw), local)
        for rows in lists.values():
            for row in rows:
                row["desc"] = self.describe(row["node"])
        return lists

    async def access_change(self, local: str, which: str, remote: str, comment: str, add: bool) -> None:
        command = access.put_command(which, local, remote, comment) if add else access.del_command(which, local, remote)
        if self.settings.demo:
            name = access._list(which)
            bucket = self._demo_access.setdefault((name, local), {})
            if add:
                bucket[remote] = comment or "added from SM-Elite"
            else:
                bucket.pop(remote, None)
            return
        result = await self.run_command(command, local)
        lowered = result.lower()
        if lowered.startswith("error") or "fail" in lowered:
            raise AmiError(result)

    async def play_sound(self, local: str, path: str, scope: str, actor: str) -> None:
        verb = SCOPES.get(scope)
        if verb is None:
            raise ValueError("pick where to play it")
        log.info("sound: %s played %s on %s (%s)", actor, path, local, scope)
        result = await self.run_command(f"rpt {verb} {local} {path}", local)
        if result.lower().startswith("error"):
            raise AmiError(result)

    async def asterisk_action(self, action: str, local: str, actor: str) -> str:
        command, done = ASTERISK_ACTIONS[action]
        log.warning("admin: %s ran %r via node %s", actor, command, local)
        try:
            result = await self.run_command(command, local)
        except AmiError:
            if action != "asterisk_restart":
                raise
            return done
        if result.lower().startswith("error"):
            raise AmiError(result)
        return done

    async def run_command(self, command: str, local: str) -> str:
        if self.settings.demo:
            return demo.command_result(command)
        cfg = self.settings.node_by_id(local)
        if cfg is None:
            raise AmiError("node is not configured")

        async def send(client):
            return await client.command(command)

        raw = await self.pool.call(cfg.ami(), send)
        lines = []
        for line in raw.replace("\r", "").split("\n"):
            line = line.strip()
            if not line or line.lower().startswith(("response:", "actionid:", "privilege:", "message:")):
                continue
            if line.lower().startswith("output:"):
                line = line.split(":", 1)[1].strip()
            lines.append(line)
        text = "\n".join(lines).strip()
        if "response: error" in raw.lower():
            return "Error: " + (text or "command was rejected")
        return text or "OK"
