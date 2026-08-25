"""Generate agent documentation from OpenAPI and MCP tool schemas."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
AGENT_DOCS = ROOT / "docs" / "agent"


def fetch_openapi(base_url: str) -> dict:
    r = httpx.get(f"{base_url}/openapi.json", timeout=10)
    r.raise_for_status()
    return r.json()


def dump_openapi_docs(spec: dict) -> None:
    AGENT_DOCS.mkdir(parents=True, exist_ok=True)
    (AGENT_DOCS / "openapi.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (AGENT_DOCS / "openapi.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    md = ["# TaskManager REST API (agent docs)\n\n"]
    md.append(f"Base URL: `http://127.0.0.1:7373`\n\n")
    md.append("## Endpoints\n\n")
    paths = spec.get("paths", {})
    for path, methods in sorted(paths.items()):
        for method, detail in methods.items():
            md.append(f"### {method.upper()} {path}\n\n")
            md.append(f"{detail.get('summary', detail.get('description', ''))}\n\n")
            if "requestBody" in detail:
                schema = detail["requestBody"]["content"]["application/json"]["schema"]
                md.append(f"**Request body schema:** `{json.dumps(schema)}`\n\n")
            for code, resp in detail.get("responses", {}).items():
                md.append(f"- **{code}**: {resp.get('description', '')}\n")
            md.append("\n")
    (AGENT_DOCS / "rest_api.md").write_text("".join(md), encoding="utf-8")


def dump_mcp_docs() -> None:
    # Import inside function so we do not need a running server.
    from taskmanager.mcp_server import TOOLS

    AGENT_DOCS.mkdir(parents=True, exist_ok=True)
    (AGENT_DOCS / "mcp_tools.json").write_text(
        json.dumps([t.model_dump() for t in TOOLS], indent=2), encoding="utf-8"
    )
    md = ["# TaskManager MCP Tools (agent docs)\n\n"]
    md.append("Transport: stdio. Command: `taskmanager-mcp`\n\n")
    md.append("## Tools\n\n")
    for tool in TOOLS:
        md.append(f"### {tool.name}\n\n")
        md.append(f"{tool.description}\n\n")
        md.append(f"```json\n{json.dumps(tool.input_schema, indent=2)}\n```\n\n")
    (AGENT_DOCS / "mcp_tools.md").write_text("".join(md), encoding="utf-8")


def main() -> None:
    base_url = os.environ.get("TASKMANAGER_DOCS_URL", "http://127.0.0.1:7373")
    try:
        spec = fetch_openapi(base_url)
        dump_openapi_docs(spec)
    except Exception as exc:
        print(f"Could not fetch OpenAPI from {base_url}: {exc}", file=sys.stderr)
        print("Generate docs with the server running, or pass TASKMANAGER_DOCS_URL.", file=sys.stderr)
        sys.exit(1)
    dump_mcp_docs()
    print(f"Agent docs written to {AGENT_DOCS}")


if __name__ == "__main__":
    main()
