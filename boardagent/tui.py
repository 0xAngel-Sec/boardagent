"""Textual TUI for BoardAgent."""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from typing import Any

import httpx
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets._select import SelectCurrent, SelectOverlay

from . import __version__
from .config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ROLE_ADMIN,
    ROLE_READ,
    ROLE_WRITE,
    generate_api_key,
    load_api_keys,
    load_keybinds,
    load_server_settings,
    save_api_keys,
    save_keybinds,
    save_server_settings,
    settings_path,
    themes_dir,
)
from .themes import BUILTIN_THEMES, get_builtin_theme, write_builtin_themes

# Human actions use this agent id (an agent that operates the local TUI).
HUMAN_AGENT_ID = "human"

API_KEY_HEADER = "X-API-Key"

# Action -> footer label
ACTION_LABELS: dict[str, str] = {
    "quit": "Quit",
    "refresh": "Refresh",
    "toggle_ai": "AI mode",
    "create": "Create",
    "edit": "Edit",
    "delete": "Delete",
    "claim": "Claim",
    "complete": "Complete",
}


class ThemeManager:
    """Loads built-in and user themes from ~/.boardagent/themes."""

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
    return {"opacity": 100, "theme": "amber", "ai_mode": False}


def _save_settings(settings: dict[str, Any]) -> None:
    settings_path().write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _load_console_key() -> str:
    """Return the TUI's admin key, creating it on first run.

    The console key is stored in settings.json and registered in keys.json so
    the server accepts it. This keeps API auth honest even for the local TUI.
    """
    settings = _load_settings()
    key = settings.get("console_key")
    if key:
        return key
    key = generate_api_key()
    settings["console_key"] = key
    _save_settings(settings)
    keys = load_api_keys()
    keys[key] = {"name": "console", "role": ROLE_ADMIN}
    save_api_keys(keys)
    return key


def _api_base() -> str:
    server = load_server_settings()
    host = os.environ.get("BOARDAGENT_HOST", server.get("host", DEFAULT_HOST))
    port = int(os.environ.get("BOARDAGENT_PORT", server.get("port", DEFAULT_PORT)))
    return f"http://{host}:{port}"


def _api_headers() -> dict[str, str]:
    return {API_KEY_HEADER: _load_console_key()}


def _find_window_by_title(marker: str) -> int | None:
    """Find a visible console/CASCADIA window whose title contains marker."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        EnumWindows = user32.EnumWindows
        GetClassNameW = user32.GetClassNameW
        GetWindowTextW = user32.GetWindowTextW
        IsWindowVisible = user32.IsWindowVisible
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        found: list[int] = []

        def cb(hwnd, lparam):
            cls = ctypes.create_unicode_buffer(256)
            GetClassNameW(hwnd, cls, 256)
            if cls.value in (
                "CASCADIA_HOSTING_WINDOW_CLASS",
                "ConsoleWindowClass",
            ) and IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(512)
                GetWindowTextW(hwnd, title, 512)
                if marker in title.value:
                    found.append(hwnd)
            return True

        EnumWindows(EnumWindowsProc(cb), 0)
        return found[0] if found else None
    except Exception:
        return None


def _apply_opacity(opacity: int) -> None:
    """Apply window opacity to the terminal window (Windows only).

    Uses SetLayeredWindowAttributes with LWA_ALPHA. Under Windows Terminal the
    console window is a hidden ConPTY, so the window is found by setting a
    unique console title (which WT propagates to the tab/window title) and
    matching it against visible CASCADIA/console windows. Falls back to
    GetConsoleWindow() for classic consoles.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        LWA_ALPHA = 0x2

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.windll.user32

        marker = f"BA-OPACITY-{kernel32.GetCurrentProcessId()}"
        kernel32.SetConsoleTitleW(marker)

        hwnd = None
        for _ in range(10):
            hwnd = _find_window_by_title(marker)
            if hwnd:
                break
            time.sleep(0.05)
        if not hwnd:
            hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        alpha = max(0, min(100, int(opacity))) * 255 // 100
        user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
        # Restore a sane title (Textual's OSC title may not reach WT).
        kernel32.SetConsoleTitleW(f"BoardAgent {__version__}")
    except Exception:
        pass


class ServiceUnavailable(Screen):
    """Full-screen notice when the background service is unreachable."""

    CSS = """
    ServiceUnavailable { align: center middle; }
    #offline-box { width: 66; height: auto; border: solid $error; padding: 2 3; }
    #offline-title { text-style: bold; color: $error; }
    #offline-hint { color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="offline-box"):
            yield Static(
                "SERVICE NOT RUNNING\n\n"
                "Start it with:\n\n"
                "    boardagent-server\n\n"
                "or:\n\n"
                "    python -m boardagent.api",
                id="offline-title",
            )
            yield Static(
                "Press R to retry once it is up.", id="offline-hint"
            )

    BINDINGS = [
        ("r", "retry", "Retry"),
    ]

    async def action_retry(self) -> None:
        await self.app.check_service()  # type: ignore[attr-defined]
        if self.app.service_up:  # type: ignore[attr-defined]
            await self.app.action_refresh()  # type: ignore[attr-defined]
            self.app.pop_screen()  # type: ignore[attr-defined]


class TaskTable(DataTable):
    """Task list table."""

    BINDINGS = [
        ("space", "select_cursor", "Select"),
    ]

    def __init__(self, ai_mode: bool = False, **kwargs) -> None:
        super().__init__(show_cursor=True, cursor_type="row", **kwargs)
        self.ai_mode = ai_mode
        self.zebra_stripes = True


class SettingsSelectOverlay(SelectOverlay):
    """Select overlay where SPACE selects (like enter) and type-to-search is off.

    Textual's stock overlay only binds enter to select; space is swallowed by
    type-to-search. This makes the dropdown fully keyboard-coherent with the
    rest of the TUI (space opens, space/enter selects, arrows move, escape
    dismisses).
    """

    BINDINGS = [
        Binding("down", "cursor_down", "Down", show=False),
        Binding("end", "last", "Last", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "dismiss", "Dismiss menu"),
        Binding("home", "first", "First", show=False),
        Binding("pagedown", "page_down", "Page Down", show=False),
        Binding("pageup", "page_up", "Page Up", show=False),
        Binding("space", "select", "Select"),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(type_to_search=False)


class SettingsSelect(Select, inherit_bindings=False):
    """Select that only opens its overlay on SPACE.

    Textual's Select binds enter/down/up/space to show_overlay, which makes
    arrow-key navigation through the settings page open the dropdown instead
    of moving. inherit_bindings=False (class keyword — a plain class
    attribute gets overwritten by __init_subclass__) drops the base
    bindings entirely.
    """

    BINDINGS = [
        ("space", "show_overlay", "Select"),
    ]

    def compose(self) -> ComposeResult:
        yield SelectCurrent(self.prompt)
        yield SettingsSelectOverlay().data_bind(compact=Select.compact)


class ConfirmScreen(Screen[bool]):
    """Modal yes/no confirmation (Textual 8 removed App.confirm)."""

    CSS = """
    ConfirmScreen { align: center middle; }
    #dialog { width: 60; height: auto; border: solid $primary; padding: 1 2; }
    #dialog-buttons { height: 3; align-horizontal: center; }
    #dialog-buttons Button { margin: 0 2; }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.message)
            with Horizontal(id="dialog-buttons"):
                yield Button("Yes", id="yes")
                yield Button("No", id="no")

    BINDINGS = [
        ("left,up", "app.focus_previous", "Previous"),
        ("right,down", "app.focus_next", "Next"),
    ]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class KeyCaptureScreen(Screen):
    """Modal screen that captures a single keypress for a keybind."""

    CSS = """
    KeyCaptureScreen { align: center middle; }
    #capture-box { width: 60; height: auto; border: solid $primary; padding: 1 2; }
    #capture-hint { color: $text-muted; }
    """

    def __init__(self, action: str, current: str) -> None:
        super().__init__()
        self.action = action
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="capture-box"):
            yield Label(f"[b]Press a key for: {self.action}[/b]")
            yield Static(
                f"Current: {self.current}   (Esc to cancel)", id="capture-hint"
            )

    async def _on_key(self, event) -> None:
        """Intercept keys BEFORE the binding chain runs.

        The screen's handle_key checks the full binding chain (including app
        bindings) — so pressing an already-bound key like 'q' would fire quit
        instead of being captured. Overriding _on_key here swallows every key
        before dispatch_key ever runs.
        """
        key = event.key
        if key in ("escape", "ctrl+c"):
            self.app.pop_screen()
        elif key not in ("enter", "tab"):
            self.app.set_keybind(self.action, key)  # type: ignore[attr-defined]
            self.app.pop_screen()
        event.stop()


class BoardAgentApp(App):
    """Main Textual app."""

    CSS = """
    Screen { align: center middle; }
    TabbedContent { height: 1fr; }
    TabPane { height: 1fr; }
    #task-list { width: 1fr; height: 100%; }
    #detail-panel { width: 44%; height: 100%; border: solid $primary; padding: 1 2; }
    #detail-content { width: 100%; height: auto; }
    #detail-actions { height: 3; padding: 1 0; }
    #settings-panel { width: 100%; height: 100%; padding: 1 2; }
    #settings-panel > * { margin: 1 0; }
    .settings-label { color: $text-muted; }
    .settings-section { text-style: bold; color: $primary; margin-top: 1; }
    .kb-row { height: 3; }
    .kb-row > * { margin: 0 1; }
    .kb-row Static { padding: 0 1; }
    .kb-row Static:hover { background: $boost; text-style: bold; }
    .kb-row Static:focus { background: $primary; color: $background; text-style: bold; }
    Footer { background: $surface; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("a", "toggle_ai", "AI mode"),
        ("c", "create", "Create"),
        ("e", "edit", "Edit"),
        ("d", "delete", "Delete"),
        ("l", "claim", "Claim"),
        ("t", "complete", "Complete"),
    ]

    ai_mode = reactive(False)
    service_up = reactive(True)
    tasks: reactive[list[dict[str, Any]]] = reactive([])
    settings: dict[str, Any]
    selected_task_id: int | None = None
    theme_colors: dict[str, str] = {}

    def __init__(self) -> None:
        self.keybinds = load_keybinds()
        super().__init__()
        # Rebuild the dispatch map from persisted keybinds so overrides apply
        # at startup (Textual builds _bindings from class-level BINDINGS at
        # mount; we replace it with the user's overrides).
        self._rebuild_bindings()
        self.settings = _load_settings()
        # Plain instance attribute. NOTE: must NOT be named current_theme —
        # Textual 8's App base class defines current_theme as a read-only
        # property, so assignment would raise AttributeError.
        self.active_theme = self.settings.get("theme", "amber")
        self.server_settings = load_server_settings()
        self.api_keys: list[dict[str, Any]] = []

    def _rebuild_bindings(self) -> None:
        """Rebuild the key dispatch map from self.keybinds.

        Textual builds _bindings once at mount from the class-level BINDINGS;
        refresh_bindings() only repaints the footer. Rebuilding the
        BindingsMap makes overridden keys dispatch immediately.
        """
        from textual.binding import Binding, BindingsMap

        new_map = BindingsMap()
        for act, k in self.keybinds.items():
            new_map._add_binding(
                Binding(k, act, ACTION_LABELS.get(act, act), show=True)
            )
        # Escape always quits (hard binding, not user-rebindable).
        new_map._add_binding(Binding("escape", "quit", "Quit", show=True))
        # Preserve the command palette binding (added by App at init).
        for key_, binding in self._bindings:
            if binding.action in ("command_palette", "app.command_palette"):
                new_map._add_binding(binding)
        self._bindings = new_map
        self.refresh_bindings()

    async def on_mount(self) -> None:
        self.title = f"BoardAgent {__version__}"
        self._register_themes()
        self.apply_theme(self.active_theme)
        # Apply persisted opacity to the console window immediately.
        _apply_opacity(int(self.settings.get("opacity", 100)))
        # Reactive attributes must be set AFTER compose (on_mount) — their
        # watchers query widgets, which don't exist during __init__.
        self.ai_mode = bool(self.settings.get("ai_mode", False))
        await self.check_service()
        await self.action_refresh()
        asyncio.create_task(self.refresh_settings_tab())
        self.set_interval(10, self.action_refresh)
        # Focus the task table so arrows/enter/space work immediately.
        try:
            self.query_one(TaskTable).focus()
        except Exception:
            pass
        # Key action buttons are mouse affordances; arrows skip them so
        # navigation from the API keys section to keybinds is one press per
        # field, not two per button.
        for bid in ("create-key", "delete-key"):
            try:
                self.query_one(f"#{bid}", Button).can_focus = False
            except Exception:
                pass

    def _register_themes(self) -> None:
        """Register every BoardAgent theme as a Textual Theme."""
        for name in THEME.list():
            colors = THEME.get(name).get("colors", {})
            variables = {
                f"status-{k}": v
                for k, v in colors.items()
                if k.startswith("status-")
            }
            variables.update(
                {
                    f"priority-{k}": v
                    for k, v in colors.items()
                    if k.startswith("priority-")
                }
            )
            theme = Theme(
                name=name,
                primary=colors.get("primary", "#ffbf00"),
                secondary=colors.get("secondary", "#806000"),
                warning=colors.get("priority-yellow", "#ffcc00"),
                error=colors.get("error", "#ff3b30"),
                success=colors.get("success", "#00ff66"),
                accent=colors.get("accent", "#ffea00"),
                foreground=colors.get("foreground", "#ffb000"),
                background=colors.get("background", "#0a0a0a"),
                surface=colors.get("background", "#0a0a0a"),
                panel=colors.get("secondary", "#806000"),
                dark=True,
                variables=variables,
            )
            if name not in self.available_themes:
                self.register_theme(theme)

    def apply_theme(self, name: str) -> None:
        self.theme_colors = THEME.get(name).get("colors", {})
        self.active_theme = name
        if name in self.available_themes:
            self.theme = name

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tasks"):
            with TabPane("Tasks", id="tasks"):
                with Horizontal():
                    # Read from plain settings here, NOT the ai_mode reactive:
                    # first access to a reactive fires its watcher, which would
                    # run populate_table() before TaskTable is mounted.
                    yield TaskTable(
                        ai_mode=bool(self.settings.get("ai_mode", False)),
                        id="task-list",
                    )
                    yield Vertical(
                        Static("Select a task to view details", id="detail-content"),
                        Horizontal(
                            Button("Claim", id="btn-claim", disabled=True),
                            Button("Complete", id="btn-complete", disabled=True),
                            Button("Edit", id="btn-edit", disabled=True),
                            Button("Delete", id="btn-delete", disabled=True),
                            id="detail-actions",
                        ),
                        id="detail-panel",
                    )
            with TabPane("Settings", id="settings"):
                yield VerticalScroll(
                    Label("Theme", classes="settings-label"),
                    SettingsSelect(
                        ((name, name) for name in THEME.list()),
                        value=self.active_theme,
                        id="theme-select",
                        allow_blank=False,
                    ),
                    Label("AI mode (show agent metadata)", classes="settings-label"),
                    Checkbox(
                        "AI mode",
                        value=bool(self.settings.get("ai_mode", False)),
                        id="ai-mode-toggle",
                    ),
                    Label("Opacity (Windows only, requires restart)", classes="settings-label"),
                    Input(
                        value=str(self.settings.get("opacity", 100)),
                        placeholder="0-100",
                        id="opacity-input",
                        restrict=r"(100|[1-9][0-9]?|)",
                        max_length=3,
                    ),
                    Label("Backend", classes="settings-section"),
                    Label("Host", classes="settings-label"),
                    Input(
                        value=str(self.server_settings.get("host", DEFAULT_HOST)),
                        id="server-host",
                    ),
                    Label("Port", classes="settings-label"),
                    Input(
                        value=str(self.server_settings.get("port", DEFAULT_PORT)),
                        id="server-port",
                    ),
                    Checkbox(
                        "API enabled",
                        value=bool(self.server_settings.get("api_enabled", True)),
                        id="api-enabled",
                    ),
                    Checkbox(
                        "MCP enabled",
                        value=bool(self.server_settings.get("mcp_enabled", True)),
                        id="mcp-enabled",
                    ),
                    Label("API Keys", classes="settings-section"),
                    DataTable(id="keys-table", cursor_type="row"),
                    Horizontal(
                        Input(placeholder="Key name", id="key-name"),
                        SettingsSelect(
                            ((r, r) for r in (ROLE_READ, ROLE_WRITE, ROLE_ADMIN)),
                            value=ROLE_READ,
                            id="key-role",
                            allow_blank=False,
                        ),
                        Button("Create Key", id="create-key"),
                        Button("Delete Selected", id="delete-key"),
                    ),
                    Label("Keybinds", classes="settings-section"),
                    *self._keybind_rows(),
                    Button("Save Settings", id="save-settings"),
                    id="settings-panel",
                )
        yield Footer()

    def _keybind_rows(self) -> list[Horizontal]:
        rows: list[Horizontal] = []
        for action, key in self.keybinds.items():
            val = Static(key, id=f"kb-val-{action}", classes="kb-val")
            val.can_focus = True
            change = Button("Change", id=f"kb-{action}")
            # Keyboard users activate a keybind with enter/space on the value;
            # the Change button is a mouse affordance only. Keeping it
            # focusable makes arrow navigation cost two presses per row.
            change.can_focus = False
            rows.append(
                Horizontal(
                    Label(f"{ACTION_LABELS.get(action, action)}", classes="settings-label"),
                    val,
                    change,
                    classes="kb-row",
                )
            )
        return rows

    # ---- service / refresh -------------------------------------------------

    async def check_service(self) -> None:
        try:
            r = await asyncio.to_thread(
                httpx.get, f"{_api_base()}/healthz", timeout=2.0
            )
            self.service_up = r.status_code == 200
        except Exception:
            self.service_up = False

    def watch_service_up(self, up: bool) -> None:
        if up:
            if isinstance(self.screen, ServiceUnavailable):
                self.pop_screen()
        else:
            if not isinstance(self.screen, ServiceUnavailable):
                self.push_screen(ServiceUnavailable())

    def watch_ai_mode(self, ai_mode: bool) -> None:
        try:
            cb = self.query_one("#ai-mode-toggle", Checkbox)
            cb.value = ai_mode
        except Exception:
            pass
        self.populate_table()
        self.notify("AI mode: ON" if ai_mode else "AI mode: OFF")

    async def action_refresh(self) -> None:
        try:
            r = await asyncio.to_thread(
                httpx.get, f"{_api_base()}/tasks", headers=_api_headers(), timeout=5.0
            )
            r.raise_for_status()
            data = r.json()
            self.tasks = data.get("tasks", [])
            self.service_up = True
        except Exception as exc:
            self.service_up = False
            self.notify(f"Refresh failed: {exc}", severity="error", timeout=5)
            self.tasks = []
        self.populate_table()

    # ---- table -------------------------------------------------------------

    def _colored(self, value: str, key: str) -> Text:
        color = self.theme_colors.get(key)
        return Text(value, style=color) if color else Text(value)

    def populate_table(self) -> None:
        """Diff-based, idempotent table rebuild.

        Never clear()+re-add: Textual 8.2.8's clear() leaves stale row/column
        keys, and on_mount can trigger two populates before the first finishes
        (DuplicateKey). Instead: remove rows that no longer exist, keep rows
        that do, add only new ones. Safe to call any number of times, even
        concurrently.
        """
        table = self.query_one(TaskTable)
        new_ids = {str(t["id"]) for t in self.tasks}

        # remove rows no longer present (deleted tasks / filter changes)
        for key in list(table.rows):
            if getattr(key, "value", None) not in new_ids:
                table.remove_row(key)

        # (re)create columns only when the mode's column set changed
        expected = 7 if self.ai_mode else 5
        if len(table.columns) != expected:
            # Removing columns drops the cell data of every existing row
            # (Textual keys cells by column), so rows must be rebuilt too —
            # otherwise the table renders blank rows after a mode toggle.
            for key in list(table.rows):
                table.remove_row(key)
            for col in list(table.columns):
                table.remove_column(getattr(col, "key", col))
            if self.ai_mode:
                table.add_columns("ID", "Title", "Status", "Priority", "Project", "Agent", "Metadata")
            else:
                table.add_columns("Title", "Status", "Priority", "Project", "Due")

        for t in self.tasks:
            key = str(t["id"])
            if key in table.rows:  # already rendered — leave untouched
                continue
            status = t.get("status", "todo")
            priority = t.get("priority", "white")
            if self.ai_mode:
                metadata = json.dumps(t.get("metadata", {}))
                table.add_row(
                    str(t["id"]),
                    t["title"],
                    self._colored(status, f"status-{status}"),
                    self._colored(priority, f"priority-{priority}"),
                    t.get("project") or "",
                    t.get("owner_agent_id") or "",
                    metadata[:60] + "..." if len(metadata) > 60 else metadata,
                    key=key,
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
                    self._colored(status, f"status-{status}"),
                    self._colored(priority, f"priority-{priority}"),
                    t.get("project") or "",
                    due,
                    key=key,
                )

    # ---- actions -----------------------------------------------------------

    def action_toggle_ai(self) -> None:
        self.ai_mode = not self.ai_mode
        self.settings["ai_mode"] = self.ai_mode
        _save_settings(self.settings)

    def action_switch_tab(self) -> None:
        """Tab toggles between the Tasks and Settings tabs."""
        # Debounce: after a Select overlay dismisses, Textual can re-deliver
        # the same key event, firing this action twice and toggling back.
        import time

        now = time.monotonic()
        if now - getattr(self, "_last_tab_switch", 0) < 0.15:
            return
        self._last_tab_switch = now
        try:
            tabs = self.query_one(TabbedContent)
            target = "settings" if tabs.active == "tasks" else "tasks"
            tabs.active = target
            # Move focus into the target tab. If a widget in the hidden pane
            # keeps focus (e.g. a Select after its overlay dismissed), Textual
            # can re-activate the old pane.
            if target == "tasks":
                self.query_one(TaskTable).focus()
            else:
                self.query_one("#theme-select").focus()
        except Exception:
            pass

    def action_focus_next(self) -> None:
        """Tab: switch tabs on the main screen, move focus on modals."""
        if len(self._screen_stack) <= 1:
            self.action_switch_tab()
        else:
            self.screen.focus_next()

    async def action_create(self) -> None:
        if not self.service_up:
            self.notify("Service not running.", severity="error")
            return
        self.push_screen(CreateTaskScreen())

    def _selected_task(self) -> dict[str, Any] | None:
        if self.selected_task_id is None:
            return None
        return next(
            (t for t in self.tasks if str(t["id"]) == str(self.selected_task_id)),
            None,
        )

    async def action_edit(self) -> None:
        task = self._selected_task()
        if task is None:
            self.notify("Select a task first.", severity="warning")
            return
        self.push_screen(EditTaskScreen(task))

    async def action_delete(self) -> None:
        task = self._selected_task()
        if task is None:
            self.notify("Select a task first.", severity="warning")
            return
        self.push_screen(
            ConfirmScreen(f"Delete task #{task['id']} \"{task['title']}\"?"),
            callback=lambda ok: asyncio.create_task(self._delete_task(task, ok)),
        )

    async def _delete_task(self, task: dict[str, Any], confirmed: bool) -> None:
        if not confirmed:
            return
        try:
            r = await asyncio.to_thread(
                httpx.delete,
                f"{_api_base()}/tasks/{task['id']}",
                headers=_api_headers(),
                timeout=5.0,
            )
            r.raise_for_status()
            self.notify("Task deleted")
            self.selected_task_id = None
            await self.action_refresh()
        except Exception as exc:
            self.notify(f"Delete failed: {exc}", severity="error")

    async def action_claim(self) -> None:
        task = self._selected_task()
        if task is None:
            self.notify("Select a task first.", severity="warning")
            return
        try:
            r = await asyncio.to_thread(
                httpx.post,
                f"{_api_base()}/tasks/{task['id']}/claim",
                json={"agent_id": HUMAN_AGENT_ID},
                headers=_api_headers(),
                timeout=5.0,
            )
            r.raise_for_status()
            self.notify(f"Claimed #{task['id']}")
            await self.action_refresh()
        except Exception as exc:
            self.notify(f"Claim failed: {exc}", severity="error")

    async def action_complete(self) -> None:
        task = self._selected_task()
        if task is None:
            self.notify("Select a task first.", severity="warning")
            return
        try:
            r = await asyncio.to_thread(
                httpx.post,
                f"{_api_base()}/tasks/{task['id']}/complete",
                json={"agent_id": HUMAN_AGENT_ID},
                headers=_api_headers(),
                timeout=5.0,
            )
            r.raise_for_status()
            self.notify(f"Completed #{task['id']}")
            await self.action_refresh()
        except Exception as exc:
            self.notify(f"Complete failed: {exc}", severity="error")

    # ---- keybinds ----------------------------------------------------------

    def set_keybind(self, action: str, key: str) -> None:
        self.keybinds[action] = key
        save_keybinds(self.keybinds)
        self._rebuild_bindings()
        try:
            self.query_one(f"#kb-val-{action}", Static).update(key)
        except Exception:
            pass
        self.notify(f"{ACTION_LABELS.get(action, action)}: {key}")

    def _update_detail(self, task: dict[str, Any]) -> None:
        detail = self.query_one("#detail-content", Static)
        colors = self.theme_colors
        status = task.get("status", "todo")
        priority = task.get("priority", "white")
        sc = colors.get(f"status-{status}", "")
        pc = colors.get(f"priority-{priority}", "")
        lines = [
            f"[b]#{task['id']} {task['title']}[/b]\n",
            f"Status: [{sc}]{status}[/]" if sc else f"Status: {status}",
            f"Priority: [{pc}]{priority}[/]" if pc else f"Priority: {priority}",
            f"Project: {task.get('project') or '-'}",
            f"Owner: {task.get('owner_agent_id') or '-'}",
        ]
        if task.get("due"):
            lines.append(f"Due: {task['due']}")
        if task.get("description"):
            lines.append(f"\n{task['description']}")
        if self.ai_mode and task.get("metadata"):
            lines.append("\n[b]Metadata[/b]")
            lines.append(json.dumps(task["metadata"], indent=2, default=str))
        detail.update("\n".join(lines))
        for bid, enabled in (
            ("btn-claim", status == "todo"),
            ("btn-complete", True),
            ("btn-edit", True),
            ("btn-delete", True),
        ):
            self.query_one(f"#{bid}", Button).disabled = not enabled

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Only the task table drives the detail panel. The keys table in
        # Settings is also a DataTable — its row keys are API key strings,
        # not task ids, and int() would crash the app.
        if event.data_table.id != "task-list":
            return
        self.selected_task_id = int(event.row_key.value)
        task = self._selected_task()
        if task is None:
            return
        self._update_detail(task)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Live preview: update the detail panel as the cursor moves."""
        if event.data_table.id != "task-list":
            return
        self.selected_task_id = int(event.row_key.value)
        task = self._selected_task()
        if task is None:
            return
        self._update_detail(task)

    # ---- settings tab ------------------------------------------------------

    async def refresh_settings_tab(self) -> None:
        """Reload server settings + API keys from the server into the widgets."""
        try:
            r = await asyncio.to_thread(
                httpx.get, f"{_api_base()}/settings", headers=_api_headers(), timeout=5.0
            )
            r.raise_for_status()
            data = r.json()
            self.server_settings.update(data)
            self.query_one("#server-host", Input).value = str(data.get("host", DEFAULT_HOST))
            self.query_one("#server-port", Input).value = str(data.get("port", DEFAULT_PORT))
            self.query_one("#api-enabled", Checkbox).value = bool(data.get("api_enabled", True))
            self.query_one("#mcp-enabled", Checkbox).value = bool(data.get("mcp_enabled", True))
        except Exception as exc:
            self.notify(f"Settings load failed: {exc}", severity="error")
        try:
            r = await asyncio.to_thread(
                httpx.get, f"{_api_base()}/keys", headers=_api_headers(), timeout=5.0
            )
            r.raise_for_status()
            self.api_keys = r.json()
            self._render_keys_table()
        except Exception as exc:
            self.notify(f"Keys load failed: {exc}", severity="error")

    def _render_keys_table(self) -> None:
        table = self.query_one("#keys-table", DataTable)
        if len(table.columns) != 3:
            table.add_columns("Name", "Role", "Key")
        for key in list(table.rows):
            table.remove_row(key)
        for k in self.api_keys:
            table.add_row(
                k.get("name", ""),
                k.get("role", ""),
                k.get("key", ""),
                key=k.get("key", ""),
            )

    async def _save_backend_settings(self) -> None:
        try:
            host = self.query_one("#server-host", Input).value.strip() or DEFAULT_HOST
            port = int(self.query_one("#server-port", Input).value.strip() or DEFAULT_PORT)
        except ValueError:
            self.notify("Port must be a number.", severity="error")
            return
        api_enabled = self.query_one("#api-enabled", Checkbox).value
        mcp_enabled = self.query_one("#mcp-enabled", Checkbox).value
        self.server_settings.update(
            {
                "host": host,
                "port": port,
                "api_enabled": bool(api_enabled),
                "mcp_enabled": bool(mcp_enabled),
            }
        )
        save_server_settings(self.server_settings)
        self.notify("Backend settings saved. Restart server to apply.")

    async def _create_key(self) -> None:
        name = self.query_one("#key-name", Input).value.strip()
        role = str(self.query_one("#key-role", Select).value or ROLE_READ)
        if not name:
            self.notify("Key name required.", severity="error")
            return
        try:
            r = await asyncio.to_thread(
                httpx.post,
                f"{_api_base()}/keys",
                json={"name": name, "role": role},
                headers=_api_headers(),
                timeout=5.0,
            )
            r.raise_for_status()
            key = r.json()
            self.notify(f"Key created: {key['key']}")
            self.query_one("#key-name", Input).value = ""
            await self.refresh_settings_tab()
        except Exception as exc:
            self.notify(f"Create key failed: {exc}", severity="error")

    async def _delete_selected_key(self) -> None:
        table = self.query_one("#keys-table", DataTable)
        if table.cursor_row is None:
            self.notify("Select a key first.", severity="warning")
            return
        row_key = table.coordinate_to_cell_key((table.cursor_row, 0))
        key = table.get_row_at(table.cursor_row)[2]
        if not key:
            return
        self.push_screen(
            ConfirmScreen(f"Delete API key {key[:12]}...?"),
            callback=lambda ok: asyncio.create_task(self._delete_key(key, ok)),
        )

    async def _delete_key(self, key: str, confirmed: bool) -> None:
        if not confirmed:
            return
        try:
            r = await asyncio.to_thread(
                httpx.delete,
                f"{_api_base()}/keys/{key}",
                headers=_api_headers(),
                timeout=5.0,
            )
            r.raise_for_status()
            self.notify("Key deleted")
            await self.refresh_settings_tab()
        except Exception as exc:
            self.notify(f"Delete key failed: {exc}", severity="error")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "save-settings":
            theme = self.query_one("#theme-select", Select).value
            opacity = self.query_one("#opacity-input", Input).value
            ai_mode = self.query_one("#ai-mode-toggle", Checkbox).value
            try:
                opacity_int = max(0, min(100, int(opacity)))
            except ValueError:
                opacity_int = 100
            self.settings.update(
                {"theme": str(theme), "opacity": opacity_int, "ai_mode": bool(ai_mode)}
            )
            _save_settings(self.settings)
            self.apply_theme(str(theme))
            self.ai_mode = bool(ai_mode)
            _apply_opacity(opacity_int)
            await self._save_backend_settings()
            self.notify("Settings saved. Opacity applied.")
        elif bid == "btn-claim":
            await self.action_claim()
        elif bid == "btn-complete":
            await self.action_complete()
        elif bid == "btn-edit":
            await self.action_edit()
        elif bid == "btn-delete":
            await self.action_delete()
        elif bid == "create-key":
            await self._create_key()
        elif bid == "delete-key":
            await self._delete_selected_key()
        elif bid and bid.startswith("kb-") and not bid.startswith("kb-val-"):
            action = bid[3:]
            current = self.keybinds.get(action, "")
            self.push_screen(KeyCaptureScreen(action, current))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "theme-select":
            self.apply_theme(str(event.value))
            # Auto-save so closing without pressing Save keeps the theme.
            self.settings["theme"] = str(event.value)
            _save_settings(self.settings)

    def on_input_changed(self, event: Input.Changed) -> None:
        # Auto-save opacity as it's typed (clamped on save).
        if event.input.id == "opacity-input":
            try:
                val = max(0, min(100, int(event.value)))
            except ValueError:
                return
            self.settings["opacity"] = val
            _save_settings(self.settings)

    def on_click(self, event) -> None:
        """Click a keybind value (hover-highlighted) to change it."""
        widget = event.widget
        if widget is None or not widget.id:
            return
        if widget.id.startswith("kb-val-"):
            action = widget.id[len("kb-val-"):]
            current = self.keybinds.get(action, "")
            self.push_screen(KeyCaptureScreen(action, current))

    def on_key(self, event) -> None:
        """Keyboard navigation helpers.

        - Enter/Space on a focused keybind value opens the capture screen.
        - Up/Down inside the Settings tab move focus between fields (Tab is
          reserved for switching tabs).
        """
        focused = self.focused
        if event.key in ("enter", "space"):
            if focused is not None and focused.id and focused.id.startswith("kb-val-"):
                action = focused.id[len("kb-val-"):]
                current = self.keybinds.get(action, "")
                self.push_screen(KeyCaptureScreen(action, current))
                event.stop()
            return
        if event.key in ("up", "down") and len(self._screen_stack) <= 1:
            try:
                tabs = self.query_one(TabbedContent)
                # DataTables (keys table) handle up/down natively for cursor
                # movement — don't hijack them. Also skip when a Select
                # overlay is open (focused widget is the overlay, not a
                # settings field).
                from textual.widgets._select import SelectOverlay

                if (
                    tabs.active == "settings"
                    and focused is not None
                    and not isinstance(focused, SelectOverlay)
                ):
                    # Keys table: let the cursor move within the table, but
                    # when it's at the edge (e.g. a single key row), move
                    # focus out so arrows never feel dead.
                    if isinstance(focused, DataTable) and focused.id == "keys-table":
                        if event.key == "down" and focused.cursor_row < len(focused.rows) - 1:
                            return  # let the table move its cursor
                        if event.key == "up" and focused.cursor_row > 0:
                            return
                        # at the edge — fall through to focus movement
                    elif isinstance(focused, DataTable):
                        return  # other tables keep native behavior
                    if event.key == "down":
                        self.screen.focus_next()
                    else:
                        self.screen.focus_previous()
                    event.stop()
            except Exception:
                pass

    def on_tabbed_content_tab_activated(self, event) -> None:
        if event.tab.id == "settings":
            asyncio.create_task(self.refresh_settings_tab())


class ArrowNavScreen(Screen):
    """Modal screens navigate fields with arrows, not just Tab.

    Up/Down move focus between fields; Tab still works as a fallback.
    When a Select overlay is open, the overlay handles up/down itself
    (option navigation) before these bindings are reached.
    """

    BINDINGS = [
        ("up", "app.focus_previous", "Previous"),
        ("down", "app.focus_next", "Next"),
    ]


class CreateTaskScreen(ArrowNavScreen):
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
            yield SettingsSelect(
                ((p, p) for p in ("white", "blue", "green", "yellow", "orange", "red")),
                value="white",
                id="priority",
                allow_blank=False,
            )
            yield Button("Create", id="create")
            yield Button("Cancel", id="cancel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.app.pop_screen()
            return
        title = self.query_one("#title", Input).value
        project = self.query_one("#project", Input).value
        priority = self.query_one("#priority", Select).value or "white"
        if not title:
            self.app.notify("Title required", severity="error")
            return
        try:
            r = await asyncio.to_thread(
                httpx.post,
                f"{_api_base()}/tasks",
                json={"title": title, "project": project, "priority": priority},
                headers=_api_headers(),
                timeout=5.0,
            )
            r.raise_for_status()
            self.app.notify("Task created")
            await self.app.action_refresh()
            self.app.pop_screen()
        except Exception as exc:
            self.app.notify(f"Create failed: {exc}", severity="error")


class EditTaskScreen(ArrowNavScreen):
    """Modal screen to edit a task's human fields."""

    CSS = """
    EditTaskScreen { align: center middle; }
    #dialog { width: 60; height: auto; border: solid $primary; padding: 1 2; }
    """

    def __init__(self, task: dict[str, Any]) -> None:
        super().__init__()
        # NOTE: cannot be named self.task — Textual's Screen base class
        # defines `task` as a read-only property (message pump bookkeeping).
        self.task_data = task

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"[b]Edit #{self.task_data['id']}[/b]")
            yield Input(value=self.task_data.get("title", ""), id="title")
            yield Input(value=self.task_data.get("project") or "", id="project")
            yield SettingsSelect(
                ((p, p) for p in ("white", "blue", "green", "yellow", "orange", "red")),
                value=self.task_data.get("priority", "white"),
                id="priority",
                allow_blank=False,
            )
            yield Button("Save", id="save")
            yield Button("Cancel", id="cancel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.app.pop_screen()
            return
        title = self.query_one("#title", Input).value
        project = self.query_one("#project", Input).value
        priority = self.query_one("#priority", Select).value or "white"
        if not title:
            self.app.notify("Title required", severity="error")
            return
        try:
            r = await asyncio.to_thread(
                httpx.patch,
                f"{_api_base()}/tasks/{self.task_data['id']}",
                json={"title": title, "project": project, "priority": priority},
                headers=_api_headers(),
                timeout=5.0,
            )
            r.raise_for_status()
            self.app.notify("Task updated")
            await self.app.action_refresh()
            self.app.pop_screen()
        except Exception as exc:
            self.app.notify(f"Update failed: {exc}", severity="error")


def main() -> None:
    app = BoardAgentApp()
    app.run()


if __name__ == "__main__":
    main()
