"""BoardAgent configuration."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

DEFAULT_PORT = 7373
DEFAULT_HOST = "127.0.0.1"
DEFAULT_DB_NAME = "boardagent.db"
APP_NAME = "BoardAgent"

# Default keybindings: action name -> key. Users can override any of these
# from the Settings tab; the TUI rebuilds its BINDINGS from this dict.
DEFAULT_KEYBINDS: dict[str, str] = {
    "quit": "q",
    "refresh": "r",
    "toggle_ai": "a",
    "create": "c",
    "edit": "e",
    "delete": "d",
    "claim": "l",
    "complete": "t",
}


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def data_dir() -> Path:
    d = _home() / ".boardagent"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    # BOARDAGENT_DB overrides the DB location (used by tests to isolate).
    override = os.environ.get("BOARDAGENT_DB")
    if override:
        return Path(override)
    return data_dir() / DEFAULT_DB_NAME


def themes_dir() -> Path:
    d = data_dir() / "themes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return data_dir() / "settings.json"


def keys_path() -> Path:
    # BOARDAGENT_KEYS overrides the keys location (used by tests to isolate).
    override = os.environ.get("BOARDAGENT_KEYS")
    if override:
        return Path(override)
    return data_dir() / "keys.json"


def server_url() -> str:
    host = os.environ.get("BOARDAGENT_HOST", DEFAULT_HOST)
    port = int(os.environ.get("BOARDAGENT_PORT", DEFAULT_PORT))
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Server settings (persisted in settings.json, editable from the TUI)
# ---------------------------------------------------------------------------

DEFAULT_SERVER_SETTINGS: dict[str, Any] = {
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "api_enabled": True,
    "mcp_enabled": True,
}


def load_server_settings() -> dict[str, Any]:
    """Load persisted server settings, merged over defaults."""
    settings: dict[str, Any] = dict(DEFAULT_SERVER_SETTINGS)
    path = settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in DEFAULT_SERVER_SETTINGS:
                if key in data:
                    settings[key] = data[key]
        except Exception:
            pass
    return settings


def save_server_settings(settings: dict[str, Any]) -> None:
    """Persist server settings, preserving any other settings keys."""
    path = settings_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    for key in DEFAULT_SERVER_SETTINGS:
        if key in settings:
            data[key] = settings[key]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# API keys (persisted in keys.json, editable from the TUI)
# ---------------------------------------------------------------------------

ROLE_READ = "read"
ROLE_WRITE = "write"
ROLE_ADMIN = "admin"
ROLES = (ROLE_READ, ROLE_WRITE, ROLE_ADMIN)


def load_api_keys() -> dict[str, dict[str, str]]:
    """Load API keys: {key: {"name": ..., "role": ...}}."""
    path = keys_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_api_keys(keys: dict[str, dict[str, str]]) -> None:
    keys_path().write_text(json.dumps(keys, indent=2), encoding="utf-8")


def generate_api_key() -> str:
    return "ba_" + secrets.token_urlsafe(24)


def load_keybinds() -> dict[str, str]:
    """Load user keybind overrides, merged over defaults."""
    binds: dict[str, str] = dict(DEFAULT_KEYBINDS)
    path = settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            keybinds = data.get("keybinds", {})
            if isinstance(keybinds, dict):
                for action, key in keybinds.items():
                    if key:
                        binds[action] = str(key)
        except Exception:
            pass
    return binds


def save_keybinds(keybinds: dict[str, str]) -> None:
    path = settings_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["keybinds"] = keybinds
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
