# Why BoardAgent?

BoardAgent is a Todoist-style task manager built for AI agents first. Humans get a fast terminal UI, but the real users are agents: they can create tasks, attach arbitrary metadata, claim work, and complete it through a clean REST API or MCP server.

It is local-first, free forever, and owns your data in a single SQLite file.

## Key ideas

- **One source of truth.** All task logic lives in the FastAPI service. The MCP server and TUI are thin wrappers.
- **Agent metadata.** Each agent writes under its own namespace (`metadata.<agent_id>.*`) so agents do not clobber each other.
- **Color priority.** Red, orange, yellow, green, blue, white — no numeric priority wars.
- **Claim / complete lifecycle.** An agent claims a `todo` task, which locks it and marks it `in_progress`. Only the owning agent can complete it.
- **Terminal UI with two modes.** Human mode shows clean task fields. AI mode shows the full metadata blob.

## Quick walkthrough

1. Start the server: `boardagent-server`
2. Open the TUI: `boardagent`
3. Press `c` to create a task, `a` to toggle AI mode, `r` to refresh, `q` to quit.
4. Use the Settings tab to switch between `amber` and `matrix` themes or adjust opacity.

## Screenshot

![TUI main screen](screenshots/tui.svg)

## Windows background service

The server can be kept running with a scheduled task or a simple wrapper. See `packaging.md` for examples.

## For agents

See `../agent/rest_api.md` and `../agent/mcp_tools.md`.
