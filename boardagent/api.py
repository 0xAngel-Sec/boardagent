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
)
from .models import (
    Agent,
    AgentCreate,
    AgentUpdate,
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


def _require_role(required: str):
    """Dependency factory: enforce a minimum API-key role.

    read < write < admin. A key with a higher role satisfies a lower
    requirement. Requests without a key are rejected with 401.
    """

    def dependency(x_api_key: str | None = Header(default=None)) -> str:
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
        description="Agent-first local task manager REST API.",
        version=__version__,
    )
    svc = service or TaskService()

    @app.get("/healthz", response_model=Healthz)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # ---- server settings --------------------------------------------------

    @app.get("/settings", response_model=ServerSettings)
    async def get_settings(_: str = Depends(require_read)) -> dict[str, Any]:
        return load_server_settings()

    @app.put("/settings", response_model=ServerSettings)
    async def put_settings(
        settings: ServerSettings, _: str = Depends(require_admin)
    ) -> dict[str, Any]:
        save_server_settings(settings.model_dump())
        return load_server_settings()

    # ---- API keys ---------------------------------------------------------

    @app.get("/keys", response_model=list[ApiKey])
    async def list_keys(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
        keys = load_api_keys()
        return [
            {"key": key, "name": info.get("name", ""), "role": info.get("role", ROLE_READ)}
            for key, info in keys.items()
        ]

    @app.post("/keys", response_model=ApiKey, status_code=201)
    async def create_key(
        create: ApiKeyCreate, _: str = Depends(require_admin)
    ) -> dict[str, Any]:
        keys = load_api_keys()
        key = generate_api_key()
        keys[key] = {"name": create.name, "role": create.role.value}
        save_api_keys(keys)
        return {"key": key, "name": create.name, "role": create.role}

    @app.delete("/keys/{key}", status_code=204)
    async def delete_key(key: str, _: str = Depends(require_admin)) -> JSONResponse:
        keys = load_api_keys()
        if key not in keys:
            raise HTTPException(status_code=404, detail="key not found")
        del keys[key]
        save_api_keys(keys)
        return JSONResponse(status_code=204, content={})

    # ---- tasks ------------------------------------------------------------

    @app.post("/tasks", response_model=Task, status_code=201)
    async def create_task(
        task: TaskCreate, _: str = Depends(require_write)
    ) -> dict[str, Any]:
        return _serialize(svc.create_task(task))

    @app.get("/tasks", response_model=TaskList)
    async def list_tasks(
        status: Status | None = None,
        project: str | None = None,
        owner: str | None = None,
        _: str = Depends(require_read),
    ) -> dict[str, Any]:
        tasks = svc.list_tasks(status=status, project=project, owner=owner)
        return {"tasks": [_serialize(t) for t in tasks], "count": len(tasks)}

    @app.get("/tasks/{task_id}", response_model=Task)
    async def get_task(
        task_id: int, _: str = Depends(require_read)
    ) -> dict[str, Any]:
        task = svc.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return _serialize(task)

    @app.patch("/tasks/{task_id}", response_model=Task)
    async def update_task(
        task_id: int, update: TaskUpdate, _: str = Depends(require_write)
    ) -> dict[str, Any]:
        try:
            task = svc.update_task(task_id, update)
        except BoardAgentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return _serialize(task)

    @app.delete("/tasks/{task_id}", status_code=204)
    async def delete_task(
        task_id: int, _: str = Depends(require_admin)
    ) -> JSONResponse:
        if not svc.delete_task(task_id):
            raise HTTPException(status_code=404, detail="task not found")
        return JSONResponse(status_code=204, content={})

    @app.post("/tasks/{task_id}/claim", response_model=Task)
    async def claim_task(
        task_id: int, claim: TaskClaim, _: str = Depends(require_write)
    ) -> dict[str, Any]:
        try:
            return _serialize(svc.claim_task(task_id, claim))
        except AlreadyClaimedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BoardAgentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/complete", response_model=Task)
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

    @app.get("/tasks/schema/priority")
    async def priority_values(_: str = Depends(require_read)) -> list[str]:
        return [p.value for p in Priority]

    @app.get("/tasks/schema/status")
    async def status_values(_: str = Depends(require_read)) -> list[str]:
        return [s.value for s in Status]

    # ---- agents -----------------------------------------------------------

    @app.get("/agents", response_model=list[Agent])
    async def list_agents(_: str = Depends(require_read)) -> list[dict[str, Any]]:
        return svc.list_agents()

    @app.post("/agents", response_model=Agent, status_code=201)
    async def create_agent(
        create: AgentCreate, _: str = Depends(require_write)
    ) -> dict[str, Any]:
        try:
            return svc.create_agent(create)
        except BoardAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/agents/{agent_id}", response_model=Agent)
    async def get_agent(
        agent_id: int, _: str = Depends(require_read)
    ) -> dict[str, Any]:
        agent = svc.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        return agent

    @app.patch("/agents/{agent_id}", response_model=Agent)
    async def update_agent(
        agent_id: int, update: AgentUpdate, _: str = Depends(require_write)
    ) -> dict[str, Any]:
        try:
            agent = svc.update_agent(agent_id, update)
        except BoardAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        return agent

    @app.delete("/agents/{agent_id}", status_code=204)
    async def delete_agent(
        agent_id: int, _: str = Depends(require_admin)
    ) -> JSONResponse:
        if not svc.delete_agent(agent_id):
            raise HTTPException(status_code=404, detail="agent not found")
        return JSONResponse(status_code=204, content={})

    return app


def _serialize(task: dict[str, Any]) -> dict[str, Any]:
    """Convert SQLite row dict to API shape (datetime strings → datetime objects)."""
    task = dict(task)
    for key in ("due", "created_at", "updated_at"):
        if task.get(key):
            try:
                task[key] = datetime.fromisoformat(task[key])
            except ValueError:
                pass
    return task


app = create_app()


def _port_in_use(host: str, port: int) -> bool:
    import socket

    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect((host, port))
        return True
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
        except Exception as exc:
            print(f"server crashed ({exc}); restarting in 2s", flush=True)
            time.sleep(2)


def main() -> None:
    import sys

    import uvicorn

    settings = load_server_settings()
    host = os.environ.get("BOARDAGENT_HOST", settings.get("host", DEFAULT_HOST))
    port = int(os.environ.get("BOARDAGENT_PORT", settings.get("port", DEFAULT_PORT)))
    if not settings.get("api_enabled", True):
        print("API disabled in settings; refusing to start.", flush=True)
        sys.exit(1)
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
