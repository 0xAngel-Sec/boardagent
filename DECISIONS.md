# TaskManager — Engineering Decisions

This document records the open-question defaults chosen during the initial build.

## Architecture

- **Single background process owns the DB.** The service layer, REST API, and MCP server all run inside one uvicorn process (`taskmanager-server`). The Textual UI is a separate foreground process that calls `http://127.0.0.1:7373`.
- **MCP wraps the service layer, not HTTP.** Both REST and MCP import from `taskmanager.service` and `taskmanager.store`. No task logic is duplicated. MCP is implemented with the official `mcp` Python SDK (stdio transport), making it easy to plug into any MCP host.
- **SQLite, single file, WAL enabled.** Storage is at `~/.taskmanager/taskmanager.db` by default. WAL is set with `PRAGMA journal_mode=WAL` for safer concurrent reads while the service runs.

## Data Model

- **Metadata namespaced per agent.** Each task has a `metadata` JSON object. Writes from an `agent_id` land under `metadata.<agent_id>.<key>`. GET returns the full metadata object; PATCH merges only the caller's namespace. This prevents cross-agent clobbering while keeping storage simple.
- **Priority as color enum:** `red`, `orange`, `yellow`, `green`, `blue`, `white`. Stored as a string.
- **Status lifecycle:** `todo`, `in_progress`, `blocked`, `done`. Status is a string enum.
- **Task claiming is a lock.** `POST /tasks/{id}/claim` with an `agent_id` atomically checks `owner_agent_id` is null and `status` is `todo`, then sets both `owner_agent_id` and `status=in_progress`. A second claim returns HTTP 409. `POST /tasks/{id}/complete` requires the same `agent_id` and transitions `status` to `done`. Release/unclaim is supported via `PATCH` (set `status=todo` and clear `owner_agent_id`).
- **Metadata stays freeform.** No schema validation beyond valid JSON. The `agent_id` namespace is passed as a query/header parameter.

## Communication

- **REST over localhost HTTP.** Base URL `http://127.0.0.1:7373`. UI detects the service by probing `/healthz`; if it fails, a "service not running" message is shown with the start command.

## Terminal UI

- **Textual, foreground, separate process.** Entry point `taskmanager`. Two display modes: Human (default) and AI (toggle with `a`). Settings tab controls opacity and theme switching.
- **Theme format: JSON.** Themes live in `~/.taskmanager/themes/*.json` and in the built-in package `taskmanager/themes/`. Schema is documented in `docs/human/themes.md`. Each theme defines a named palette map (name → Textual CSS variables + metadata colors). Default themes: `amber.json` and `matrix.json`.
- **Opacity via Windows layered window (when on Windows).** Textual apps run in a terminal; true per-pixel window transparency requires platform-specific APIs. We provide a documented Windows helper script that sets the console window opacity via the Win32 `SetLayeredWindowAttributes` API. The setting is persisted; applying it requires restarting the TUI after running the helper.

## Docs

- **Agent docs auto-generated.** `scripts/generate_agent_docs.py` dumps OpenAPI endpoints and MCP tool schemas to `docs/agent/`.
- **Human docs hand-written.** README plus `docs/human/`.

## Packaging

- **pyproject.toml with console scripts:** `taskmanager-server`, `taskmanager`, `taskmanager-mcp`.
- **MIT license.** `LICENSE` included.
- **PyInstaller exes are documented but not pre-built.** Build scripts are in `scripts/build_exe*.bat` and `docs/human/packaging.md`.

## Testing

- **pytest for API + service layer.** Tests use a temporary SQLite DB. MCP layer gets a smoke test that starts the MCP server via stdio and calls a tool.
