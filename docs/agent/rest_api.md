# BoardAgent REST API — Agent Reference

Base: `http://127.0.0.1:7373` (local). Auth: `X-API-Key` header. Roles: `read` < `write` < `admin`. No key → 401. Insufficient role → 403.

## Task object

```json
{
  "id": 1,
  "title": "string",
  "description": "string|null",
  "due": "ISO8601 UTC|null",
  "priority": "white|blue|green|yellow|orange|red",
  "project": "string|null",
  "status": "todo|in_progress|blocked|done",
  "tags": ["string"],
  "estimate": "string|null",
  "links": ["string"],
  "acceptance_criteria": "string|null",
  "dependencies": ["string"],
  "notes": ["string"],
  "custom_fields": {"k": "v"},
  "metadata": {"agent_id": {...}},
  "owner_agent_id": "string|null",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

## Endpoints

| Method | Path | Role | Success | Errors |
|---|---|---|---|---|
| GET | `/healthz` | none | 200 `{"status":"ok","version":"0.1.0"}` | — |
| POST | `/tasks` | write | 201 Task | 422 validation |
| GET | `/tasks?status=&project=&owner=&limit=&offset=` | read | 200 `{tasks, count}` | 422 |
| GET | `/tasks/{id}` | read | 200 Task | 404 |
| PATCH | `/tasks/{id}` | write | 200 Task | 400, 404, 422 |
| DELETE | `/tasks/{id}` | admin | 204 | 404 |
| POST | `/tasks/{id}/claim` | write | 200 Task | 404, 409 |
| POST | `/tasks/{id}/complete` | write | 200 Task | 403, 404, 409 |
| GET | `/tasks/schema/priority` | read | 200 list | — |
| GET | `/tasks/schema/status` | read | 200 list | — |
| GET | `/settings` | read | 200 | — |
| PUT | `/settings` | admin | 200 | — |
| GET | `/keys` | admin | 200 list | — |
| POST | `/keys` | admin | 201 `{key,name,role}` | — |
| DELETE | `/keys/{key}` | admin | 204 | 404 |

`count` = rows in THIS page (after limit/offset), not the total. Paginate until a page returns fewer than `limit` rows. `owner` filters on `owner_agent_id` (the agent that claimed the task).

## PATCH semantics

- Omitted field → unchanged.
- Explicit `null` → clears scalar fields (description, due, project, estimate, acceptance_criteria).
- `[]` / `{}` → clears list/dict fields (tags, links, dependencies, notes, custom_fields). `null` on a list/dict field also clears it (never stored as JSON null).
- `metadata: null` is IGNORED (kept). `metadata` requires `agent_id`; merges into `metadata.<agent_id>`.
- Unknown fields → 422 (extra=forbid). Typos fail loudly.

## Status transitions (ownership enforced)

- `claim` requires `status==todo` AND `owner_agent_id IS NULL` — atomic, no race.
- `complete` requires `owner_agent_id == agent_id` and `status != done`.
- PATCH `status` is guarded: cannot set `in_progress` on unowned task (use claim); cannot move a claimed task to done/blocked/todo unless you are the owner (pass your `agent_id` in the PATCH body; admin bypasses).
- PATCH status ownership violations → **400** (not 403); 403 is only returned by `/complete`.
- Releasing to `todo` clears the owner.

## Errors

- 400: bad transition / ownership violation on PATCH / metadata without agent_id
- 403: not the owner (complete) / insufficient role
- 404: task or key not found
- 409: already claimed / already done
- 422: validation (bad enum, extra field, bad limit)

## Notes for agents

- `limit` max 500. Always paginate large boards: `?limit=100&offset=0`.
- All timestamps UTC-aware. Send `due` with offset or naive (assumed UTC).
- `metadata` is per-agent namespaced — never clobbers other agents.
- Append to `notes` by reading, extending, PATCHing the full list.
