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
    TextArea,
)
from textual.widgets._select import SelectCurrent, SelectOverlay

from . import __version__
from .config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ROLE_ADMIN,
    ROLE_READ,
    ROLE_WRITE,
    _atomic_write,
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


def _mask_key(key: str) -> str:
    """Mask an API key for display: first 3 + last 3 chars only.

    The full key never appears in the table; the only place it is ever
    shown is the post-creation modal, and only until you click away.
    """
    if len(key) <= 6:
        return key
    return f"{key[:3]}…{key[-3:]}"


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to the Windows clipboard (clip.exe reads stdin)."""
    import subprocess

    try:
        subprocess.run(
            ["clip"], input=text.encode("utf-8"), check=True, timeout=5
        )
        return True
    except Exception:
        return False


def _load_settings() -> dict[str, Any]:
    path = settings_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"opacity": 100, "theme": "amber", "ai_mode": False}


def _save_settings(settings: dict[str, Any]) -> None:
    """Persist settings, merging over the existing file.

    A bare overwrite would clobber co-existing keys (console_key, keybinds)
    written by other writers — causing console-key churn and keybind loss.
    """
    path = settings_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(settings)
    _atomic_write(path, json.dumps(data, indent=2))


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


class KeysTable(DataTable):
    """API-keys table: keyboard-first, with a PIN that survives navigation.

    Terminal-style model:
    - The CURSOR row (bright block) is the navigation highlight — arrows
      move it freely to browse the list.
    - SPACE or ENTER pins the row under the cursor. The pin is marked
      with a '>' prefix and is what Delete Selected acts on. Arrows NEVER
      move the pin, so you can pin a key, arrow up to the Delete button,
      and the right key stays selected. (This was the bug: delete hit the
      row the cursor had crawled to — usually the top — instead of the
      key you wanted.)
    - 'd' deletes the pinned key directly, no button travel needed.
    - Clicks select natively (cursor moves); hover is fully dead.

    Cursor-placement fight: when focus ENTERS this table via an arrow key,
    Textual re-dispatches that same key to the newly-focused table (its
    action_cursor_up/down runs one more time). A one-shot pending_entry
    flag swallows the re-delivered key and pins the cursor to the entry
    edge (bottom for up-entry, top for down-entry), so it can't land on a
    "second-lowest" row.
    """

    BINDINGS = [
        ("space", "pin", "Pin"),
        ("enter", "pin", "Pin"),
        ("d", "delete_pinned", "Delete"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pending_entry: tuple[str, float] | None = None
        self.pinned_key: str | None = None

    def _on_mouse_move(self, event) -> None:
        """Mouse hover is intentionally DEAD for the keys table.

        No preview highlight, no cursor movement. Textual's stock hover
        paint made the mouse-following row look like a selection while the
        real selection (the cursor or the pin) sat elsewhere. The mouse
        should not imply anything.
        """

    def action_pin(self) -> None:
        """Pin the row under the cursor (toggle).

        The pin is what Delete Selected acts on; arrows never move it.
        Toggling unpins the row. The '>' marker is painted in place so a
        full table re-render (and its app-data dependency) is not needed.
        """
        if self.cursor_row is None or not self.row_count:
            return
        key = self.get_row_at(self.cursor_row)[2]
        if self.pinned_key == key:
            self.pinned_key = None
            self._set_pin_marker(key, False)
            return
        if self.pinned_key:
            self._set_pin_marker(self.pinned_key, False)
        self.pinned_key = key
        self._set_pin_marker(key, True)

    def _set_pin_marker(self, key: str, pinned: bool) -> None:
        """Prefix/unprefix '>' on the row with the given key."""
        try:
            if not self.ordered_columns:
                return
            name_col = self.ordered_columns[0].key  # the "Name" column
            for row_key in self.rows:
                if getattr(row_key, "value", None) == key:
                    name = str(self.get_row(row_key)[0])
                    if name.startswith("> "):
                        name = name[2:]
                    self.update_cell(
                        row_key, name_col, ("> " if pinned else "") + name
                    )
                    break
        except Exception as exc:
            import traceback

            traceback.print_exc()

    async def action_delete_pinned(self) -> None:
        """Delete the pinned key (or the cursor row as fallback)."""
        app = getattr(self, "app", None)
        if app is not None and hasattr(app, "_delete_selected_key"):
            await app._delete_selected_key()

    def action_cursor_up(self) -> None:
        if self.pending_entry:
            direction, ts = self.pending_entry
            self.pending_entry = None
            if direction == "up" and time.monotonic() - ts < 0.3:
                if self.row_count:
                    self.move_cursor(row=self.row_count - 1, column=0)
                return
        if self.cursor_row is None or self.cursor_row > 0 or not self.row_count:
            super().action_cursor_up()
        else:
            self.screen.focus_previous()

    def action_cursor_down(self) -> None:
        if self.pending_entry:
            direction, ts = self.pending_entry
            self.pending_entry = None
            if direction == "down" and time.monotonic() - ts < 0.3:
                if self.row_count:
                    self.move_cursor(row=0, column=0)
                return
        if self.cursor_row is None or self.cursor_row < self.row_count - 1:
            super().action_cursor_down()
        else:
            self.screen.focus_next()

    def _on_click(self, event) -> None:
        self.pending_entry = None
        return super()._on_click(event)


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
        ("escape", "dismiss_no", "Cancel"),
    ]

    def action_dismiss_no(self) -> None:
        self.dismiss(False)

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
    .keys-actions { height: 3; }
    .keys-actions > * { margin: 0 1; }
    .kb-row { height: 3; }
    .kb-row > * { margin: 0 1; }
    .kb-row Static { padding: 0 1; }
    .kb-row Static:hover { background: $boost; text-style: bold; }
    .kb-row Static:focus { background: $primary; color: $background; text-style: bold; }
    Footer { background: $surface; }
    .keys-actions Button:focus { background: $primary; color: $background; text-style: bold; }
    /* The keys table's selected row must be obvious even when the table
       isn't focused (after hovering/clicking), otherwise users can't tell
       which key is selected. Textual only draws a strong cursor on :focus. */
    #keys-table > .datatable--cursor {
        background: $primary;
        color: $background;
        text-style: bold;
    }
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
        # Footer is hidden while the Settings tab is active (it is a
        # distraction there — you are already in the settings).
        self._update_footer_visibility()
        # Focus the task table so arrows/enter/space work immediately.
        try:
            self.query_one(TaskTable).focus()
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
            # Footer: Textual's default footer variables are a dim blue
            # (#0178D4) no matter the theme, so the key hints render almost
            # unreadable on dark themes like matrix. Derive the footer from
            # the theme's own tokens so the keybinds stay bright.
            variables["footer-background"] = colors.get("background", "#0a0a0a")
            variables["footer-foreground"] = colors.get("foreground", "#ffb000")
            variables["footer-key-background"] = colors.get("secondary", "#806000")
            variables["footer-key-foreground"] = colors.get("foreground", "#ffb000")
            variables["footer-description-foreground"] = colors.get("foreground", "#ffb000")
            variables["footer-description-background"] = colors.get("background", "#0a0a0a")
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
                    Horizontal(
                        Button("Create Key", id="create-key"),
                        Button("Delete Selected", id="delete-key"),
                        classes="keys-actions",
                    ),
                    KeysTable(id="keys-table", cursor_type="row"),
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
        expected = 14 if self.ai_mode else 8
        if len(table.columns) != expected:
            # Removing columns drops the cell data of every existing row
            # (Textual keys cells by column), so rows must be rebuilt too —
            # otherwise the table renders blank rows after a mode toggle.
            for key in list(table.rows):
                table.remove_row(key)
            for col in list(table.columns):
                table.remove_column(getattr(col, "key", col))
            if self.ai_mode:
                table.add_columns("ID", "Title", "Description", "Tags", "Estimate", "Links", "Acceptance", "Dependencies", "Notes", "Status", "Priority", "Project", "Agent", "Metadata")
            else:
                table.add_columns("Title", "Description", "Tags", "Estimate", "Status", "Priority", "Project", "Due")

        for t in self.tasks:
            key = str(t["id"])
            if key in table.rows:  # already rendered — leave untouched
                continue
            status = t.get("status", "todo")
            priority = t.get("priority", "white")
            desc = (t.get("description") or "").replace("\n", " ")[:40]
            tags = ", ".join(t.get("tags") or [])[:30]
            estimate = t.get("estimate") or ""
            if self.ai_mode:
                metadata = json.dumps(t.get("metadata", {}))
                links = ", ".join(t.get("links") or [])[:40]
                acceptance = (t.get("acceptance_criteria") or "").replace("\n", " ")[:40]
                deps = ", ".join(t.get("dependencies") or [])[:40]
                notes = "; ".join(t.get("notes") or [])[:40]
                table.add_row(
                    str(t["id"]),
                    t["title"],
                    desc,
                    tags,
                    estimate,
                    links,
                    acceptance,
                    deps,
                    notes,
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
                    desc,
                    tags,
                    estimate,
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
        """Tab cycles Tasks -> Settings -> Tasks."""
        # Debounce: after a Select overlay dismisses, Textual can re-deliver
        # the same key event, firing this action twice and toggling back.
        import time

        now = time.monotonic()
        if now - getattr(self, "_last_tab_switch", 0) < 0.15:
            return
        self._last_tab_switch = now
        try:
            tabs = self.query_one(TabbedContent)
            order = ["tasks", "settings"]
            target = order[(order.index(tabs.active) + 1) % len(order)]
            tabs.active = target
            # Move focus into the target tab. If a widget in the hidden pane
            # keeps focus (e.g. a Select after its overlay dismissed), Textual
            # can re-activate the old pane.
            if target == "tasks":
                self.query_one(TaskTable).focus()
            else:
                self.query_one("#theme-select").focus()
            self._update_footer_visibility()
        except Exception:
            pass

    def _update_footer_visibility(self) -> None:
        """Hide the footer while on the Settings tab.

        The footer's key hints (q/r/a/c/e/d/l/t) only make sense on the
        Tasks tab. Inside Settings it is noise, so it disappears.
        """
        try:
            tabs = self.query_one(TabbedContent)
            footer = self.query_one(Footer)
            footer.display = tabs.active == "tasks"
        except Exception:
            pass

    def _on_settings_tab(self) -> bool:
        """True when the Settings tab is currently active."""
        try:
            tabs = self.query_one(TabbedContent)
            return tabs.active == "settings"
        except Exception:
            return False

    def _guard_task_actions(self) -> bool:
        """Block task create/edit/delete/claim/complete outside the Tasks tab.

        Returning True means the caller should abort. Users only manage
        tasks from the Tasks tab — acting from Settings would be surprising.
        """
        if self._on_settings_tab():
            self.notify("Switch to the Tasks tab to manage tasks.", severity="warning")
            return True
        return False

    def action_focus_next(self) -> None:
        """Tab: switch tabs on the main screen, move focus on modals."""
        if len(self._screen_stack) <= 1:
            self.action_switch_tab()
        else:
            self.screen.focus_next()

    async def action_create(self) -> None:
        if self._guard_task_actions():
            return
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
        if self._guard_task_actions():
            return
        task = self._selected_task()
        if task is None:
            self.notify("Select a task first.", severity="warning")
            return
        self.push_screen(EditTaskScreen(task))

    async def action_delete(self) -> None:
        if self._guard_task_actions():
            return
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
        if self._guard_task_actions():
            return
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
        if self._guard_task_actions():
            return
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
        if task.get("estimate"):
            lines.append(f"Estimate: {task['estimate']}")
        tags = task.get("tags") or []
        if tags:
            lines.append(f"Tags: {', '.join(tags)}")
        links = task.get("links") or []
        if links:
            lines.append(f"Links: {', '.join(links)}")
        deps = task.get("dependencies") or []
        if deps:
            lines.append(f"Dependencies: {', '.join(deps)}")
        if task.get("description"):
            lines.append(f"\n{task['description']}")
        if task.get("acceptance_criteria"):
            lines.append(f"\n[b]Acceptance criteria[/b]\n{task['acceptance_criteria']}")
        notes = task.get("notes") or []
        if notes:
            lines.append("\n[b]Notes[/b]")
            for n in notes:
                lines.append(f"- {n}")
        custom = task.get("custom_fields") or {}
        if custom:
            lines.append("\n[b]Custom fields[/b]")
            for k, v in custom.items():
                lines.append(f"{k}: {v}")
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
        # Preserve the selected key across re-renders. remove_row()/add_row()
        # otherwise reset the cursor to the top row, so a selection the user
        # just made visibly disappears on the next refresh (tab re-activation,
        # after creating a key, etc.).
        selected_key = None
        if table.cursor_row is not None and table.row_count:
            row_key = list(table.rows)[table.cursor_row]
            selected_key = getattr(row_key, "value", None)
        pinned = getattr(table, "pinned_key", None)
        for key in list(table.rows):
            table.remove_row(key)
        for k in self.api_keys:
            name = k.get("name", "")
            # '>' marks the pinned key — the one Delete Selected acts on.
            if pinned and k.get("key") == pinned:
                name = f"> {name}"
            table.add_row(
                name,
                k.get("role", ""),
                _mask_key(k.get("key", "")),
                key=k.get("key", ""),
            )
        # If the pinned key still exists, put the cursor on it (the pin and
        # cursor converge); otherwise clear the pin (it was deleted).
        if pinned and pinned not in {k.get("key") for k in self.api_keys}:
            table.pinned_key = None
        if selected_key is not None:
            for idx, row_key in enumerate(table.rows):
                if getattr(row_key, "value", None) == selected_key:
                    table.move_cursor(row=idx, column=0)
                    break

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

    async def _delete_selected_key(self) -> None:
        table = self.query_one("#keys-table", DataTable)
        # The PIN is the selection: deleting acts on the pinned key even if
        # the cursor has been arrowed elsewhere (e.g. up to the Delete
        # button). Falls back to the cursor row when nothing is pinned.
        # Row keys carry the FULL key (the cell shows only the mask).
        key = None
        if getattr(table, "pinned_key", None):
            key = table.pinned_key
        elif table.cursor_row is not None and table.row_count:
            row_key = list(table.rows)[table.cursor_row]
            key = getattr(row_key, "value", None)
        if not key:
            self.notify("Select a key first.", severity="warning")
            return
        self.push_screen(
            ConfirmScreen(f"Delete API key {_mask_key(key)}?"),
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
            self.push_screen(CreateKeyScreen())
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
        - Left/Right between the Create Key / Delete Selected buttons move
          focus within the button group (up/down move focus between rows).
        """
        focused = self.focused
        if event.key in ("enter", "space"):
            # Textual's Button only binds enter -> press; space needs a nudge
            # here. (Enter on a Button is consumed by the button's own
            # binding before this handler runs, so this only fires for space.)
            if focused is not None:
                if isinstance(focused, Button) and event.key == "space":
                    focused.press()
                    event.stop()
                    return
                if focused.id and focused.id.startswith("kb-val-"):
                    action = focused.id[len("kb-val-"):]
                    current = self.keybinds.get(action, "")
                    self.push_screen(KeyCaptureScreen(action, current))
                    event.stop()
            return
        if event.key in ("left", "right") and len(self._screen_stack) <= 1:
            # Create Key / Delete Selected form a left/right button group:
            # up/down leave the group, left/right switch between the two.
            if focused is not None and focused.id in ("create-key", "delete-key"):
                target = "delete-key" if focused.id == "create-key" else "create-key"
                try:
                    self.query_one(f"#{target}").focus()
                    event.stop()
                except Exception:
                    pass
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
                    # The Create Key / Delete Selected buttons form a
                    # left/right group: up/down LEAVE the group to the
                    # sections above/below instead of moving between the
                    # two buttons (left/right does that).
                    if isinstance(focused, Button) and focused.id in (
                        "create-key",
                        "delete-key",
                    ):
                        if event.key == "down":
                            table = self.query_one("#keys-table")
                            # focus() is deferred (call_later), so the key is
                            # NOT re-dispatched to the table — move the cursor
                            # explicitly. pending_entry is a safety net.
                            table.pending_entry = ("down", time.monotonic())
                            if table.row_count:
                                table.move_cursor(row=0, column=0)
                            table.focus()
                        else:
                            self.query_one("#mcp-enabled").focus()
                        event.stop()
                        return
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
                        target = self.screen.focus_next()
                    else:
                        target = self.screen.focus_previous()
                    # Entering the keys table must place the cursor
                    # deterministically, otherwise it keeps whatever stale row
                    # it had (moving up from the keybinds used to land on the
                    # second-lowest key instead of the lowest). Down from
                    # above -> top row; up from below -> bottom row. The
                    # table's pending_entry swallows the re-dispatched arrow
                    # key and pins the edge row (see KeysTable docstring).
                    if target is not None and target.id == "keys-table":
                        target.pending_entry = (event.key, time.monotonic())
                    event.stop()
            except Exception:
                pass

    def on_tabbed_content_tab_activated(self, event) -> None:
        self._update_footer_visibility()
        if event.tab.id == "settings":
            asyncio.create_task(self.refresh_settings_tab())


class ArrowNavTextArea(TextArea):
    """TextArea where arrows exit to the next/previous field at the edges.

    Stock Textual TextArea swallows up/down for text-cursor movement, so a
    modal form can get stuck on a multi-line field (down keeps moving the
    text cursor, never the focus). When the cursor is on the first/last
    line, up/down moves FOCUS to the next field instead — matching the
    app's arrow-driven navigation. Mid-document, arrows still edit text.
    """

    def action_cursor_up(self, select: bool = False) -> None:
        if self.cursor_at_first_line:
            self.screen.focus_previous()
            return
        super().action_cursor_up(select)

    def action_cursor_down(self, select: bool = False) -> None:
        if self.cursor_at_last_line:
            self.screen.focus_next()
            return
        super().action_cursor_down(select)


class ArrowNavScreen(Screen):
    """Modal screens navigate fields with arrows, not just Tab.

    Up/Down move focus between fields; Tab still works as a fallback.
    When a Select overlay is open, the overlay handles up/down itself
    (option navigation) before these bindings are reached.
    Escape dismisses the modal (the app-level Escape→quit is shadowed
    while a modal is up, so a stray Escape can't kill the app mid-form).
    """

    BINDINGS = [
        ("up", "app.focus_previous", "Previous"),
        ("down", "app.focus_next", "Next"),
        ("escape", "dismiss_modal", "Cancel"),
    ]

    def action_dismiss_modal(self) -> None:
        self.app.pop_screen()


class CreateKeyScreen(ArrowNavScreen):
    """Modal screen to create an API key with a role."""

    CSS = """
    CreateKeyScreen { align: center middle; }
    #dialog { width: 62; height: auto; border: solid $primary; padding: 1 2; }
    #role-hint { color: $text-muted; height: auto; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[b]Create API Key[/b]")
            yield Label("Name", classes="settings-label")
            yield Input(placeholder="e.g. agent-1", id="key-name")
            yield Label("Role", classes="settings-label")
            yield SettingsSelect(
                (
                    (ROLE_READ, ROLE_READ),
                    (ROLE_WRITE, ROLE_WRITE),
                    (ROLE_ADMIN, ROLE_ADMIN),
                ),
                value=ROLE_READ,
                id="key-role",
                allow_blank=False,
            )
            yield Label(
                "[dim]read — list & view tasks only\n"
                "write — create, edit, complete, claim\n"
                "admin — everything, including deleting tasks & keys[/dim]",
                id="role-hint",
            )
            yield Button("Create", id="create")
            yield Button("Cancel", id="cancel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.app.pop_screen()
            return
        name = self.query_one("#key-name", Input).value.strip()
        role = str(self.query_one("#key-role", Select).value or ROLE_READ)
        if not name:
            self.app.notify("Key name required.", severity="error")
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
            self.app.pop_screen()
            self.app.push_screen(KeyCreatedScreen(key["key"], key.get("name", "")))
            await self.app.refresh_settings_tab()
        except Exception as exc:
            self.app.notify(f"Create key failed: {exc}", severity="error")


class KeyCreatedScreen(ArrowNavScreen):
    """One-time full-key reveal after creation.

    The full key is shown ONLY here, and only until you dismiss or click
    away — after that it is masked in the table (first 3 + last 3 chars)
    and unrecoverable from the UI. Copy it now or lose it.
    """

    CSS = """
    KeyCreatedScreen { align: center middle; }
    #dialog { width: 72; height: auto; border: solid $primary; padding: 1 2; }
    #key-value { padding: 1 1; background: $boost; border: round $primary; }
    #key-hint { color: $text-muted; }
    """

    def __init__(self, key: str, key_name: str) -> None:
        super().__init__()
        self.key = key
        self.key_name = key_name

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"[b]Key created: {self.key_name}[/b]")
            yield Label("This is the ONLY time the full key is shown.", classes="settings-label")
            yield Static(self.key, id="key-value")
            yield Label(
                "[dim]After you close this window the key is masked\n"
                "(first 3 … last 3 characters) and cannot be\n"
                "recovered from the UI. Copy it now or delete\n"
                "the key and create a new one.[/dim]",
                id="key-hint",
            )
            with Horizontal(id="field-actions"):
                yield Button("Copy to Clipboard", id="copy")
                yield Button("Done", id="done")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy":
            if _copy_to_clipboard(self.key):
                self.app.notify("Key copied to clipboard")
            else:
                self.app.notify("Copy failed — select the key text manually", severity="error")
        else:
            self.app.pop_screen()

    def _on_key(self, event) -> None:
        """Enter/Escape dismiss the reveal (space must keep working for
        activating the Copy button)."""
        if event.key in ("enter", "escape"):
            self.app.pop_screen()
            event.stop()
        else:
            super()._on_key(event)


class CreateTaskScreen(ArrowNavScreen):
    """Modal screen to add a task quickly."""

    CSS = """
    CreateTaskScreen { align: center middle; }
    #dialog { width: 68; height: auto; max-height: 100%; overflow-y: auto; border: solid $primary; padding: 1 2; }
    .field-row { height: 3; }
    .field-row > * { margin: 0 1; }
    .field-row Input { width: 1fr; }
    .field-row Input:first-child { width: 30; }
    #field-actions { height: 3; }
    #field-actions Button { margin: 0 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[b]Create Task[/b]")
            yield Input(placeholder="Title", id="title")
            yield Input(placeholder="Project", id="project")
            yield Label("Description", classes="settings-label")
            yield ArrowNavTextArea("", id="task-desc")
            yield Label("Tags (comma separated)", classes="settings-label")
            yield Input(placeholder="e.g. urgent, backend", id="tags")
            yield Label("Estimate", classes="settings-label")
            yield Input(placeholder="e.g. 2h, 1d", id="estimate")
            yield Label("Links (comma separated)", classes="settings-label")
            yield Input(placeholder="https://... or C:\\path\\to\\file", id="links")
            yield Label("Acceptance criteria", classes="settings-label")
            yield ArrowNavTextArea("", id="acceptance")
            yield Label("Dependencies (comma separated)", classes="settings-label")
            yield Input(placeholder="e.g. #12, #34 or task names", id="dependencies")
            yield Label("Notes (one per line)", classes="settings-label")
            yield ArrowNavTextArea("", id="notes")
            yield SettingsSelect(
                ((p, p) for p in ("white", "blue", "green", "yellow", "orange", "red")),
                value="white",
                id="priority",
                allow_blank=False,
            )
            yield Label("Custom fields (name = value)", classes="settings-label")
            with Vertical(id="field-list"):
                pass
            with Horizontal(id="field-actions"):
                yield Button("Add Field", id="field-add")
                yield Button("Remove Field", id="field-remove")
            yield Button("Create", id="create")
            yield Button("Cancel", id="cancel")

    def _field_row(self, name: str = "", value: str = "") -> Horizontal:
        return Horizontal(
            Input(value=name, placeholder="field name", classes="field-name"),
            Input(value=value, placeholder="value", classes="field-value"),
            classes="field-row",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.app.pop_screen()
        elif event.button.id == "field-add":
            self.query_one("#field-list").mount(self._field_row())
            self.query_one("#field-list").query(Input).last.focus()
        elif event.button.id == "field-remove":
            rows = list(self.query_one("#field-list").query(".field-row"))
            if not rows:
                return
            rows[-1].remove()
        elif event.button.id == "create":
            asyncio.create_task(self._create())

    def _collect_custom_fields(self) -> dict[str, str]:
        fields: dict[str, str] = {}
        for row in self.query_one("#field-list").query(".field-row"):
            inputs = row.query(Input)
            fname = inputs[0].value.strip()
            if fname:
                fields[fname] = inputs[1].value
        return fields

    async def _create(self) -> None:
        title = self.query_one("#title", Input).value
        project = self.query_one("#project", Input).value
        description = self.query_one("#task-desc", TextArea).text
        priority = self.query_one("#priority", Select).value or "white"
        tags = [t.strip() for t in self.query_one("#tags", Input).value.split(",") if t.strip()]
        estimate = self.query_one("#estimate", Input).value.strip()
        links = [l.strip() for l in self.query_one("#links", Input).value.split(",") if l.strip()]
        acceptance = self.query_one("#acceptance", TextArea).text
        deps = [d.strip() for d in self.query_one("#dependencies", Input).value.split(",") if d.strip()]
        notes = [n.strip() for n in self.query_one("#notes", TextArea).text.splitlines() if n.strip()]
        custom_fields = self._collect_custom_fields()
        if not title:
            self.app.notify("Title required", severity="error")
            return
        try:
            r = await asyncio.to_thread(
                httpx.post,
                f"{_api_base()}/tasks",
                json={
                    "title": title,
                    "project": project,
                    "priority": priority,
                    "description": description,
                    "tags": tags,
                    "estimate": estimate,
                    "links": links,
                    "acceptance_criteria": acceptance,
                    "dependencies": deps,
                    "notes": notes,
                    "custom_fields": custom_fields,
                },
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
    #dialog { width: 68; height: auto; max-height: 100%; overflow-y: auto; border: solid $primary; padding: 1 2; }
    .field-row { height: 3; }
    .field-row > * { margin: 0 1; }
    .field-row Input { width: 1fr; }
    .field-row Input:first-child { width: 30; }
    #field-actions { height: 3; }
    #field-actions Button { margin: 0 1; }
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
            yield Label("Description", classes="settings-label")
            yield ArrowNavTextArea(self.task_data.get("description") or "", id="task-desc")
            yield Label("Tags (comma separated)", classes="settings-label")
            yield Input(
                value=", ".join(self.task_data.get("tags") or []),
                id="tags",
            )
            yield Label("Estimate", classes="settings-label")
            yield Input(value=self.task_data.get("estimate") or "", id="estimate")
            yield Label("Links (comma separated)", classes="settings-label")
            yield Input(
                value=", ".join(self.task_data.get("links") or []),
                id="links",
            )
            yield Label("Acceptance criteria", classes="settings-label")
            yield ArrowNavTextArea(self.task_data.get("acceptance_criteria") or "", id="acceptance")
            yield Label("Dependencies (comma separated)", classes="settings-label")
            yield Input(
                value=", ".join(self.task_data.get("dependencies") or []),
                id="dependencies",
            )
            yield Label("Notes (one per line)", classes="settings-label")
            yield ArrowNavTextArea("\n".join(self.task_data.get("notes") or []), id="notes")
            yield SettingsSelect(
                ((p, p) for p in ("white", "blue", "green", "yellow", "orange", "red")),
                value=self.task_data.get("priority", "white"),
                id="priority",
                allow_blank=False,
            )
            yield Label("Custom fields (name = value)", classes="settings-label")
            with Vertical(id="field-list"):
                for name, value in (self.task_data.get("custom_fields") or {}).items():
                    yield self._field_row(name, value)
            with Horizontal(id="field-actions"):
                yield Button("Add Field", id="field-add")
                yield Button("Remove Field", id="field-remove")
            yield Button("Save", id="save")
            yield Button("Cancel", id="cancel")

    def _field_row(self, name: str = "", value: str = "") -> Horizontal:
        return Horizontal(
            Input(value=name, placeholder="field name", classes="field-name"),
            Input(value=value, placeholder="value", classes="field-value"),
            classes="field-row",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.app.pop_screen()
        elif event.button.id == "field-add":
            self.query_one("#field-list").mount(self._field_row())
            self.query_one("#field-list").query(Input).last.focus()
        elif event.button.id == "field-remove":
            rows = list(self.query_one("#field-list").query(".field-row"))
            if not rows:
                return
            rows[-1].remove()
        elif event.button.id == "save":
            asyncio.create_task(self._save())

    def _collect_custom_fields(self) -> dict[str, str]:
        fields: dict[str, str] = {}
        for row in self.query_one("#field-list").query(".field-row"):
            inputs = row.query(Input)
            fname = inputs[0].value.strip()
            if fname:
                fields[fname] = inputs[1].value
        return fields

    async def _save(self) -> None:
        title = self.query_one("#title", Input).value
        project = self.query_one("#project", Input).value
        description = self.query_one("#task-desc", TextArea).text
        priority = self.query_one("#priority", Select).value or "white"
        tags = [t.strip() for t in self.query_one("#tags", Input).value.split(",") if t.strip()]
        estimate = self.query_one("#estimate", Input).value.strip()
        links = [l.strip() for l in self.query_one("#links", Input).value.split(",") if l.strip()]
        acceptance = self.query_one("#acceptance", TextArea).text
        deps = [d.strip() for d in self.query_one("#dependencies", Input).value.split(",") if d.strip()]
        notes = [n.strip() for n in self.query_one("#notes", TextArea).text.splitlines() if n.strip()]
        custom_fields = self._collect_custom_fields()
        if not title:
            self.app.notify("Title required", severity="error")
            return
        try:
            r = await asyncio.to_thread(
                httpx.patch,
                f"{_api_base()}/tasks/{self.task_data['id']}",
                json={
                    "title": title,
                    "project": project,
                    "priority": priority,
                    "description": description,
                    "tags": tags,
                    "estimate": estimate,
                    "links": links,
                    "acceptance_criteria": acceptance,
                    "dependencies": deps,
                    "notes": notes,
                    "custom_fields": custom_fields,
                },
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
