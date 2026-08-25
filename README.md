# BoardAgent

A local-first, agent-first task manager. Free forever, no cloud.

- **REST API** — source of truth.
- **MCP server** — thin adapter over the same service layer.
- **Textual TUI** — foreground UI talking to the background service over localhost.

Built from SPEC.md by Kimi K2.7.

## Install

```bash
cd boardagent
pip install -e .
```

Dependencies: FastAPI, uvicorn, pydantic, textual, httpx, mcp.

## Run

Start the background service:

```bash
boardagent-server
```

Open the TUI in another terminal:

```bash
boardagent
```

Use the MCP server with any MCP host:

```json
{
  "mcpServers": {
    "boardagent": {
      "command": "boardagent-mcp"
    }
  }
}
```

## Defaults

- API base URL: `http://127.0.0.1:7373`
- DB: `~/.boardagent/boardagent.db` (SQLite, WAL enabled)
- Themes: `~/.boardagent/themes/` (plus built-ins `amber` and `matrix`)
- Settings: `~/.boardagent/settings.json`

## Docs

- **Agent docs** (auto-generated): `docs/agent/`
- **Human docs**: `docs/human/`
- **Engineering decisions**: `DECISIONS.md`

## Development

```bash
python -m pytest
python scripts/generate_agent_docs.py   # needs boardagent-server running
```

## License

MIT — see `LICENSE`.
