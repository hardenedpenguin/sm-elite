"""Session login with roles.

Roles are viewer, operator, and admin. Admin includes operator. A user with
no node restrictions may control every configured node. A restriction list
limits operator actions to those nodes, the same idea as Allmon3
user-restrictions, stored in SQLite instead of a side file.

Passwords are hashed with scrypt. The session cookie is the only credential
the browser holds.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .store import SQLiteStore

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,32}$")
ROLES = ("viewer", "operator", "admin")
SESSION_TTL = 12 * 60 * 60


@dataclass
class User:
    name: str
    role: str
    nodes: tuple[str, ...] | None

    def allows(self, need: str) -> bool:
        order = {"viewer": 0, "operator": 1, "admin": 2}
        return order.get(self.role, -1) >= order[need]

    def can_command(self, node: str) -> bool:
        if not self.allows("operator"):
            return False
        if self.nodes is None:
            return True
        return node in self.nodes


def _check_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")


def _node_text(nodes: list[str] | None) -> str | None:
    clean = []
    for node in nodes or []:
        node = str(node).strip()
        if not re.fullmatch(r"\d{1,8}", node):
            raise ValueError("restriction list has a bad node number")
        clean.append(node)
    return ",".join(clean) if clean else None


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


SETUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def setup_code(path: Path) -> str:
    """The one-time code needed to create the first admin account.

    Kept in a file only the service account (and root) can read, so taking
    over a freshly installed node needs shell access to it.
    """
    try:
        code = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        code = ""
    if code:
        return code
    code = "-".join("".join(secrets.choice(SETUP_ALPHABET) for _ in range(4)) for _ in range(3))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(code + "\n")
    return code


def setup_code_matches(path: Path, given: str) -> bool:
    given = re.sub(r"[\s-]", "", given).upper()
    expected = setup_code(path).replace("-", "")
    return bool(given) and hmac.compare_digest(given, expected)


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(digest.hex(), digest_hex)


class AuthStore(SQLiteStore):
    tables = ("users", "sessions")

    def _init(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                name TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                nodes TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires INTEGER NOT NULL
            );
            """
        )
        self._db.commit()

    def user_count(self) -> int:
        row = self._db.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])

    def create_user(self, name: str, password: str, role: str, nodes: list[str] | None = None) -> None:
        if not USERNAME_RE.fullmatch(name):
            raise ValueError("username must be 2-32 letters, numbers, dot, dash, or underscore")
        if role not in ROLES:
            raise ValueError("unknown role")
        _check_password(password)
        try:
            self._db.execute(
                "INSERT INTO users (name, password, role, nodes) VALUES (?, ?, ?, ?)",
                (name, _hash_password(password), role, _node_text(nodes)),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("that username already exists") from exc
        self._db.commit()

    def update_user(self, name: str, role: str, nodes: list[str] | None) -> None:
        if role not in ROLES:
            raise ValueError("unknown role")
        cur = self._db.execute("UPDATE users SET role = ?, nodes = ? WHERE name = ?", (role, _node_text(nodes), name))
        self._db.commit()
        if cur.rowcount == 0:
            raise ValueError("user was not found")

    def set_password(self, name: str, password: str, keep_token: str | None = None) -> None:
        """Replace a password and end that user's other sessions."""
        _check_password(password)
        cur = self._db.execute("UPDATE users SET password = ? WHERE name = ?", (_hash_password(password), name))
        if cur.rowcount == 0:
            self._db.rollback()
            raise ValueError("user was not found")
        self._db.execute("DELETE FROM sessions WHERE username = ? AND token != ?", (name, keep_token or ""))
        self._db.commit()

    def delete_user(self, name: str) -> None:
        cur = self._db.execute("DELETE FROM users WHERE name = ?", (name,))
        self._db.execute("DELETE FROM sessions WHERE username = ?", (name,))
        self._db.commit()
        if cur.rowcount == 0:
            raise ValueError("user was not found")

    def list_users(self) -> list[dict]:
        rows = self._db.execute("SELECT name, role, nodes FROM users ORDER BY name").fetchall()
        return [
            {
                "name": row["name"],
                "role": row["role"],
                "nodes": row["nodes"].split(",") if row["nodes"] else None,
            }
            for row in rows
        ]

    def get_user(self, name: str) -> User | None:
        row = self._db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
        return None if row is None else self._user_from_row(row)

    def _user_from_row(self, row: sqlite3.Row) -> User:
        nodes = tuple(row["nodes"].split(",")) if row["nodes"] else None
        return User(name=row["name"], role=row["role"], nodes=nodes)

    def verify(self, name: str, password: str) -> User | None:
        row = self._db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
        if row is None or not _verify_password(password, row["password"]):
            return None
        return self._user_from_row(row)

    def create_session(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        self._db.execute(
            "INSERT INTO sessions (token, username, expires) VALUES (?, ?, ?)",
            (token, user.name, int(time.time()) + SESSION_TTL),
        )
        self._db.commit()
        return token

    def user_for_token(self, token: str | None) -> User | None:
        if not token:
            return None
        now = int(time.time())
        row = self._db.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.name = sessions.username
            WHERE sessions.token = ? AND sessions.expires > ?
            """,
            (token, now),
        ).fetchone()
        if row is None:
            return None
        return self._user_from_row(row)

    def drop_session(self, token: str | None) -> None:
        if not token:
            return
        self._db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        self._db.commit()
