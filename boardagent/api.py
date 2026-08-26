"""FastAPI REST API — source of truth."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from . import __version__
from .config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ROLE_ADMIN,
    ROLE_READ,
    ROLE_WRITE,
    generate_api_key,
    load_api_keys,
    load_server_settings,
    save_api_keys,
    save_server_settings,
    settings_path,
)
from .models import (
    ApiKey,
    ApiKeyCreate,
    Healthz,
    Priority,
    Role,
    ServerSettings,
    Status,
    Task,
    TaskClaim,
    TaskComplete,
    TaskCreate,
    TaskList,
    TaskUpdate,
)
from .service import (
    AlreadyClaimedError,
    InvalidTransitionError,
    NotOwnerError,
    BoardAgentError,
    TaskService,
)

API_KEY_HEADER = "X-API-Key"

# Shared OpenAPI error responses. Referenced by route decorators so the
# generated spec documents auth/ownership failures accurately and
# reproducibly (no hand-editing of generated files).
AUTH_ERRORS = {
    401: {"description": "Missing or invalid API key"},
    403: {"description": "Insufficient role or not the owner"},
}
NOT_FOUND = {404: {"description": "Task or key not found"}}
CONFLICT = {409: {"description": "Already claimed / already done"}}
BAD_REQUEST = {400: {"description": "Bad transition or invalid request"}}


def _require_role(required: str):
    """Dependency factory: enforce a minimum API-key role.

    read < write < admin. A key with a higher role satisfies a lower
    requirement. Requests without a key are rejected with 401.
    """

    def dependency(x_api_key: str = Header(default=None)) -> str:
        keys = load_api_keys()
        if not x_api_key or x_api_key not in keys:
            raise HTTPException(status_code=401, detail="missing or invalid API key")
        role = keys[x_api_key].get("role", ROLE_READ)
        rank = {ROLE_READ: 1, ROLE_WRITE: 2, ROLE_ADMIN: 3}
        if rank.get(role, 0) < rank[required]:
            raise HTTPException(status_code=403, detail="insufficient permissions")
        return role

    return dependency


require_read = _require_role(ROLE_READ)
require_write = _require_role(ROLE_WRITE)
require_admin = _require_role(ROLE_ADMIN)


def create_app(service: TaskService | None = None) -> FastAPI:
    app = FastAPI(
        title="BoardAgent API",
        description=(
            "BoardAgent local task manager API. All endpoints except /healthz "
            "require the X-API-Key header (roles: read < write < admin). "
            "See docs/agent/rest_api.md for the full error table."
        ),
        version=__version__,
    )
    svc = service or TaskService()

    @app.get("/healthz", response_model=Healthz)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # ---- server settings --------------------------------------------------

    @app.get("/settings", response_model=ServerSettings, responses={**AUTH_ERRORS})
    async def get_settings(_: str = Depends(require_read)) -> dict[str, Any]:
        return load_server_settings()

    @app.put("/settings", response_model=ServerSettings, responses={**AUTH_ERRORS})
    async def put_settings(
        settings: ServerSettings, _: str = Depends(require_admin)
    ) -> dict[str, Any]:
        save_server_settings(settings.model_dump())
        return load_server_settings()

    # ---- API keys ---------------------------------------------------------

    @app.get("/keys", response_model=list[ApiKey], responses={**AUTH_ERRORS})
    async def list_keys(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
        keys = load_api_keys()
        return [
            {"key": key, "name": info.get("name", ""), "role": info.get("role", ROLE_READ)}
            for key, info in keys.items()
        ]

    @app.post("/keys", response_model=ApiKey, status_code=201, responses={**AUTH_ERRORS})
    async def create_key(
        create: ApiKeyCreate, _: str = Depends(require_admin)
    ) -> dict[str, Any]:
        keys = load_api_keys()
        key = generate_api_key()
        keys[key] = {"name": create.name, "role": create.role.value}
        save_api_keys(keys)
        return {"key": key, "name": create.name, "role": create.role}

    @app.delete("/keys/{key}", status_code=204, responses={**AUTH_ERRORS, **NOT_FOUND, **BAD_REQUEST})
    async def delete_key(key: str, _: str = Depends(require_admin)) -> JSONResponse:
        keys = load_api_keys()
        if key not in keys:
            raise HTTPException(status_code=404, detail="key not found")
        if _is_console_key(key, keys):
            # The console key is the TUI's own admin credential. Deleting it
            # would lock the local UI out of the API, so it is protected.
            raise HTTPException(
                status_code=400, detail="console key cannot be deleted"
            )
        del keys[key]
        save_api_keys(keys)
        return JSONResponse(status_code=204, content={})

    # ---- tasks ------------------------------------------------------------

    @app.post("/tasks", response_model=Task, status_code=201, responses={**AUTH_ERRORS})
    async def create_task(
        task: TaskCreate, _: str = Depends(require_write)
    ) -> dict[str, Any]:
        return _serialize(svc.create_task(task))

    @app.get("/tasks", response_model=TaskList, responses={**AUTH_ERRORS})
    async def list_tasks(
        status: Status | None = None,
        project: str | None = None,
        owner: str | None = None,
        limit: int | None = Query(default=None, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        _: str = Depends(require_read),
    ) -> dict[str, Any]:
        tasks = svc.list_tasks(
            status=status, project=project, owner=owner, limit=limit, offset=offset
        )
        return {"tasks": [_serialize(t) for t in tasks], "count": len(tasks)}

    @app.get("/tasks/{task_id}", response_model=Task, responses={**AUTH_ERRORS, **NOT_FOUND})
    async def get_task(
        task_id: int, _: str = Depends(require_read)
    ) -> dict[str, Any]:
        task = svc.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return _serialize(task)

    @app.patch("/tasks/{task_id}", response_model=Task, responses={**AUTH_ERRORS, **NOT_FOUND, **BAD_REQUEST})
    async def update_task(
        task_id: int,
        update: TaskUpdate,
        caller_role: str = Depends(require_write),
    ) -> dict[str, Any]:
        try:
            task = svc.update_task(task_id, update, caller_role=caller_role)
        except BoardAgentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return _serialize(task)

    @app.delete("/tasks/{task_id}", status_code=204, responses={**AUTH_ERRORS, **NOT_FOUND})
    async def delete_task(
        task_id: int, _: str = Depends(require_admin)
    ) -> JSONResponse:
        if not svc.delete_task(task_id):
            raise HTTPException(status_code=404, detail="task not found")
        return JSONResponse(status_code=204, content={})

    @app.post("/tasks/{task_id}/claim", response_model=Task, responses={**AUTH_ERRORS, **NOT_FOUND, **CONFLICT})
    async def claim_task(
        task_id: int, claim: TaskClaim, _: str = Depends(require_write)
    ) -> dict[str, Any]:
        try:
            return _serialize(svc.claim_task(task_id, claim))
        except AlreadyClaimedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BoardAgentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/complete", response_model=Task, responses={**AUTH_ERRORS, **NOT_FOUND, **CONFLICT})
    async def complete_task(
        task_id: int,
        complete: TaskComplete,
        _: str = Depends(require_write),
    ) -> dict[str, Any]:
        try:
            return _serialize(svc.complete_task(task_id, complete))
        except NotOwnerError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BoardAgentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/tasks/schema/priority", responses={**AUTH_ERRORS})
    async def priority_values(_: str = Depends(require_read)) -> list[str]:
        return [p.value for p in Priority]

    @app.get("/tasks/schema/status", responses={**AUTH_ERRORS})
    async def status_values(_: str = Depends(require_read)) -> list[str]:
        return [s.value for s in Status]

    return app


def _serialize(task: dict[str, Any]) -> dict[str, Any]:
    """Convert SQLite row dict to API shape (datetime strings → datetime objects)."""
    task = dict(task)
    for key in ("due", "created_at", "updated_at"):
        if task.get(key):
            try:
                task[key] = datetime.fromisoformat(task[key])
            except ValueError:
                # Malformed stored timestamp: log loudly instead of silently
                # passing a string that fails response-model validation.
                import logging

                logging.getLogger("boardagent").warning(
                    "malformed timestamp in %s: %r", key, task.get(key)
                )
    return task


app = create_app()


def _port_in_use(host: str, port: int) -> bool:
    """True only if a live BoardAgent is answering on the port.

    A bare TCP connect would report "already running" if any other process
    holds the port, silently masking a dead BoardAgent. Verify the HTTP
    healthz payload before declaring the service alive.
    """
    import socket

    try:
        s = socket.create_connection((host, port), timeout=1)
    except OSError:
        return False
    try:
        s.sendall(
            b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            # Break only when the FULL payload is present. Breaking on a
            # partial '"status"' (split across packets) would false-negative.
            if b'"status":"ok"' in data or b'"status": "ok"' in data:
                break
            if len(data) > 8192:
                break
        return b'"status":"ok"' in data or b'"status": "ok"' in data
    except OSError:
        return False
    finally:
        s.close()


def _run_forever(host: str, port: int) -> None:
    """Run the server, restarting it in place if it crashes. Never exits."""
    import time

    import uvicorn

    while True:
        try:
            uvicorn.run(app, host=host, port=port, log_level="info")
            return
        except KeyboardInterrupt:
            return
        except (Exception, SystemExit) as exc:
            # If another instance owns the port while we were restarting
            # (watchdog + manual start race), bow out instead of
            # crash-looping forever. uvicorn raises SystemExit(1) on bind
            # failure, so SystemExit must be caught too.
            if _port_in_use(host, port):
                print("another instance owns the port; exiting", flush=True)
                return
            print(f"server crashed ({exc}); restarting in 2s", flush=True)
            time.sleep(2)


def _is_console_key(key: str, keys: dict[str, dict[str, str]]) -> bool:
    """True if the given key is the protected console credential.

    Identified by the `console: true` flag, OR (for legacy keys created
    before the flag existed) by matching the settings.json console_key
    value, OR by the historical name marker. Never trust the name alone
    for NEW keys — a user could create a key named "console".
    """
    if keys.get(key, {}).get("console"):
        return True
    settings: dict[str, Any] = {}
    try:
        settings = json.loads(settings_path().read_text(encoding="utf-8"))
        if settings.get("console_key") == key:
            return True
    except Exception:
        pass
    return keys.get(key, {}).get("name") == "console" and settings.get("console_key") == key


def _ensure_console_key() -> str:
    """Return the console admin key, creating or re-registering as needed.

    The console key is the local UI's credential, stored in settings.json
    and registered in keys.json. If it exists in settings but is missing
    from keys.json (e.g. all keys were deleted via the API), re-register it
    so the server is never unreachable. If neither exists, mint a new one.

    NOTE: reads settings.json raw — load_server_settings() strips
    non-default keys, so console_key would always appear missing.
    """
    import json

    from .config import save_server_settings

    settings: dict[str, Any] = {}
    try:
        p = settings_path()
        if p.exists():
            settings = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        settings = {}
    key = settings.get("console_key")
    keys = load_api_keys()
    if key and key in keys:
        # Backfill the protection flag on legacy keys (created before the
        # flag existed) so delete_key's flag check covers them too.
        if not keys[key].get("console"):
            keys[key]["console"] = True
            save_api_keys(keys)
        return key
    if not key:
        key = generate_api_key()
        settings["console_key"] = key
        save_server_settings(settings)
    keys[key] = {"name": "console", "role": ROLE_ADMIN, "console": True}
    save_api_keys(keys)
    return key


def main() -> None:
    import sys

    import uvicorn

    settings = load_server_settings()
    host = os.environ.get("BOARDAGENT_HOST", settings.get("host", DEFAULT_HOST))
    try:
        port = int(os.environ.get("BOARDAGENT_PORT", settings.get("port", DEFAULT_PORT)))
    except (TypeError, ValueError):
        # A corrupt env var or settings value must not crash startup.
        print(f"invalid port value; using default {DEFAULT_PORT}", flush=True)
        port = DEFAULT_PORT
    if not settings.get("api_enabled", True):
        print("API disabled in settings; refusing to start.", flush=True)
        sys.exit(1)

    # Ensure the server can never come up unreachable: if no console key
    # exists yet, mint one (the TUI creates its own on first use, but a
    # fresh install may start the server before the TUI ever runs).
    keys = load_api_keys()
    if not any(info.get("console") for info in keys.values()):
        _ensure_console_key()

    if "--check" in sys.argv:  # watchdog probe: exit 0 if already running
        sys.exit(0 if _port_in_use(host, port) else 1)
    if "--watch" in sys.argv or "--watchdog" in sys.argv:
        # Self-healing modes for autostart / scheduled-task watchdog.
        # Idempotent: never fight an instance that already holds the port.
        if _port_in_use(host, port):
            print("already running", flush=True)
            sys.exit(0)
        _run_forever(host, port)
        return
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
