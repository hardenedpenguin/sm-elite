"""Site-wide default theme. The colors themselves live in web/js/theme.js."""

from __future__ import annotations

import re
from typing import Any

BUILT_IN = ("dark", "light", "blue", "green", "seafoam", "neon-purple")
COLOR_KEYS = (
    "bg", "bg-raise", "line", "text", "muted", "accent", "accent-ink",
    "local", "network", "telemetry", "playback", "idle", "linked", "keyed", "danger", "heat",
)
# Fills keys that older custom themes omit after a new status color is added.
DEFAULT_COLORS = {
    "local": "#e39b2b",
    "network": "#3db7e8",
    "telemetry": "#b79af0",
    "playback": "#ef7aa8",
    "linked": "#3dbe86",
    "keyed": "#e39b2b",
    "heat": "#c4473a",
    "idle": "#6d8294",
    "danger": "#e15d5d",
}
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
    if not isinstance(colors, dict):
        raise ValueError("a custom theme needs every color as #rrggbb")
    cleaned: dict[str, str] = {}
    for key in COLOR_KEYS:
        raw = str(colors.get(key) or "")
        if not HEX.fullmatch(raw):
            raw = DEFAULT_COLORS.get(key, "")
        if not HEX.fullmatch(raw):
            raise ValueError("a custom theme needs every color as #rrggbb")
        cleaned[key] = raw.lower()
    label = " ".join(str(value.get("label") or "").split())[:40] or "Custom"
    return {"id": "custom", "label": label, "colors": cleaned}


def label(theme: dict | None) -> str:
    if theme is None:
        return "stock dark"
    return theme.get("label") or theme["id"]
