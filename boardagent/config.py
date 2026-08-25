"""BoardAgent configuration."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PORT = 7373
DEFAULT_HOST = "127.0.0.1"
DEFAULT_DB_NAME = "boardagent.db"
APP_NAME = "BoardAgent"


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


def server_url() -> str:
    host = os.environ.get("BOARDAGENT_HOST", DEFAULT_HOST)
    port = int(os.environ.get("BOARDAGENT_PORT", DEFAULT_PORT))
    return f"http://{host}:{port}"
