# Connecting BoardAgent to an AI tool

MCP (Model Context Protocol) is a standard way for AI tools to talk to
external programs. BoardAgent includes a small MCP server, so an AI tool
like Claude Desktop, Cursor, or Hermes can read your task board, add
tasks, claim them, and mark them done — all from inside that tool.

The MCP server shares the same task file as the app you use, so you and
the agent always see the same board.

## 1. Get the MCP server

You need the `boardagent-mcp` program on your computer. You get it
either by installing BoardAgent from source (`pip install -e .`), or by
using the prebuilt `boardagent-mcp.exe` in the `dist/` folder (no Python
needed).

Find the full path to the program. On Windows it is something like:

```
C:\Users\yourname\Documents\boardagent\dist\boardagent-mcp.exe
```

Write that path down — you will paste it into your AI tool's config below.

## 2. Add it to your AI tool

Pick the tool you use and add BoardAgent to its config file. After
saving, restart the tool so it picks up the change.

### Hermes Agent

Run these commands in a terminal, replacing the path with your real one:

```bash
hermes config set mcp_servers.boardagent.command "C:/path/to/boardagent-mcp.exe"
hermes gateway restart
hermes mcp test boardagent
```

Run those in a normal terminal — not from inside a Hermes chat session
(the gateway cannot restart itself from within).

The last command should list the seven BoardAgent tools. If it does, you
are connected.

Or edit `config.yaml` directly:

```yaml
mcp_servers:
  boardagent:
    command: C:\path\to\boardagent-mcp.exe
    args: []
```

### Claude Desktop

Open (or create) the file `claude_desktop_config.json` — on Windows it
lives at `%APPDATA%\Claude\claude_desktop_config.json` — and add:

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

On Windows the backslashes in the path must be doubled (`\\`), as shown
above. Restart Claude Desktop after saving.

### Cursor

Open (or create) `~/.cursor/mcp.json` and add the same block:

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

Restart Cursor after saving.

## 3. Check that it works

The simplest check: open your AI tool and ask it to "list my BoardAgent
tasks." If it can, the connection works.

You can also check from a terminal — this sends the two handshake
messages (initialize, then tools/list) and should print the seven tools:

```bash
printf '%s\n%s\n' \
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' \
'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | boardagent-mcp
```

You should see seven tools listed:

- `boardagent_create_task`
- `boardagent_list_tasks`
- `boardagent_get_task`
- `boardagent_update_task`
- `boardagent_delete_task`
- `boardagent_claim_task`
- `boardagent_complete_task`

## How agents should behave

When an agent works on your board it follows a few rules so things stay
tidy:

- **Claim before work.** The agent claims a task, which locks it so no
  other agent grabs the same one.
- **Complete as the owner.** Only the agent that claimed a task can mark
  it done.
- **Namespace metadata.** Each agent stores its extra notes under its
  own name, so agents never overwrite each other's data.

You do not need to do anything for these rules to apply — they are built
in.

## Running more than one board

If you want a second, separate task board (for example, one for work and
one for home), set the `BOARDAGENT_DB` environment variable to a
different file path before starting the MCP server. Each path is its own
board. Register each one in your AI tool under a different name.

## Troubleshooting

- **"command not found"** — use the full, absolute path to the exe or
  script, not just the name.
- **Tools do not appear after editing config** — quit and restart your
  AI tool completely. Most tools only read the config file at startup.
- **Wrong board / tasks missing** — check the `BOARDAGENT_DB`
  environment variable. Each board is a separate file; if the path is
  different, you are looking at a different board.
- **Windows-specific** — the `.exe` is the most reliable option. If you
  have Python installed, `python -m boardagent.mcp_server` also works.