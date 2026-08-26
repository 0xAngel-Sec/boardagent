# Connecting BoardAgent to an MCP host (Hermes, Claude, Cursor, etc.)

BoardAgent ships an MCP server (`boardagent-mcp` / `boardagent-mcp.exe`) that
exposes its 7 task tools over the standard Model Context Protocol (stdio
transport). Any MCP-compatible host can use it: Hermes Agent, Claude Desktop,
Cursor, VS Code, etc.

The MCP server talks **directly to the same service layer** as the REST API —
same SQLite file, same locking, same metadata namespacing. You do not need the
REST server running for the MCP server to work.

## 1. Get the server

Either install from source (`pip install -e .` gives you `boardagent-mcp`),
or use the prebuilt exe in `dist/boardagent-mcp.exe` (no Python needed).

## 2. Add it to your MCP host

### Hermes Agent

```bash
hermes config set mcp_servers.boardagent.command "/absolute/path/to/boardagent-mcp"
hermes config set mcp_servers.boardagent.type stdio
hermes gateway restart        # from a separate terminal
hermes mcp test boardagent    # verify — should list 7 tools
```

Or edit `config.yaml` directly:

```yaml
mcp_servers:
  boardagent:
    command: C:\path\to\boardagent-mcp.exe
    args: []
    type: stdio
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "boardagent": {
      "command": "C:\\path\\to\\boardagent-mcp.exe",
      "args": []
    }
  }
}
```

### Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "boardagent": {
      "command": "C:\\path\\to\\boardagent-mcp.exe",
      "args": []
    }
  }
}
```

## 3. Verify

Ask the host to list tools, or from a shell:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | boardagent-mcp
```

You should see the 7 tools:
`boardagent_create_task`, `boardagent_list_tasks`, `boardagent_get_task`,
`boardagent_update_task`, `boardagent_delete_task`,
`boardagent_claim_task`, `boardagent_complete_task`.

## Configuration

The MCP server honors the same environment variables as the REST server:

| Variable          | Default          | Meaning                          |
|-------------------|------------------|----------------------------------|
| `BOARDAGENT_DB`   | `~/.boardagent/boardagent.db` | SQLite file (override to use a different board) |
| `BOARDAGENT_HOST` | `127.0.0.1`      | Used only if the MCP server talks to REST |
| `BOARDAGENT_PORT` | `7373`           | Used only if the MCP server talks to REST |

**Multiple boards:** run a second MCP server instance with a different
`BOARDAGENT_DB` and register it under a different name in your host.

## Agent conventions

- **Claim before work.** An agent claims a `todo` task
  (`boardagent_claim_task`), which locks it. A second agent gets an error.
- **Namespace your metadata.** Pass `agent_id` with every
  `boardagent_update_task` call; your fields land under
  `metadata.<agent_id>.*` and never clobber another agent's fields.
- **Complete as the owner.** Only the agent that claimed a task can complete it
  (`boardagent_complete_task`).

## Troubleshooting

- **"command not found"** — use an absolute path to the exe or script.
- **Tools missing after config** — MCP config changes require a host restart.
- **Wrong board** — check `BOARDAGENT_DB`; each instance has its own file.
- **Windows + stdio hosts** — the exe is the reliable choice; the Python
  module variant (`python -m boardagent.mcp_server`) also works when Python
  is installed.
