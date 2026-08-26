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

    # Resolve auth once at startup.
    auth_role = _check_auth()

    def _require(required: str) -> None:
        if auth_role is None:
            return  # unauthenticated local mode: allow everything
        if ROLE_RANK.get(auth_role, 0) < ROLE_RANK[required]:
            raise BoardAgentError("insufficient permissions")

    async def on_list_tools(ctx, params):  # noqa: ARG001
        return types.ListToolsResult(tools=TOOLS)

    async def on_call_tool(ctx, params):  # noqa: ARG001
        name = params.name
        args = params.arguments or {}
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
                )
                text = _to_json(svc.create_task(create))

            elif name == "boardagent_list_tasks":
                status = Status(args["status"]) if "status" in args else None
                tasks = svc.list_tasks(
                    status=status,
                    project=args.get("project"),
                    owner=args.get("owner"),
                )
                text = _to_json({"tasks": tasks, "count": len(tasks)})

            elif name == "boardagent_get_task":
                task = svc.get_task(args["id"])
                text = _to_json(task if task else {"error": "task not found"})

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
                )
                result = svc.update_task(args["id"], update)
                text = _to_json(result if result else {"error": "task not found"})

            elif name == "boardagent_delete_task":
                text = json.dumps({"deleted": svc.delete_task(args["id"])})

            elif name == "boardagent_claim_task":
                result = svc.claim_task(args["id"], TaskClaim(agent_id=args["agent_id"]))
                text = _to_json(result)

            elif name == "boardagent_complete_task":
                result = svc.complete_task(
                    args["id"], TaskComplete(agent_id=args["agent_id"])
                )
                text = _to_json(result)

            else:
                text = json.dumps({"error": f"unknown tool {name}"})

        except AlreadyClaimedError as exc:
            text = json.dumps({"error": str(exc), "code": "already_claimed"})
        except NotOwnerError as exc:
            text = json.dumps({"error": str(exc), "code": "not_owner"})
        except InvalidTransitionError as exc:
            text = json.dumps({"error": str(exc), "code": "invalid_transition"})
        except BoardAgentError as exc:
            text = json.dumps({"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            text = json.dumps({"error": f"internal error: {exc}"})

        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

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
