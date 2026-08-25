# Packaging & Windows Service Notes

## Console scripts

After `pip install -e .`:

- `boardagent-server` — background REST + MCP server.
- `boardagent` — foreground Textual UI.
- `boardagent-mcp` — MCP stdio server (for plugging into hosts).

## Windows executables (no Python needed)

Prebuilt one-file exes live in `dist/` (or build them yourself):

```bash
python scripts/build_exes.py
```

- `boardagent-server.exe` — background service (no console window).
- `boardagent.exe` — terminal UI (run in a terminal or double-click).
- `boardagent-mcp.exe` — MCP stdio server for MCP hosts.

They bundle Python, FastAPI, Textual, and the built-in themes — no
installation required on the target machine. Textual's lazy widget imports
require `--collect-submodules textual`; the build script already handles it.

## Windows background service options

BoardAgent is a plain process. You can keep it running with any of these:

1. **Task Scheduler** — create a basic task that runs `boardagent-server.exe`
   at logon, hidden.
2. **WinSW / nssm** — wrap the console exe as a Windows service.
3. **Run manually** during the day; the DB is durable and the UI reconnects.

## PyInstaller (optional)

Build scripts are provided as examples only; they are not pre-built:

- `scripts/build_server_exe.bat`
- `scripts/build_tui_exe.bat`
- `scripts/build_mcp_exe.bat`

Example for the server:

```bat
pyinstaller --onefile --name boardagent-server boardagent/api.py
```

Note: Textual apps packaged with PyInstaller may need `--collect-all textual` and platform-specific console handling.
