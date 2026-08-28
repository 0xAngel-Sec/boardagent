# BoardAgent MCP Tools — Agent Reference

Transport: stdio. Command: `boardagent-mcp` (or `boardagent-mcp.exe`). Auth: optional `BOARDAGENT_API_KEY` env var; without it, unauthenticated local mode (all tools allowed).

Machine-readable schemas: `mcp_tools.json` (Python-side dump, snake_case keys — for reference only, not the JSON-RPC wire format) and `openapi.json/yaml` (REST spec, `inputSchema`/camelCase in MCP terms).

## Tools

### boardagent_create_task
Create a task. `priority` is a color. `status` defaults `todo`. `metadata` namespaced under `agent_id`.

```json
{
  "title": "string (required)",
  "description": "string",
  "due": "ISO8601",
  "priority": "white|blue|green|yellow|orange|red",
  "project": "string",
  "status": "todo|in_progress|blocked|done",
  "agent_id": "string",
  "metadata": "object",
  "tags": ["string"],
  "estimate": "string",
  "links": ["string"],
  "acceptance_criteria": "string",
  "dependencies": ["string"],
  "notes": ["string"],
  "custom_fields": {"k": "v"}
}
```

### boardagent_list_tasks
Filter + paginate. `limit` max 500.

```json
{"status": "enum", "project": "string", "owner": "string", "limit": 100, "offset": 0}
```

### boardagent_get_task
```json
{"id": 1}
```

### boardagent_update_task
PATCH semantics: omitted = unchanged, `null` = clear, `[]`/`{}` = clear list/dict. `metadata` needs `agent_id`.

```json
{"id": 1, "title": "string", "description": "string", "due": "ISO8601", "priority": "enum", "project": "string", "status": "enum", "agent_id": "string", "metadata": "object", "tags": ["string"], "estimate": "string", "links": ["string"], "acceptance_criteria": "string", "dependencies": ["string"], "notes": ["string"], "custom_fields": {"k": "v"}}
```

### boardagent_delete_task
```json
{"id": 1}
```

### boardagent_claim_task
Atomic claim: requires `status==todo` and unowned. Errors: `already_claimed`.

```json
{"id": 1, "agent_id": "string"}
```

### boardagent_complete_task
Requires `owner_agent_id == agent_id`. Errors: `not_owner`, `invalid_transition`.

```json
{"id": 1, "agent_id": "string"}
```

## Error protocol

Failures return `isError: true` with JSON text:

```json
{"error": "message", "code": "already_claimed|not_owner|invalid_transition|not_found|invalid_input|boardagent_error|internal|unknown_tool"}
```

`not_found` is emitted by get/update/delete/claim/complete for a missing task.

Check `isError` — never string-parse success. `invalid_input` = the caller sent a malformed request (missing required argument, bad enum value); fix the arguments and retry. `internal` = server bug, retry later. `boardagent_error` is a catch-all that also covers auth/role failures (invalid API key, insufficient permissions). If MCP is disabled in settings, the server refuses to start entirely — no tool call will ever succeed.

## Agent conventions

- Claim before work. Never complete what you don't own.
- Namespace metadata: always pass `agent_id` with metadata writes.
- Append to `notes` (read → extend → PATCH full list).
- Paginate: `limit=100` on large boards.
