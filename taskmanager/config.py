"""TaskManager configuration."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PORT = 7373
DEFAULT_HOST = "127.0.0.1"
DEFAULT_DB_NAME = "taskmanager.db"
APP_NAME = "TaskManager"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def data_dir() -> Path:
    d = _home() / ".taskmanager"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / DEFAULT_DB_NAME


def themes_dir() -> Path:
    d = data_dir() / "themes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return data_dir() / "settings.json"


def server_url() -> str:
    host = os.environ.get("TASKMANAGER_HOST", DEFAULT_HOST)
    port = int(os.environ.get("TASKMANAGER_PORT", DEFAULT_PORT))
    return f"http://{host}:{port}"
