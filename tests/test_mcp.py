"""Smoke test for MCP server via stdio.

Uses the MCP Python SDK client (ClientSession via AnyIO streams) for a robust
end-to-end check instead of hand-rolling JSON-RPC.
"""
from __future__ import annotations

import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.anyio
@pytest.mark.skipif(
    sys.platform == "win32" and sys.version_info < (3, 9),
    reason="stdio MCP smoke test skipped on old Windows",
)
async def test_mcp_smoke_create_and_get():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "taskmanager.mcp_server"],
        cwd=str(ROOT),
        env={"PYTHONUNBUFFERED": "1"},
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {t.name for t in tools.tools}
            assert "taskmanager_create_task" in tool_names
            assert "taskmanager_claim_task" in tool_names
            assert "taskmanager_complete_task" in tool_names

            result = await session.call_tool(
                "taskmanager_create_task",
                arguments={
                    "title": "MCP smoke task",
                    "priority": "green",
                    "agent_id": "smoke_agent",
                    "metadata": {"test": True},
                },
            )
            assert len(result.content) == 1
            import json

            task = json.loads(result.content[0].text)
            assert task["title"] == "MCP smoke task"
            assert task["metadata"]["smoke_agent"]["test"] is True
            task_id = task["id"]

            result = await session.call_tool(
                "taskmanager_get_task", arguments={"id": task_id}
            )
            got = json.loads(result.content[0].text)
            assert got["id"] == task_id

            result = await session.call_tool(
                "taskmanager_claim_task",
                arguments={"id": task_id, "agent_id": "smoke_agent"},
            )
            claim = json.loads(result.content[0].text)
            assert claim["status"] == "in_progress"

            # Second claim should fail
            result = await session.call_tool(
                "taskmanager_claim_task",
                arguments={"id": task_id, "agent_id": "other"},
            )
            assert "error" in result.content[0].text.lower()

            result = await session.call_tool(
                "taskmanager_complete_task",
                arguments={"id": task_id, "agent_id": "smoke_agent"},
            )
            complete = json.loads(result.content[0].text)
            assert complete["status"] == "done"
