"""Service layer — source of truth for all task operations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .config import ROLE_ADMIN, db_path
from .models import (
    Priority,
    Status,
    TaskClaim,
    TaskComplete,
    TaskCreate,
    TaskUpdate,
)
from .store import TaskStore, _UNSET


class BoardAgentError(Exception):
    pass


class AlreadyClaimedError(BoardAgentError):
    pass


class NotOwnerError(BoardAgentError):
    pass


class InvalidTransitionError(BoardAgentError):
    pass


class InvalidInputError(BoardAgentError):
    """Caller sent a malformed request (missing field, bad enum value).

    Distinct from BoardAgentError so the MCP surface can return a
    400-class `invalid_input` code instead of `internal` — an agent that
    follows the documented protocol must be able to self-correct, not
    retry a server bug forever.
    """


class TaskNotFoundError(BoardAgentError):
    pass


def _normalize_due(dt: datetime | None) -> str | None:
    """Store all timestamps UTC-aware. Naive input is assumed UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


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

        # Tasks are always created as todo. A caller-supplied status of
        # in_progress/done would create an ownerless task that claim (409)
        # and complete (403) both reject — a deadlock only escapable by
        # PATCHing it back to todo. The claim/complete endpoints are the
        # only way to enter those states.
        status = Status.TODO.value

        task_id = self.store.create_task(
            title=create.title,
            description=create.description,
            due=_normalize_due(create.due),
            priority=create.priority.value,
            project=create.project,
            status=status,
            metadata=metadata,
            now=self._now(),
            tags=create.tags,
            estimate=create.estimate,
            custom_fields=create.custom_fields,
            links=create.links,
            acceptance_criteria=create.acceptance_criteria,
            dependencies=create.dependencies,
            notes=create.notes,
        )
        return self.store.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        return self.store.get_task(task_id)

    def list_tasks(
        self,
        status: Status | None = None,
        project: str | None = None,
        owner: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self.store.list_tasks(
            status=status.value if status else None,
            project=project,
            owner=owner,
            limit=limit,
            offset=offset,
        )

    def _check_status_transition(
        self,
        existing: dict[str, Any],
        new_status: str | None,
        caller_agent_id: str | None,
        caller_role: str | None,
    ) -> None:
        """Enforce the claim/complete ownership invariant on PATCH status.

        The dedicated claim/complete endpoints are atomic, but a write-role
        caller could otherwise bypass ownership by PATCHing status directly.
        Rules:
        - in_progress on an unowned task is rejected (use claim) — it would
          create an unclaimable, uncompletable deadlock.
        - done / blocked / release-to-todo on a claimed task requires the
          owning agent_id (or an admin key).

        Ownership runs in EVERY auth mode, including unauthenticated local
        MCP. agent_id is self-declared (cooperative, not a security
        boundary), but the check must still run — otherwise PATCH status
        becomes a second, unlocked door next to the locked claim/complete
        endpoints. Only an admin key bypasses ownership.
        """
        if new_status is None or new_status == existing.get("status"):
            return
        owner = existing.get("owner_agent_id")
        is_admin = caller_role == ROLE_ADMIN

        if new_status == Status.IN_PROGRESS.value:
            # Even admins must go through claim for unowned tasks: setting
            # in_progress without an owner creates an unclaimable,
            # uncompletable deadlock.
            if owner is None:
                raise BoardAgentError("use claim to start work on a task")
            if owner != caller_agent_id and not is_admin:
                raise NotOwnerError("only the owning agent can start this task")
        elif new_status == Status.DONE.value:
            # Unowned tasks must go through complete (which records the
            # owner); PATCHing done on an unowned task would bypass the
            # ownership invariant and leave a done task with no owner.
            if owner is None:
                raise BoardAgentError("use complete to finish a task")
            if owner != caller_agent_id and not is_admin:
                raise NotOwnerError("only the owning agent can complete this task")
        elif new_status == Status.BLOCKED.value:
            if owner is not None and owner != caller_agent_id and not is_admin:
                raise NotOwnerError("only the owning agent can block this task")
        elif new_status == Status.TODO.value:
            if owner is not None and owner != caller_agent_id and not is_admin:
                raise NotOwnerError("only the owning agent can release this task")

    def update_task(
        self,
        task_id: int,
        update: TaskUpdate,
        caller_role: str | None = None,
    ) -> dict[str, Any] | None:
        existing = self.store.get_task(task_id)
        if existing is None:
            return None

        # Status transitions must respect ownership (see _check_status_transition).
        # The client's agent_id (same field the claim/complete endpoints use)
        # is the caller identity for the ownership check. The check runs
        # against the FRESH row inside the store's write transaction (via
        # transition_check) so a stale pre-read cannot race a concurrent
        # claim/complete.
        transition_check: Callable[[dict[str, Any]], None] | None = None
        if update.status is not None:
            def transition_check(existing: dict[str, Any]) -> None:
                self._check_status_transition(
                    existing, update.status.value, update.agent_id, caller_role
                )

        # Distinguish "field omitted" from "explicitly set to null" so the
        # store can clear nullable columns. model_fields_set is the set of
        # fields the caller actually sent.
        sent = update.model_fields_set

        def val(field: str) -> Any:
            return getattr(update, field) if field in sent else _UNSET

        # List/dict fields must never store JSON null (breaks the response
        # model). Explicit null on them means "clear" — same as []/{}.
        def list_val(field: str) -> Any:
            if field not in sent:
                return _UNSET
            value = getattr(update, field)
            return value if value is not None else []

        def dict_val(field: str) -> Any:
            if field not in sent:
                return _UNSET
            value = getattr(update, field)
            return value if value is not None else {}

        metadata = _UNSET
        metadata_merge: Callable[[dict[str, Any]], Any] | None = None
        if "metadata" in sent:
            if update.metadata is not None and update.agent_id:
                # Merge under the agent's namespace, preserving all other
                # agents' namespaces. The merge runs INSIDE the store's
                # write transaction against the fresh row (via
                # metadata_merge) so two agents merging concurrently cannot
                # both merge against a stale snapshot and silently drop
                # each other's namespace (lost update).
                def metadata_merge(existing: dict[str, Any]) -> Any:
                    merged = dict(existing["metadata"])
                    merged[update.agent_id] = {
                        **merged.get(update.agent_id, {}),
                        **update.metadata,
                    }
                    return merged
            elif update.metadata is not None and not update.agent_id:
                raise InvalidInputError("metadata update requires agent_id")
            else:
                metadata = _UNSET  # explicit null metadata: keep existing

        # Releasing to todo clears the claim; any other status keeps it.
        if update.status == Status.TODO:
            owner_agent_id: Any = None
            clear_owner = True
        elif update.status is not None:
            owner_agent_id = _UNSET
            clear_owner = False
        else:
            owner_agent_id = _UNSET
            clear_owner = False

        return self.store.update_task(
            task_id=task_id,
            title=val("title") if val("title") is not None else _UNSET,
            description=val("description"),
            due=_normalize_due(update.due) if "due" in sent else _UNSET,
            priority=(
                update.priority.value
                if "priority" in sent and update.priority is not None
                else _UNSET
            ),
            project=val("project"),
            status=(
                update.status.value
                if "status" in sent and update.status is not None
                else _UNSET
            ),
            owner_agent_id=owner_agent_id,
            metadata=metadata,
            now=self._now(),
            clear_owner=clear_owner,
            tags=list_val("tags"),
            estimate=val("estimate"),
            custom_fields=dict_val("custom_fields"),
            links=list_val("links"),
            acceptance_criteria=val("acceptance_criteria"),
            dependencies=list_val("dependencies"),
            notes=list_val("notes"),
            transition_check=transition_check,
            metadata_merge=metadata_merge,
        )

    def delete_task(self, task_id: int) -> bool:
        return self.store.delete_task(task_id)

    def claim_task(self, task_id: int, claim: TaskClaim) -> dict[str, Any]:
        updated = self.store.claim_task(task_id, claim.agent_id, self._now())
        if updated is not None:
            return updated
        # Guard failed or task gone — distinguish for a clean error.
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError("task not found")
        raise AlreadyClaimedError("task is not available for claiming")

    def complete_task(self, task_id: int, complete: TaskComplete) -> dict[str, Any]:
        updated = self.store.complete_task(task_id, complete.agent_id, self._now())
        if updated is not None:
            return updated
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError("task not found")
        if task.get("owner_agent_id") != complete.agent_id:
            raise NotOwnerError("only the owning agent can complete this task")
        if task["status"] == Status.DONE.value:
            raise InvalidTransitionError("task already completed")
        raise BoardAgentError("task is not available for completion")
