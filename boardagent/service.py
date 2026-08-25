"""Service layer — source of truth for all task operations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import db_path
from .models import Priority, Status, TaskClaim, TaskComplete, TaskCreate, TaskUpdate
from .store import TaskStore


class BoardAgentError(Exception):
    pass


class AlreadyClaimedError(BoardAgentError):
    pass


class NotOwnerError(BoardAgentError):
    pass


class InvalidTransitionError(BoardAgentError):
    pass


class TaskService:
    """Business logic. Imported by REST API and MCP server."""

    def __init__(self, store: TaskStore | None = None):
        self.store = store or TaskStore(db_path())

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_task(self, create: TaskCreate) -> dict[str, Any]:
        metadata: dict[str, Any] = create.metadata or {}
        agent_id = create.agent_id
        # If initial metadata is passed, namespace it under the creating agent
        if metadata and agent_id:
            metadata = {agent_id: metadata}

        task_id = self.store.create_task(
            title=create.title,
            description=create.description,
            due=create.due.isoformat() if create.due else None,
            priority=create.priority.value,
            project=create.project,
            status=create.status.value,
            metadata=metadata,
            now=self._now(),
        )
        return self.store.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        return self.store.get_task(task_id)

    def list_tasks(
        self,
        status: Status | None = None,
        project: str | None = None,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.store.list_tasks(
            status=status.value if status else None,
            project=project,
            owner=owner,
        )

    def update_task(self, task_id: int, update: TaskUpdate) -> dict[str, Any] | None:
        existing = self.store.get_task(task_id)
        if existing is None:
            return None

        metadata = existing["metadata"].copy()
        if update.metadata is not None and update.agent_id:
            metadata[update.agent_id] = {
                **metadata.get(update.agent_id, {}),
                **update.metadata,
            }
        elif update.metadata is not None and not update.agent_id:
            raise BoardAgentError("metadata update requires agent_id")

        if update.status == Status.TODO:
            owner_agent_id = None  # releasing to todo clears the claim
        elif update.status is not None:
            owner_agent_id = existing.get("owner_agent_id")  # keep claim
        else:
            owner_agent_id = None  # no status change -> store keeps existing

        return self.store.update_task(
            task_id=task_id,
            title=update.title,
            description=update.description,
            due=update.due.isoformat() if update.due else None,
            priority=update.priority.value if update.priority else None,
            project=update.project,
            status=update.status.value if update.status else None,
            owner_agent_id=owner_agent_id,
            metadata=metadata,
            now=self._now(),
            clear_owner=update.status == Status.TODO,
        )

    def delete_task(self, task_id: int) -> bool:
        return self.store.delete_task(task_id)

    def claim_task(self, task_id: int, claim: TaskClaim) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise BoardAgentError("task not found")
        if task["status"] != Status.TODO.value:
            raise AlreadyClaimedError("task is not available for claiming")
        if task.get("owner_agent_id") is not None:
            raise AlreadyClaimedError("task already claimed")

        updated = self.store.update_task(
            task_id=task_id,
            title=None,
            description=None,
            due=None,
            priority=None,
            project=None,
            status=Status.IN_PROGRESS.value,
            owner_agent_id=claim.agent_id,
            metadata=None,
            now=self._now(),
        )
        return updated  # type: ignore[return-value]

    def complete_task(self, task_id: int, complete: TaskComplete) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise BoardAgentError("task not found")
        if task.get("owner_agent_id") != complete.agent_id:
            raise NotOwnerError("only the owning agent can complete this task")
        if task["status"] == Status.DONE.value:
            raise InvalidTransitionError("task already completed")

        updated = self.store.update_task(
            task_id=task_id,
            title=None,
            description=None,
            due=None,
            priority=None,
            project=None,
            status=Status.DONE.value,
            owner_agent_id=complete.agent_id,
            metadata=None,
            now=self._now(),
        )
        return updated  # type: ignore[return-value]
