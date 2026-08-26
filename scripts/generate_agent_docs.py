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
    # The X-API-Key header is required for every endpoint except /healthz
    # (missing key → 401). FastAPI renders Header(default=None) as optional,
    # so fix the requiredness here — reproducible from source.
    for path in spec.get("paths", {}).values():
        for op in path.values():
            if not isinstance(op, dict) or "parameters" not in op:
                continue
            for param in op["parameters"]:
                if param.get("in") == "header" and param.get("name", "").lower() == "x-api-key":
                    param["required"] = True
                    param.pop("anyOf", None)  # drop the [string, null] union
                    param["schema"] = {"type": "string"}
    AGENT_DOCS.mkdir(parents=True, exist_ok=True)
    (AGENT_DOCS / "openapi.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (AGENT_DOCS / "openapi.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    # NOTE: rest_api.md is hand-maintained (token-optimized for agents) and
    # intentionally NOT regenerated here.


def dump_mcp_docs() -> None:
    # Import inside function so we do not need a running server.
    from boardagent.mcp_server import TOOLS

    AGENT_DOCS.mkdir(parents=True, exist_ok=True)
    (AGENT_DOCS / "mcp_tools.json").write_text(
        json.dumps([t.model_dump() for t in TOOLS], indent=2), encoding="utf-8"
    )
    # NOTE: mcp_tools.md is hand-maintained (token-optimized for agents) and
    # intentionally NOT regenerated here.


def main() -> None:
    base_url = os.environ.get("BOARDAGENT_DOCS_URL", "http://127.0.0.1:7373")
    try:
        spec = fetch_openapi(base_url)
        dump_openapi_docs(spec)
    except Exception as exc:
        print(f"Could not fetch OpenAPI from {base_url}: {exc}", file=sys.stderr)
        print("Generate docs with the server running, or pass BOARDAGENT_DOCS_URL.", file=sys.stderr)
        sys.exit(1)
    dump_mcp_docs()
    print(f"Agent docs written to {AGENT_DOCS}")


if __name__ == "__main__":
    main()
