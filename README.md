# TaskManager

A local-first, agent-first task manager. Free forever, no cloud.

- **REST API** — source of truth.
- **MCP server** — thin adapter over the same service layer.
- **Textual TUI** — foreground UI talking to the background service over localhost.

Built from SPEC.md by Kimi K2.7.

## Install

```bash
cd taskmanager
pip install -e .
```

Dependencies: FastAPI, uvicorn, pydantic, textual, httpx, mcp.

## Run

Start the background service:

```bash
taskmanager-server
```

Open the TUI in another terminal:

```bash
taskmanager
```

Use the MCP server with any MCP host:

```json
{
  "mcpServers": {
    "taskmanager": {
      "command": "taskmanager-mcp"
    }
  }
}
```

## Defaults

- API base URL: `http://127.0.0.1:7373`
- DB: `~/.taskmanager/taskmanager.db` (SQLite, WAL enabled)
- Themes: `~/.taskmanager/themes/` (plus built-ins `amber` and `matrix`)
- Settings: `~/.taskmanager/settings.json`

## Docs

- **Agent docs** (auto-generated): `docs/agent/`
- **Human docs**: `docs/human/`
- **Engineering decisions**: `DECISIONS.md`

## Development

```bash
python -m pytest
python scripts/generate_agent_docs.py   # needs taskmanager-server running
```

## License

MIT — see `LICENSE`.
