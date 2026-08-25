"""Textual TUI for TaskManager."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from . import __version__
from .config import DEFAULT_HOST, DEFAULT_PORT, server_url, settings_path, themes_dir
from .themes import BUILTIN_THEMES, get_builtin_theme, write_builtin_themes


class ThemeManager:
    """Loads built-in and user themes from ~/.taskmanager/themes."""

    def __init__(self):
        write_builtin_themes(themes_dir())
        self._themes: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        self._themes = dict(BUILTIN_THEMES)
        for path in sorted(themes_dir().glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                name = data.get("name", path.stem)
                self._themes[name.lower()] = data
            except Exception:
                continue

    def list(self) -> list[str]:
        return sorted(self._themes.keys())

    def get(self, name: str) -> dict[str, Any]:
        return self._themes.get(name.lower(), self._themes["amber"])


THEME = ThemeManager()


def _load_settings() -> dict[str, Any]:
    path = settings_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"opacity": 100, "theme": "amber"}


def _save_settings(settings: dict[str, Any]) -> None:
    settings_path().write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _api_base() -> str:
    host = os.environ.get("TASKMANAGER_HOST", DEFAULT_HOST)
    port = int(os.environ.get("TASKMANAGER_PORT", DEFAULT_PORT))
    return f"http://{host}:{port}"


class ServiceUnavailable(Static):
    """Shown when the background service cannot be reached."""

    def compose(self) -> ComposeResult:
        yield Static(
            "[b]SERVICE NOT RUNNING[/b]\n\n"
            "Start it with:\n\n"
            "[b]taskmanager-server[/b]\n\n"
            "or:\n\n"
            "[b]python -m taskmanager.api[/b]",
            classes="error-message",
        )


class TaskTable(DataTable):
    """Task list table."""

    def __init__(self, ai_mode: bool = False) -> None:
        super().__init__(show_cursor=True, cursor_type="row")
        self.ai_mode = ai_mode
        self.zebra_stripes = True


class TaskManagerApp(App):
    """Main Textual app."""

    CSS = """
    Screen { align: center middle; }
    .error-message { width: 60; height: auto; border: solid $primary; padding: 1 2; text-align: center; }
    #task-list { width: 100%; height: 100%; }
    #detail-panel { width: 40%; height: 100%; border: solid $primary; padding: 1 2; }
    #detail-content { width: 100%; height: 100%; }
    #settings-panel { width: 100%; height: 100%; padding: 1 2; }
    .settings-row { height: auto; margin: 1 0; }
    Footer { background: $surface; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("a", "toggle_ai", "AI mode"),
        ("c", "create", "Create"),
    ]

    ai_mode = reactive(False)
    service_up = reactive(True)
    tasks: reactive[list[dict[str, Any]]] = reactive([])
    settings: dict[str, Any]

    def __init__(self) -> None:
        super().__init__()
        self.settings = _load_settings()
        # Plain instance attribute. NOTE: must NOT be named current_theme —
        # Textual 8's App base class defines current_theme as a read-only
        # property, so assignment would raise AttributeError.
        self.active_theme = self.settings.get("theme", "amber")

    async def on_mount(self) -> None:
        self.title = f"TaskManager {__version__}"
        self.apply_theme(self.active_theme)
        self.check_service()
        await self.action_refresh()
        self.set_interval(10, self.action_refresh)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tasks"):
            with TabPane("Tasks", id="tasks"):
                with Horizontal():
                    yield TaskTable(ai_mode=False)
                    yield Vertical(
                        Static("Select a task to view details", id="detail-content"),
                        id="detail-panel",
                    )
            with TabPane("Settings", id="settings"):
                yield VerticalScroll(
                    Label("Theme"),
                    Select(
                        ((name, name) for name in THEME.list()),
                        value=self.active_theme,
                        id="theme-select",
                    ),
                    Label("Opacity (Windows only, requires restart)"),
                    Input(
                        value=str(self.settings.get("opacity", 100)),
                        placeholder="0-100",
                        id="opacity-input",
                    ),
                    Button("Save Settings", id="save-settings"),
                    id="settings-panel",
                )
        yield Footer()

    def watch_ai_mode(self, ai_mode: bool) -> None:
        table = self.query_one(TaskTable)
        table.ai_mode = ai_mode
        self.populate_table()
        self.notify("AI mode: ON" if ai_mode else "AI mode: OFF")

    def check_service(self) -> None:
        try:
            r = httpx.get(f"{_api_base()}/healthz", timeout=2.0)
            self.service_up = r.status_code == 200
        except Exception:
            self.service_up = False
        if not self.service_up:
            self.notify("Service not running. Start taskmanager-server.", severity="error", timeout=10)

    def apply_theme(self, name: str) -> None:
        theme = THEME.get(name)
        colors = theme.get("colors", {})
        css_lines = [":root {"]
        for key, value in colors.items():
            var = key.replace("-", "-")
            css_lines.append(f"    --{var}: {value};")
        css_lines.append("}")
        # Map a few semantic slots to Textual CSS variables
        self.styles.background = colors.get("background", "#0a0a0a")
        self.styles.color = colors.get("foreground", "#ffb000")
        self.CSS += "\n".join(css_lines)
        self.active_theme = name

    def populate_table(self) -> None:
        table = self.query_one(TaskTable)
        table.clear(columns=True)
        if self.ai_mode:
            table.add_columns("ID", "Title", "Status", "Priority", "Project", "Agent", "Metadata")
        else:
            table.add_columns("Title", "Status", "Priority", "Project", "Due")
        for t in self.tasks:
            if self.ai_mode:
                metadata = json.dumps(t.get("metadata", {}))
                table.add_row(
                    str(t["id"]),
                    t["title"],
                    t["status"],
                    t["priority"],
                    t.get("project") or "",
                    t.get("owner_agent_id") or "",
                    metadata[:60] + "..." if len(metadata) > 60 else metadata,
                    key=str(t["id"]),
                )
            else:
                due = t.get("due") or ""
                if isinstance(due, str) and due:
                    try:
                        due = datetime.fromisoformat(due).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass
                table.add_row(
                    t["title"],
                    t["status"],
                    t["priority"],
                    t.get("project") or "",
                    due,
                    key=str(t["id"]),
                )

    async def action_refresh(self) -> None:
        try:
            r = httpx.get(f"{_api_base()}/tasks", timeout=5.0)
            r.raise_for_status()
            data = r.json()
            self.tasks = data.get("tasks", [])
            self.service_up = True
        except Exception as exc:
            self.service_up = False
            self.notify(f"Refresh failed: {exc}", severity="error", timeout=5)
            self.tasks = []
        self.populate_table()

    def action_toggle_ai(self) -> None:
        self.ai_mode = not self.ai_mode

    async def action_create(self) -> None:
        if not self.service_up:
            self.notify("Service not running.", severity="error")
            return
        self.push_screen(CreateTaskScreen())

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        task_id = event.row_key.value
        task = next((t for t in self.tasks if str(t["id"]) == task_id), None)
        if task is None:
            return
        detail = self.query_one("#detail-content", Static)
        lines = [
            f"[b]#{task['id']} {task['title']}[/b]\n",
            f"Status: {task['status']}",
            f"Priority: {task['priority']}",
            f"Project: {task.get('project') or '-'}",
        ]
        if task.get("due"):
            lines.append(f"Due: {task['due']}")
        if task.get("description"):
            lines.append(f"\n{task['description']}")
        if self.ai_mode and task.get("metadata"):
            lines.append("\n[b]Metadata[/b]")
            lines.append(json.dumps(task["metadata"], indent=2, default=str))
        detail.update("\n".join(lines))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-settings":
            theme = self.query_one("#theme-select", Select).value
            opacity = self.query_one("#opacity-input", Input).value
            try:
                opacity_int = max(0, min(100, int(opacity)))
            except ValueError:
                opacity_int = 100
            self.settings.update({"theme": str(theme), "opacity": opacity_int})
            _save_settings(self.settings)
            self.apply_theme(str(theme))
            self.notify("Settings saved. Restart TUI for opacity to take effect.")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "theme-select":
            self.apply_theme(str(event.value))


class CreateTaskScreen(Screen):
    """Modal screen to add a task quickly."""

    CSS = """
    CreateTaskScreen { align: center middle; }
    #dialog { width: 60; height: auto; border: solid $primary; padding: 1 2; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[b]Create Task[/b]")
            yield Input(placeholder="Title", id="title")
            yield Input(placeholder="Project", id="project")
            yield Input(placeholder="Priority (red/orange/yellow/green/blue/white)", id="priority", value="white")
            yield Button("Create", id="create")
            yield Button("Cancel", id="cancel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.app.pop_screen()
            return
        title = self.query_one("#title", Input).value
        project = self.query_one("#project", Input).value
        priority = self.query_one("#priority", Input).value or "white"
        if not title:
            self.app.notify("Title required", severity="error")
            return
        try:
            r = httpx.post(
                f"{_api_base()}/tasks",
                json={"title": title, "project": project, "priority": priority},
                timeout=5.0,
            )
            r.raise_for_status()
            self.app.notify("Task created")
            await self.app.action_refresh()
            self.app.pop_screen()
        except Exception as exc:
            self.app.notify(f"Create failed: {exc}", severity="error")


def main() -> None:
    app = TaskManagerApp()
    app.run()


if __name__ == "__main__":
    main()
