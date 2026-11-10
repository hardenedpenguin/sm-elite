"""Run the SM-Elite dashboard."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from aiohttp import web

from .actions import ActionLog
from .astdb import NodeDirectory
from .auth import AuthStore
from .config import NodeConfig, load_settings
from .history import HistoryStore
from .monitor import Engine
from .schedule import ScheduleStore
from .server import create_app


def config_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("SMELITE_CONFIG")
    if env:
        return Path(env)
    local = Path.cwd() / "config" / "smelite.ini"
    system = Path("/etc/sm-elite/smelite.ini")
    if local.is_file():
        return local
    if system.is_file():
        return system
    return local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SM-Elite AllStar dashboard")
    parser.add_argument("--config", help="path to smelite.ini")
    parser.add_argument("--demo", action="store_true", help="run with synthetic nodes and no Asterisk")
    parser.add_argument("--listen", help="override the listen address (comma-separated for several)")
    parser.add_argument("--port", type=int, help="override the listen port")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = config_path(args.config)
    if not path.is_file():
        print(f"config not found: {path}", file=sys.stderr)
        return 2
    settings = load_settings(path)
    if args.demo:
        settings.demo = True
    if args.listen:
        settings.listen = [part.strip() for part in args.listen.split(",") if part.strip()]
    if args.port:
        settings.port = args.port
    if settings.demo and not settings.nodes:
        settings.nodes = [
            NodeConfig(node="1998", host="demo", port=0, user="demo", password="", voters=["1998"], label="Demo repeater"),
            NodeConfig(node="1999", host="demo", port=0, user="demo", password="", label="Second demo"),
        ]
    directory = NodeDirectory(
        settings.astdb_file,
        settings.private_db,
        "" if settings.demo else settings.astdb_url,
        settings.db_refresh_minutes,
    )
    auth = AuthStore(settings.data_dir / "smelite.db")
    history = HistoryStore(settings.data_dir / "history.db", settings.history_days)
    actions = ActionLog(settings.data_dir / "actions.db", settings.action_days)
    schedule = ScheduleStore(settings.data_dir / "schedule.db")
    engine = Engine(settings, auth, directory, history, actions, schedule)
    app = create_app(engine)
    web.run_app(
        app, host=settings.listen, port=settings.port,
        access_log_format='%a (for %{X-Forwarded-For}i) "%r" %s %b "%{User-Agent}i"',
        print=lambda msg: logging.getLogger("smelite").info(msg),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
