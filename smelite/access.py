"""Inbound connection allow and deny lists.

The stock ASL3 dialplan ([radio-secure] in extensions.conf) checks the
Asterisk database before accepting a node link:

    allowlist/<local>/<remote>   if the family has any keys, only these connect
    denylist/<local>/<remote>    otherwise, these are refused

Only incoming links are affected. Links the node starts, and connections from
127.0.0.1, are never checked.
"""

from __future__ import annotations

import re

from .commands import NODE_RE, CommandError, check_command

LISTS = ("allowlist", "denylist")
COMMENT_RE = re.compile(r"^[A-Za-z0-9 .,_/()#:+\-]{0,50}$")
_ROW = re.compile(r"^/(allowlist|denylist)/(\d+)/(\d+)\s*:\s*(.*)$")


def _node(value: str, label: str) -> str:
    text = str(value).strip()
    if not NODE_RE.fullmatch(text):
        raise CommandError(f"{label} must be a node number")
    return text


def _list(value: str) -> str:
    name = str(value).strip().lower()
    if name in {"allow", "deny"}:
        name += "list"
    if name not in LISTS:
        raise CommandError("list must be allow or deny")
    return name


def show_command(which: str, local: str) -> str:
    return check_command(f"database show {_list(which)}/{_node(local, 'local node')}")


def put_command(which: str, local: str, remote: str, comment: str = "") -> str:
    comment = " ".join(str(comment).split())
    if not COMMENT_RE.fullmatch(comment):
        raise CommandError("comment may use letters, numbers, spaces, and . , _ / ( ) # : + - (50 max)")
    value = comment or "added from SM-Elite"
    return check_command(
        f'database put {_list(which)}/{_node(local, "local node")} {_node(remote, "node")} "{value}"'
    )


def del_command(which: str, local: str, remote: str) -> str:
    return check_command(f"database del {_list(which)}/{_node(local, 'local node')} {_node(remote, 'node')}")


def parse_lists(raw: str, local: str) -> dict[str, list[dict[str, str]]]:
    """Read ``database show`` output into allow and deny entries for one node."""
    found: dict[str, list[dict[str, str]]] = {"allowlist": [], "denylist": []}
    for line in raw.replace("\r", "").split("\n"):
        line = line.strip()
        if line.lower().startswith("output:"):
            line = line.split(":", 1)[1].strip()
        match = _ROW.match(line)
        if not match or match.group(2) != local:
            continue
        found[match.group(1)].append({"node": match.group(3), "comment": match.group(4).strip()})
    for rows in found.values():
        rows.sort(key=lambda row: int(row["node"]))
    return found
