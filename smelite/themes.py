"""Site-wide default theme. The colors themselves live in web/js/theme.js."""

from __future__ import annotations

import re
from typing import Any

BUILT_IN = ("dark", "light", "blue", "green", "seafoam", "neon-purple")
COLOR_KEYS = (
    "bg", "bg-raise", "line", "text", "muted", "accent", "accent-ink",
    "local", "network", "telemetry", "playback", "idle", "linked", "danger", "heat",
)
HEX = re.compile(r"#[0-9a-fA-F]{6}")


def clean(value: Any) -> dict | None:
    """A built-in theme as {"id"}, a custom one as {"id": "custom", "label", "colors"}, or None for the stock look."""
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise ValueError("pick a theme")
    theme_id = str(value.get("id") or "")
    if theme_id in BUILT_IN:
        return {"id": theme_id}
    if theme_id != "custom":
        raise ValueError("unknown theme")
    colors = value.get("colors")
    if not isinstance(colors, dict) or any(not HEX.fullmatch(str(colors.get(key) or "")) for key in COLOR_KEYS):
        raise ValueError("a custom theme needs every color as #rrggbb")
    label = " ".join(str(value.get("label") or "").split())[:40] or "Custom"
    return {"id": "custom", "label": label, "colors": {key: str(colors[key]).lower() for key in COLOR_KEYS}}


def label(theme: dict | None) -> str:
    if theme is None:
        return "stock dark"
    return theme.get("label") or theme["id"]
