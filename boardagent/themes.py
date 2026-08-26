"""Default built-in TUI themes for BoardAgent.

Each theme is a JSON-compatible dict (same shape as community theme files in
~/.boardagent/themes/). The TUI maps these tokens onto Textual's Theme object
(see tui.py:_build_theme_object) — background/foreground/primary/etc. become
Textual color slots, and every token is also exposed as a CSS variable so the
whole app re-themes instantly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _theme(name: str, colors: dict[str, str]) -> dict[str, Any]:
    return {"name": name, "description": f"Built-in {name} theme", "colors": colors}


AMBER = _theme(
    "amber",
    {
        "background": "#0a0a0a",
        "foreground": "#ffb000",
        "primary": "#ffbf00",
        "secondary": "#806000",
        "accent": "#ffea00",
        "border": "#b38600",
        "muted": "#5c4600",
        "error": "#ff3b30",
        "success": "#00ff66",
        "status-todo": "#aaaaaa",
        "status-in_progress": "#ffbf00",
        "status-blocked": "#ff3b30",
        "status-done": "#00ff66",
        "priority-red": "#ff3b30",
        "priority-orange": "#ff9500",
        "priority-yellow": "#ffcc00",
        "priority-green": "#00ff66",
        "priority-blue": "#00ccff",
        "priority-white": "#ffffff",
    },
)

MATRIX = _theme(
    "matrix",
    {
        "background": "#000000",
        "foreground": "#00ff41",
        "primary": "#00ff41",
        "secondary": "#008f11",
        "accent": "#003b00",
        "border": "#00cc33",
        "muted": "#005511",
        "error": "#ff3b30",
        "success": "#00ff66",
        "status-todo": "#aaaaaa",
        "status-in_progress": "#00ff41",
        "status-blocked": "#ff3b30",
        "status-done": "#00ff66",
        "priority-red": "#ff3b30",
        "priority-orange": "#ff9500",
        "priority-yellow": "#ffcc00",
        "priority-green": "#00ff66",
        "priority-blue": "#00ccff",
        "priority-white": "#ffffff",
    },
)

MIDNIGHT = _theme(
    "midnight",
    {
        "background": "#0a0e1a",
        "foreground": "#c9d4ff",
        "primary": "#4da3ff",
        "secondary": "#26406e",
        "accent": "#7fd4ff",
        "border": "#26406e",
        "muted": "#3d4a6b",
        "error": "#ff5c5c",
        "success": "#4dff88",
        "status-todo": "#8a97b8",
        "status-in_progress": "#ffd166",
        "status-blocked": "#ff5c5c",
        "status-done": "#4dff88",
        "priority-red": "#ff5c5c",
        "priority-orange": "#ff9f43",
        "priority-yellow": "#ffd93d",
        "priority-green": "#4dff88",
        "priority-blue": "#4da3ff",
        "priority-white": "#ffffff",
    },
)

GRUV = _theme(
    "gruv",
    {
        "background": "#282828",
        "foreground": "#ebdbb2",
        "primary": "#fabd2f",
        "secondary": "#b8bb26",
        "accent": "#83a598",
        "border": "#504945",
        "muted": "#928374",
        "error": "#fb4934",
        "success": "#b8bb26",
        "status-todo": "#a89984",
        "status-in_progress": "#fabd2f",
        "status-blocked": "#fb4934",
        "status-done": "#b8bb26",
        "priority-red": "#fb4934",
        "priority-orange": "#fe8019",
        "priority-yellow": "#fabd2f",
        "priority-green": "#b8bb26",
        "priority-blue": "#83a598",
        "priority-white": "#ebdbb2",
    },
)

SYNTH = _theme(
    "synth",
    {
        "background": "#1a0b2e",
        "foreground": "#e5d9ff",
        "primary": "#ff2e88",
        "secondary": "#7a1fa8",
        "accent": "#00e5ff",
        "border": "#4a1d7a",
        "muted": "#6d5a8c",
        "error": "#ff3b5c",
        "success": "#00ff9f",
        "status-todo": "#9a86c2",
        "status-in_progress": "#ff2e88",
        "status-blocked": "#ff3b5c",
        "status-done": "#00ff9f",
        "priority-red": "#ff3b5c",
        "priority-orange": "#ff9f43",
        "priority-yellow": "#ffe14d",
        "priority-green": "#00ff9f",
        "priority-blue": "#00e5ff",
        "priority-white": "#ffffff",
    },
)


BUILTIN_THEMES: dict[str, dict[str, Any]] = {
    "amber": AMBER,
    "matrix": MATRIX,
    "midnight": MIDNIGHT,
    "gruv": GRUV,
    "synth": SYNTH,
}


def get_builtin_theme(name: str) -> dict[str, Any] | None:
    return BUILTIN_THEMES.get(name)


def write_builtin_themes(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, theme in BUILTIN_THEMES.items():
        path = target_dir / f"{name}.json"
        if not path.exists():
            path.write_text(json.dumps(theme, indent=2), encoding="utf-8")
