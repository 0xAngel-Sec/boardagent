"""MCP server — thin adapter over the service layer.

Uses the official Python MCP SDK (lowlevel Server) with stdio transport.

Auth: if the BOARDAGENT_API_KEY environment variable is set, the server
requires that key to exist in ~/.boardagent/keys.json and enforces its role
per tool (read < write < admin). Without the env var, the server runs
unauthenticated (local default).
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any

import mcp_types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .config import (
    ROLE_ADMIN,
    ROLE_READ,
    ROLE_WRITE,
    load_api_keys,
    load_server_settings,
)
from .models import (
    Priority,
    Status,
    TaskClaim,
    TaskComplete,
    TaskCreate,
    TaskUpdate,
)
from .service import (
    AlreadyClaimedError,
    InvalidTransitionError,
    NotOwnerError,
    BoardAgentError,
    TaskService,
)

ROLE_RANK = {ROLE_READ: 1, ROLE_WRITE: 2, ROLE_ADMIN: 3}


def _to_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


def _iso_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _tool(name: str, description: str, schema: dict[str, Any]) -> types.Tool:
    return types.Tool(name=name, description=description, inputSchema=schema)


TOOLS = [
    _tool(
        "boardagent_create_task",
        "Create a new task. Priority is a color: red/orange/yellow/green/blue/white. Requires write role.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title"},
                "description": {"type": "string"},
                "due": {"type": "string", "format": "date-time"},
                "priority": {
                    "type": "string",
                    "enum": [p.value for p in Priority],
                    "default": "white",
                },
                "project": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": [s.value for s in Status],
                    "default": "todo",
                },
                "agent_id": {"type": "string", "description": "Agent namespace for metadata"},
                "metadata": {"type": "object", "description": "Freeform JSON metadata"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Labels for the task"},
                "estimate": {"type": "string", "description": "Time estimate, e.g. '2h'"},
                "links": {"type": "array", "items": {"type": "string"}, "description": "Reference URLs or file paths"},
                "acceptance_criteria": {"type": "string", "description": "Definition of done"},
                "dependencies": {"type": "array", "items": {"type": "string"}, "description": "Blocking task ids/names"},
                "notes": {"type": "array", "items": {"type": "string"}, "description": "Running log of updates"},
                "custom_fields": {"type": "object", "description": "Custom field name -> value"},
            },
            "required": ["title"],
        },
    ),
    _tool(
        "boardagent_list_tasks",
        "List tasks, optionally filtered. Requires read role.",
        {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": [s.value for s in Status]},
                "project": {"type": "string"},
                "owner": {"type": "string"},
                "limit": {"type": "integer", "description": "Max rows (1-500)"},
                "offset": {"type": "integer", "description": "Skip N rows"},
            },
        },
    ),
    _tool(
        "boardagent_get_task",
        "Read a single task by id. Requires read role.",
        {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    ),
    _tool(
        "boardagent_update_task",
        "Update a task. Metadata is merged into agent_id's namespace. Requires write role.",
        {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "due": {"type": "string", "format": "date-time"},
                "priority": {"type": "string", "enum": [p.value for p in Priority]},
                "project": {"type": "string"},
                "status": {"type": "string", "enum": [s.value for s in Status]},
                "agent_id": {"type": "string"},
                "metadata": {"type": "object"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "estimate": {"type": "string"},
                "links": {"type": "array", "items": {"type": "string"}},
                "acceptance_criteria": {"type": "string"},
                "dependencies": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "array", "items": {"type": "string"}},
                "custom_fields": {"type": "object"},
            },
            "required": ["id"],
        },
    ),
    _tool(
        "boardagent_delete_task",
        "Delete a task by id. Requires admin role.",
        {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    ),
    _tool(
        "boardagent_claim_task",
        "Claim/lock a todo task for an agent. Returns an error if unavailable. Requires write role.",
        {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "agent_id": {"type": "string"},
            },
            "required": ["id", "agent_id"],
        },
    ),
    _tool(
        "boardagent_complete_task",
        "Mark a task done. Only the owning agent may complete it. Requires write role.",
        {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "agent_id": {"type": "string"},
            },
            "required": ["id", "agent_id"],
        },
    ),
]

# Tool name -> minimum role required
TOOL_ROLES: dict[str, str] = {
    "boardagent_create_task": ROLE_WRITE,
    "boardagent_list_tasks": ROLE_READ,
    "boardagent_get_task": ROLE_READ,
    "boardagent_update_task": ROLE_WRITE,
    "boardagent_delete_task": ROLE_ADMIN,
    "boardagent_claim_task": ROLE_WRITE,
    "boardagent_complete_task": ROLE_WRITE,
}


def _check_auth() -> str | None:
    """Return the authenticated role, or None if unauthenticated.

    Raises BoardAgentError if a key was provided but is invalid.
    """
    provided = os.environ.get("BOARDAGENT_API_KEY")
    if not provided:
        return None
    keys = load_api_keys()
    if provided not in keys:
        raise BoardAgentError("invalid API key")
    return keys[provided].get("role", ROLE_READ)


def create_mcp_server(service: TaskService | None = None) -> Server:
    svc = service or TaskService()

    # Startup gate: refuse to serve if MCP is disabled in settings.
    settings = load_server_settings()
    if not settings.get("mcp_enabled", True):
        raise BoardAgentError("MCP disabled in settings")

    def _require(required: str) -> None:
        # Re-check auth per call: load_api_keys is mtime-cached, so this is
        # cheap, and role changes (key rotation/demotion) take effect
        # without an MCP server restart.
        auth_role = _check_auth()
        if auth_role is None:
            return  # unauthenticated local mode: allow everything
        if ROLE_RANK.get(auth_role, 0) < ROLE_RANK[required]:
            raise BoardAgentError("insufficient permissions")

    async def on_list_tools(ctx, params):  # noqa: ARG001
        return types.ListToolsResult(tools=TOOLS)

    async def on_call_tool(ctx, params):  # noqa: ARG001
        name = params.name
        args = params.arguments or {}
        is_error = False
        try:
            _require(TOOL_ROLES.get(name, ROLE_READ))

            if name == "boardagent_create_task":
                create = TaskCreate(
                    title=args["title"],
                    description=args.get("description"),
                    due=_iso_to_dt(args.get("due")),
                    priority=Priority(args.get("priority", "white")),
                    project=args.get("project"),
                    status=Status(args.get("status", "todo")),
                    agent_id=args.get("agent_id"),
                    metadata=args.get("metadata"),
                    tags=args.get("tags") or [],
                    estimate=args.get("estimate"),
                    custom_fields=args.get("custom_fields") or {},
                    links=args.get("links") or [],
                    acceptance_criteria=args.get("acceptance_criteria"),
                    dependencies=args.get("dependencies") or [],
                    notes=args.get("notes") or [],
                )
                text = _to_json(svc.create_task(create))

            elif name == "boardagent_list_tasks":
                status = Status(args["status"]) if "status" in args else None
                limit = args.get("limit")
                if limit is not None:
                    # Clamp to the same bounds the REST API enforces; a
                    # negative limit would make SQLite return the whole table.
                    limit = max(1, min(500, int(limit)))
                offset = args.get("offset", 0)
                try:
                    offset = max(0, int(offset))
                except (TypeError, ValueError):
                    offset = 0
                tasks = svc.list_tasks(
                    status=status,
                    project=args.get("project"),
                    owner=args.get("owner"),
                    limit=limit,
                    offset=offset,
                )
                text = _to_json({"tasks": tasks, "count": len(tasks)})

            elif name == "boardagent_get_task":
                task = svc.get_task(args["id"])
                if task is None:
                    text = json.dumps({"error": "task not found", "code": "not_found"})
                    is_error = True
                else:
                    text = _to_json(task)

            elif name == "boardagent_update_task":
                update = TaskUpdate(
                    title=args.get("title"),
                    description=args.get("description"),
                    due=_iso_to_dt(args.get("due")),
                    priority=Priority(args["priority"]) if "priority" in args else None,
                    project=args.get("project"),
                    status=Status(args["status"]) if "status" in args else None,
                    agent_id=args.get("agent_id"),
                    metadata=args.get("metadata"),
                    tags=args.get("tags"),
                    estimate=args.get("estimate"),
                    custom_fields=args.get("custom_fields"),
                    links=args.get("links"),
                    acceptance_criteria=args.get("acceptance_criteria"),
                    dependencies=args.get("dependencies"),
                    notes=args.get("notes"),
                )
                result = svc.update_task(
                    args["id"],
                    update,
                    caller_role=auth_role,
                )
                if result is None:
                    text = json.dumps({"error": "task not found", "code": "not_found"})
                    is_error = True
                else:
                    text = _to_json(result)

            elif name == "boardagent_delete_task":
                deleted = svc.delete_task(args["id"])
                if not deleted:
                    text = json.dumps({"error": "task not found", "code": "not_found"})
                    is_error = True
                else:
                    text = json.dumps({"deleted": True})

            elif name == "boardagent_claim_task":
                result = svc.claim_task(args["id"], TaskClaim(agent_id=args["agent_id"]))
                text = _to_json(result)

            elif name == "boardagent_complete_task":
                result = svc.complete_task(
                    args["id"], TaskComplete(agent_id=args["agent_id"])
                )
                text = _to_json(result)

            else:
                text = json.dumps({"error": f"unknown tool {name}", "code": "unknown_tool"})
                is_error = True

        except AlreadyClaimedError as exc:
            text = json.dumps({"error": str(exc), "code": "already_claimed"})
            is_error = True
        except NotOwnerError as exc:
            text = json.dumps({"error": str(exc), "code": "not_owner"})
            is_error = True
        except InvalidTransitionError as exc:
            text = json.dumps({"error": str(exc), "code": "invalid_transition"})
            is_error = True
        except BoardAgentError as exc:
            text = json.dumps({"error": str(exc), "code": "boardagent_error"})
            is_error = True
        except Exception:  # noqa: BLE001
            # Never leak exception internals (paths, SQL fragments) to the
            # client. Log server-side, return a generic error.
            import logging

            logging.getLogger("boardagent").exception("MCP tool %s failed", name)
            text = json.dumps({"error": "internal error", "code": "internal"})
            is_error = True

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], isError=is_error
        )

    return Server(
        "boardagent",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def main() -> None:
    server = create_mcp_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
