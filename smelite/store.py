"""Shared base for the SQLite files in the data folder."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SQLiteStore:
    """One SQLite file. Subclasses create their tables in _init, which must be safe to run again."""

    tables: tuple[str, ...] = ()

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        self._db.close()

    def backup_to(self, path: Path) -> None:
        dest = sqlite3.connect(path)
        try:
            self._db.backup(dest)
        finally:
            dest.close()

    def restore_from(self, path: Path) -> None:
        """Replace every row with the contents of another copy of this file, then bring its schema up to date."""
        source = sqlite3.connect(path)
        try:
            source.backup(self._db)
        finally:
            source.close()
        self._init()
