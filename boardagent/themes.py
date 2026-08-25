"""Default built-in TUI themes for BoardAgent."""
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


BUILTIN_THEMES: dict[str, dict[str, Any]] = {
    "amber": AMBER,
    "matrix": MATRIX,
}


def get_builtin_theme(name: str) -> dict[str, Any] | None:
    return BUILTIN_THEMES.get(name)


def write_builtin_themes(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, theme in BUILTIN_THEMES.items():
        path = target_dir / f"{name}.json"
        if not path.exists():
            path.write_text(json.dumps(theme, indent=2), encoding="utf-8")
