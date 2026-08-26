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
