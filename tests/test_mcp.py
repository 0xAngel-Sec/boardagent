"""Smoke test for MCP server via stdio.

Uses the MCP Python SDK client (ClientSession via AnyIO streams) for a robust
end-to-end check instead of hand-rolling JSON-RPC.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from boardagent.config import ROLE_ADMIN, ROLE_READ, generate_api_key, save_api_keys

ROOT = Path(__file__).resolve().parents[1]


def _server_params(tmp_path, extra_env=None):
    env = {
        "PYTHONUNBUFFERED": "1",
        "BOARDAGENT_DB": str(tmp_path / "mcp_test.db"),
        "BOARDAGENT_KEYS": str(tmp_path / "keys.json"),
    }
    if extra_env:
        env.update(extra_env)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "boardagent.mcp_server"],
        cwd=str(ROOT),
        env=env,
    )


@pytest.mark.anyio
async def test_mcp_smoke_create_and_get(tmp_path):
    server_params = _server_params(tmp_path)
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {t.name for t in tools.tools}
            assert "boardagent_create_task" in tool_names
            assert "boardagent_claim_task" in tool_names
            assert "boardagent_complete_task" in tool_names

            result = await session.call_tool(
                "boardagent_create_task",
                arguments={
                    "title": "MCP smoke task",
                    "priority": "green",
                    "agent_id": "smoke_agent",
                    "metadata": {"test": True},
                },
            )
            assert len(result.content) == 1
            task = json.loads(result.content[0].text)
            assert task["title"] == "MCP smoke task"
            assert task["metadata"]["smoke_agent"]["test"] is True
            task_id = task["id"]

            result = await session.call_tool(
                "boardagent_get_task", arguments={"id": task_id}
            )
            got = json.loads(result.content[0].text)
            assert got["id"] == task_id

            result = await session.call_tool(
                "boardagent_claim_task",
                arguments={"id": task_id, "agent_id": "smoke_agent"},
            )
            claim = json.loads(result.content[0].text)
            assert claim["status"] == "in_progress"

            # Second claim should fail
            result = await session.call_tool(
                "boardagent_claim_task",
                arguments={"id": task_id, "agent_id": "other"},
            )
            assert "error" in result.content[0].text.lower()

            result = await session.call_tool(
                "boardagent_complete_task",
                arguments={"id": task_id, "agent_id": "smoke_agent"},
            )
            complete = json.loads(result.content[0].text)
            assert complete["status"] == "done"


@pytest.mark.anyio
async def test_mcp_update_task(tmp_path):
    """boardagent_update_task works end-to-end.

    Regression: a prior refactor left an undefined `auth_role` reference in
    this branch — every update call raised NameError and returned a generic
    'internal' error. This test exercises the exact path that was dead.
    """
    server_params = _server_params(tmp_path)
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            result = await session.call_tool(
                "boardagent_create_task",
                arguments={"title": "to update", "agent_id": "u1"},
            )
            task = json.loads(result.content[0].text)
            task_id = task["id"]

            result = await session.call_tool(
                "boardagent_update_task",
                arguments={
                    "id": task_id,
                    "title": "updated via mcp",
                    "description": "desc",
                    "tags": ["mcp", "test"],
                    "estimate": "2h",
                    "agent_id": "u1",
                },
            )
            assert result.is_error is not True, result.content[0].text
            updated = json.loads(result.content[0].text)
            assert updated["title"] == "updated via mcp"
            assert updated["description"] == "desc"
            assert updated["tags"] == ["mcp", "test"]
            assert updated["estimate"] == "2h"


@pytest.mark.anyio
async def test_mcp_auth_roles(tmp_path, monkeypatch):
    """With BOARDAGENT_API_KEY set, role enforcement applies."""
    from boardagent import config as cfg

    monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
    admin_key = generate_api_key()
    read_key = generate_api_key()
    save_api_keys(
        {
            admin_key: {"name": "admin", "role": ROLE_ADMIN},
            read_key: {"name": "reader", "role": ROLE_READ},
        }
    )

    # Read key: can list, cannot create, cannot delete
    server_params = _server_params(tmp_path, {"BOARDAGENT_API_KEY": read_key})
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("boardagent_list_tasks", arguments={})
            assert "error" not in result.content[0].text.lower()

            result = await session.call_tool(
                "boardagent_create_task", arguments={"title": "nope"}
            )
            assert "insufficient permissions" in result.content[0].text

    # Admin key: can create and delete
    server_params = _server_params(tmp_path, {"BOARDAGENT_API_KEY": admin_key})
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "boardagent_create_task", arguments={"title": "yes"}
            )
            task = json.loads(result.content[0].text)
            assert task["title"] == "yes"
            result = await session.call_tool(
                "boardagent_delete_task", arguments={"id": task["id"]}
            )
            assert "deleted" in result.content[0].text


@pytest.mark.anyio
async def test_mcp_invalid_key_rejected(tmp_path, monkeypatch):
    """An invalid BOARDAGENT_API_KEY is rejected on tool calls.

    Auth is re-checked per call (load_api_keys is mtime-cached), so the
    server starts fine but every tool call fails with an auth error.
    """
    from boardagent import config as cfg

    monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
    save_api_keys({})

    server_params = _server_params(tmp_path, {"BOARDAGENT_API_KEY": "ba_bogus"})
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "boardagent_list_tasks", arguments={}
            )
            assert result.is_error is True
            assert "invalid API key" in result.content[0].text


@pytest.mark.anyio
async def test_mcp_console_script_serves(tmp_path):
    """The installed `boardagent-mcp` console script actually serves.

    Regression: pyproject.toml wired the script to the async main(), so
    `pip install -e .` produced a script that printed an unawaited
    coroutine and exited 0 — a silently dead MCP server. The script must
    now route through the sync cli() wrapper.
    """
    import shutil

    exe = shutil.which("boardagent-mcp")
    if exe is None:
        pytest.skip("boardagent-mcp console script not on PATH")

    env = {
        "PYTHONUNBUFFERED": "1",
        "BOARDAGENT_DB": str(tmp_path / "console_test.db"),
        "BOARDAGENT_KEYS": str(tmp_path / "keys.json"),
    }
    server_params = StdioServerParameters(
        command=exe,
        args=[],
        cwd=str(ROOT),
        env=env,
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "boardagent_create_task" in names
            result = await session.call_tool(
                "boardagent_create_task", arguments={"title": "via console script"}
            )
            task = json.loads(result.content[0].text)
            assert task["title"] == "via console script"


@pytest.mark.anyio
async def test_mcp_invalid_input_code(tmp_path):
    """Malformed calls return invalid_input, not internal.

    Regression: a missing required field (or bad enum) raised KeyError /
    ValueError inside the handler and was reported as `internal` — a
    server bug. Agents following the documented protocol would retry a
    malformed call forever instead of fixing their own arguments.
    """
    server_params = _server_params(tmp_path)
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            result = await session.call_tool("boardagent_create_task", arguments={})
            assert result.is_error is True
            body = json.loads(result.content[0].text)
            assert body["code"] == "invalid_input"
            assert "title" in body["error"]

            result = await session.call_tool(
                "boardagent_create_task",
                arguments={"title": "x", "priority": "chartreuse"},
            )
            assert result.is_error is True
            body = json.loads(result.content[0].text)
            assert body["code"] == "invalid_input"

            result = await session.call_tool("boardagent_get_task", arguments={})
            assert result.is_error is True
            body = json.loads(result.content[0].text)
            assert body["code"] == "invalid_input"

            result = await session.call_tool(
                "boardagent_claim_task", arguments={"id": 1}
            )
            assert result.is_error is True
            body = json.loads(result.content[0].text)
            assert body["code"] == "invalid_input"


@pytest.mark.anyio
async def test_mcp_ownership_enforced_unauthenticated(tmp_path):
    """PATCH status cannot bypass ownership in unauthenticated local mode.

    Regression: with no BOARDAGENT_API_KEY, caller_role was None and the
    status-transition check returned early — so a non-owner could PATCH a
    claimed task to done while the complete endpoint correctly blocked
    them. Same intent, two doors, one locked.
    """
    server_params = _server_params(tmp_path)
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            result = await session.call_tool(
                "boardagent_create_task",
                arguments={"title": "alpha task", "agent_id": "alpha"},
            )
            task = json.loads(result.content[0].text)
            tid = task["id"]

            result = await session.call_tool(
                "boardagent_claim_task", arguments={"id": tid, "agent_id": "alpha"}
            )
            assert json.loads(result.content[0].text)["status"] == "in_progress"

            # complete as beta: blocked
            result = await session.call_tool(
                "boardagent_complete_task", arguments={"id": tid, "agent_id": "beta"}
            )
            assert result.is_error is True
            assert json.loads(result.content[0].text)["code"] == "not_owner"

            # PATCH status=done as beta: must also be blocked now
            result = await session.call_tool(
                "boardagent_update_task",
                arguments={"id": tid, "status": "done", "agent_id": "beta"},
            )
            assert result.is_error is True
            assert json.loads(result.content[0].text)["code"] == "not_owner"

            # owner can still complete
            result = await session.call_tool(
                "boardagent_complete_task", arguments={"id": tid, "agent_id": "alpha"}
            )
            assert result.is_error is not True
            assert json.loads(result.content[0].text)["status"] == "done"


@pytest.mark.anyio
async def test_mcp_timestamps_match_rest_format(tmp_path):
    """MCP timestamps use the same Z-suffix format as the REST surface.

    Regression: MCP returned the raw stored isoformat (+00:00) while REST
    re-serialized through pydantic to Z. Both valid ISO 8601, but a
    string comparison across surfaces reported a false "changed" signal.
    """
    server_params = _server_params(tmp_path)
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "boardagent_create_task", arguments={"title": "ts check"}
            )
            task = json.loads(result.content[0].text)
            for key in ("created_at", "updated_at"):
                assert task[key].endswith("Z"), (key, task[key])
                assert "+00:00" not in task[key]


@pytest.mark.anyio
async def test_mcp_non_scalar_id_limit_invalid_input(tmp_path):
    """Non-scalar id/limit return invalid_input, not internal.

    Regression: a dict/list id reached SQLite and raised
    sqlite3.ProgrammingError (reported as `internal`); a dict limit raised
    TypeError in int(limit). Both are caller errors and must be
    self-correctable.
    """
    server_params = _server_params(tmp_path)
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            result = await session.call_tool(
                "boardagent_get_task", arguments={"id": {"a": 1}}
            )
            assert result.is_error is True
            assert json.loads(result.content[0].text)["code"] == "invalid_input"

            result = await session.call_tool(
                "boardagent_list_tasks", arguments={"limit": {"n": 5}}
            )
            assert result.is_error is True
            assert json.loads(result.content[0].text)["code"] == "invalid_input"

            result = await session.call_tool(
                "boardagent_claim_task", arguments={"id": [1], "agent_id": "x"}
            )
            assert result.is_error is True
            assert json.loads(result.content[0].text)["code"] == "invalid_input"

            # string id is a caller error too, not "task not found"
            result = await session.call_tool(
                "boardagent_get_task", arguments={"id": "abc"}
            )
            assert result.is_error is True
            assert json.loads(result.content[0].text)["code"] == "invalid_input"


@pytest.mark.anyio
async def test_mcp_create_always_todo(tmp_path):
    """create_task cannot create an ownerless in_progress/done task.

    Regression: a caller-supplied status of in_progress/done created a
    task that claim (409) and complete (403) both reject — a deadlock
    only escapable by PATCHing back to todo.
    """
    server_params = _server_params(tmp_path)
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "boardagent_create_task",
                arguments={"title": "stuck?", "status": "in_progress"},
            )
            task = json.loads(result.content[0].text)
            assert task["status"] == "todo"
            assert task["owner_agent_id"] is None
