"""FastAPI REST API — source of truth."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from . import __version__
from .config import DEFAULT_HOST, DEFAULT_PORT
from .models import (
    Healthz,
    Priority,
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

    @app.post("/tasks", response_model=Task, status_code=201)
    async def create_task(task: TaskCreate) -> dict[str, Any]:
        return _serialize(svc.create_task(task))

    @app.get("/tasks", response_model=TaskList)
    async def list_tasks(
        status: Status | None = None,
        project: str | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        tasks = svc.list_tasks(status=status, project=project, owner=owner)
        return {"tasks": [_serialize(t) for t in tasks], "count": len(tasks)}

    @app.get("/tasks/{task_id}", response_model=Task)
    async def get_task(task_id: int) -> dict[str, Any]:
        task = svc.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return _serialize(task)

    @app.patch("/tasks/{task_id}", response_model=Task)
    async def update_task(task_id: int, update: TaskUpdate) -> dict[str, Any]:
        try:
            task = svc.update_task(task_id, update)
        except BoardAgentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return _serialize(task)

    @app.delete("/tasks/{task_id}", status_code=204)
    async def delete_task(task_id: int) -> JSONResponse:
        if not svc.delete_task(task_id):
            raise HTTPException(status_code=404, detail="task not found")
        return JSONResponse(status_code=204, content={})

    @app.post("/tasks/{task_id}/claim", response_model=Task)
    async def claim_task(task_id: int, claim: TaskClaim) -> dict[str, Any]:
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
    async def priority_values() -> list[str]:
        return [p.value for p in Priority]

    @app.get("/tasks/schema/status")
    async def status_values() -> list[str]:
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
                pass
    return task


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("BOARDAGENT_HOST", DEFAULT_HOST)
    port = int(os.environ.get("BOARDAGENT_PORT", DEFAULT_PORT))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
