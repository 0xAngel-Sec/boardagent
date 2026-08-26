# BoardAgent

A task manager that lives on your computer — for you **and** your AI agents.
Terminal UI, REST API, MCP server. Free, local, no cloud.

## Install (Windows)

**You need the prebuilt exes.** Two ways to get them:

**Option A — use the release binaries (easiest):**
Download the latest release from GitHub → unzip → double-click `INSTALL.bat`.
That's it. It copies the programs, sets up autostart, installs a watchdog,
and adds the app to your PATH.

**Option B — build them yourself:**
Requires Python 3.10+.

```bash
pip install -e .          # installs the 3 CLI commands
python scripts/build_exes.py   # builds boardagent.exe, boardagent-server.exe, boardagent-mcp.exe into dist/
```

Then run `INSTALL.bat`.

## Run

**Windows (after INSTALL.bat):** double-click `boardagent.exe` (or type
`boardagent` in a terminal). The background server auto-starts at logon.

**Anywhere (source):**

```bash
boardagent-server   # terminal 1 — the background service
boardagent          # terminal 2 — the task board UI
```

First-time tips:

- Press `c` to create a task, `a` for AI mode, `q` to quit.
- Full keyboard-first: arrows move, space selects/opens, enter activates.
- Settings tab: theme, opacity, API keys, keybinds.

## Let an AI agent use the board

Point your MCP host (Claude Desktop, Cursor, Hermes) at `boardagent-mcp`:

```json
{
  "mcpServers": {
    "boardagent": { "command": "C:\\path\\to\\boardagent-mcp.exe" }
  }
}
```

Create an API key in Settings → API Keys for anything else.

## How it works

- **REST API** — source of truth, `http://127.0.0.1:7373`.
- **MCP server** — thin adapter over the same service layer.
- **Textual TUI** — the app you see, talks to the service over localhost.
- Data lives in `~/.boardagent/boardagent.db` (SQLite). Everything local.

## Docs

- **For humans**: `docs/human/` — intro, MCP setup, themes, packaging.
- **For AI agents**: `docs/agent/` — token-optimized REST + MCP references.
- **Engineering decisions**: `DECISIONS.md`.

## Development

```bash
python -m pytest
python scripts/generate_agent_docs.py   # needs the server running
python scripts/build_exes.py            # Windows exes into dist/
```

## License

MIT — see `LICENSE`.
