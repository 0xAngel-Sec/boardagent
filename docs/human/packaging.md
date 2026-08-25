# Packaging & Windows Service Notes

## Console scripts

After `pip install -e .`:

- `taskmanager-server` — background REST + MCP server.
- `taskmanager` — foreground Textual UI.
- `taskmanager-mcp` — MCP stdio server (for plugging into hosts).

## Windows background service options

TaskManager is a plain Python process. You can keep it running with any of these:

1. **Task Scheduler** — create a basic task that runs `taskmanager-server` at logon, hidden.
2. **WinSW / nssm** — wrap the console exe as a Windows service.
3. **Run manually** during the day; the DB is durable and the UI reconnects.

## PyInstaller (optional)

Build scripts are provided as examples only; they are not pre-built:

- `scripts/build_server_exe.bat`
- `scripts/build_tui_exe.bat`
- `scripts/build_mcp_exe.bat`

Example for the server:

```bat
pyinstaller --onefile --name taskmanager-server taskmanager/api.py
```

Note: Textual apps packaged with PyInstaller may need `--collect-all textual` and platform-specific console handling.
