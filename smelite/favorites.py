"""Read and write Supermon-compatible favorites.ini files.

Each section holds parallel ``label[]`` / ``cmd[]`` lists paired by position,
so all labels may come before all commands (as supermon-ng writes them).
``[general]`` entries apply to every node; a ``[<node>]`` section applies to
that local node only and may use plain ``label`` / ``cmd`` keys for a single
entry. A node number is taken from ``ilink <code> <node>`` when the command is
a link. Header rows are labels with no node. Rewriting the file from the
parsed list avoids the string-splice bugs in older favorites editors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_NODE_IN_CMD = re.compile(r"\bilink\s+\d+\s+(\d{1,8})\b", re.IGNORECASE)
_FILE_RE = re.compile(r"^favorites[\w.\-]*\.ini$")
_SECTION_RE = re.compile(r"^\[([\w.\-]+)\]")
GENERAL = "general"


@dataclass
class Favorite:
    label: str
    cmd: str
    node: str | None
    section: str = GENERAL
    listed: bool = True

    @property
    def header(self) -> bool:
        return self.node is None


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def parse_favorites(text: str) -> list[Favorite]:
    sections: dict[str, dict] = {}
    current = GENERAL
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        match = _SECTION_RE.match(line)
        if match:
            current = match.group(1)
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        listed = key.endswith("[]")
        name = key[:-2].rstrip() if listed else key
        if name not in ("label", "cmd"):
            continue
        entry = sections.setdefault(current, {"label": [], "cmd": [], "listed": True})
        if listed:
            entry[name].append(_unquote(value))
        else:
            entry[name] = [_unquote(value)]
            entry["listed"] = False
    items: list[Favorite] = []
    for section, entry in sections.items():
        labels, cmds = entry["label"], entry["cmd"]
        for index in range(max(len(labels), len(cmds))):
            label = labels[index] if index < len(labels) else ""
            cmd = cmds[index] if index < len(cmds) else ""
            if not label and not cmd:
                continue
            match = _NODE_IN_CMD.search(cmd)
            items.append(Favorite(label, cmd, match.group(1) if match else None, section, entry["listed"]))
    return items


def render_favorites(items: list[Favorite]) -> str:
    lines = [
        "; SM-Elite favorites. Compatible with Supermon and AllScan.",
        "; [general] entries apply to every node; [<node>] entries to that node only.",
        "; %node% is the local node when a command is shown to an operator.",
    ]
    order = [GENERAL] + [item.section for item in items if item.section != GENERAL]
    for section in dict.fromkeys(order):
        group = [item for item in items if item.section == section]
        if not group and section != GENERAL:
            continue
        lines += ["", f"[{section}]"]
        listed = len(group) != 1 or group[0].listed
        suffix = "[]" if listed else ""
        for item in group:
            label = item.label.replace('"', "'")
            cmd = item.cmd.replace('"', "'")
            lines.append(f'label{suffix} = "{label}"')
            lines.append(f'cmd{suffix} = "{cmd}"')
    return "\n".join(lines).rstrip() + "\n"


def safe_filename(name: str) -> str:
    base = Path(name).name
    if not _FILE_RE.fullmatch(base):
        raise ValueError("favorites file name is not allowed")
    return base


def list_files(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.glob("favorites*.ini") if path.is_file())


def load_file(directory: Path, name: str) -> list[Favorite]:
    path = directory / safe_filename(name)
    if not path.is_file():
        raise FileNotFoundError(name)
    return parse_favorites(path.read_text(encoding="utf-8", errors="replace"))


def save_file(directory: Path, name: str, items: list[Favorite]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / safe_filename(name)
    path.write_text(render_favorites(items), encoding="utf-8")


def add_favorite(items: list[Favorite], node: str, label: str) -> list[Favorite]:
    if not re.fullmatch(r"\d{1,8}", node):
        raise ValueError("node number is invalid")
    label = label.strip() or node
    if any(item.node == node for item in items):
        raise ValueError("that node is already a favorite")
    items = list(items)
    items.append(Favorite(
        label=label,
        cmd=f"rpt cmd %node% ilink 3 {node}",
        node=node,
    ))
    return items


def remove_favorite(items: list[Favorite], node: str) -> list[Favorite]:
    kept = [item for item in items if item.node != node]
    if len(kept) == len(items):
        raise ValueError("favorite was not found")
    return kept
