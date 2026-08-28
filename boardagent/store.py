"""SQLite persistence layer."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

# Sentinel: distinguishes "field omitted" from "explicitly set to null".
# Pydantic's model_fields_set tells us which fields the caller actually sent;
# the service layer converts "sent with value None" into this sentinel so the
# store can clear a column instead of keeping the old value.
_UNSET = object()


class TaskStore:
    """Thread-local SQLite store for tasks with JSON metadata."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._local = threading.local()
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # WAL allows one writer at a time; without a busy timeout a
            # colliding write fails instantly with "database is locked".
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    due TEXT,
                    priority TEXT NOT NULL DEFAULT 'white',
                    project TEXT,
                    status TEXT NOT NULL DEFAULT 'todo',
                    owner_agent_id TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project)"
            )
            # Migration: add task field columns if missing (older DBs).
            cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
            if "tags" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
            if "estimate" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN estimate TEXT")
            if "custom_fields" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN custom_fields TEXT NOT NULL DEFAULT '{}'")
            if "links" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN links TEXT NOT NULL DEFAULT '[]'")
            if "acceptance_criteria" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN acceptance_criteria TEXT")
            if "dependencies" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN dependencies TEXT NOT NULL DEFAULT '[]'")
            if "notes" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN notes TEXT NOT NULL DEFAULT '[]'")
            # Drop removed columns (category was removed from the model; a
            # stale column would fail response validation under extra=forbid).
            if "category" in cols:
                conn.execute("ALTER TABLE tasks DROP COLUMN category")
            # Drop the agent registry table if it exists (registry was removed).
            conn.execute("DROP TABLE IF EXISTS agents")
            conn.commit()
        finally:
            conn.close()

    def create_task(
        self,
        title: str,
        description: str | None,
        due: str | None,
        priority: str,
        project: str | None,
        status: str,
        metadata: dict[str, Any],
        now: str,
        tags: list[str] | None = None,
        estimate: str | None = None,
        custom_fields: dict[str, str] | None = None,
        links: list[str] | None = None,
        acceptance_criteria: str | None = None,
        dependencies: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> int:
        conn = self._connection()
        try:
            cur = conn.execute(
                """
                INSERT INTO tasks (title, description, due, priority, project, status, owner_agent_id, metadata, tags, estimate, custom_fields, links, acceptance_criteria, dependencies, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    description,
                    due,
                    priority,
                    project,
                    status,
                    None,
                    json.dumps(metadata, ensure_ascii=False),
                    json.dumps(tags or [], ensure_ascii=False),
                    estimate,
                    json.dumps(custom_fields or {}, ensure_ascii=False),
                    json.dumps(links or [], ensure_ascii=False),
                    acceptance_criteria,
                    json.dumps(dependencies or [], ensure_ascii=False),
                    json.dumps(notes or [], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]
        except Exception:
            # A failed write leaves an open transaction on the thread-local
            # connection; roll back so the next BEGIN IMMEDIATE doesn't fail
            # with "cannot start a transaction within a transaction".
            conn.rollback()
            raise

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        conn = self._connection()
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(self._row_to_dict(row))

    def list_tasks(
        self,
        status: str | None = None,
        project: str | None = None,
        owner: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conn = self._connection()
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if project:
            where.append("project = ?")
            params.append(project)
        if owner:
            where.append("owner_agent_id = ?")
            params.append(owner)
        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def update_task(
        self,
        task_id: int,
        *,
        title=_UNSET,
        description=_UNSET,
        due=_UNSET,
        priority=_UNSET,
        project=_UNSET,
        status=_UNSET,
        owner_agent_id=_UNSET,
        metadata=_UNSET,
        tags=_UNSET,
        estimate=_UNSET,
        custom_fields=_UNSET,
        links=_UNSET,
        acceptance_criteria=_UNSET,
        dependencies=_UNSET,
        notes=_UNSET,
        now: str,
        clear_owner: bool = False,
        transition_check: Callable[[dict[str, Any]], None] | None = None,
        metadata_merge: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update a task inside a single write transaction.

        Read + write happen under BEGIN IMMEDIATE so two concurrent PATCHes
        cannot both merge against a stale read (lost update). Fields passed
        as _UNSET keep their current value; explicit None clears the column.
        If transition_check is given, it runs against the FRESH row read
        inside this transaction — closing the TOCTOU where a status
        transition validated against a stale snapshot could clobber a
        concurrent claim/complete.

        If metadata_merge is given, it runs against the FRESH row's
        metadata inside the transaction and its return value is stored.
        This closes the lost-update window for per-agent metadata
        namespaces: two agents merging into the same task concurrently
        both merge against the latest committed row, so neither write
        silently drops the other's namespace.
        """
        conn = self._connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            existing = self._row_to_dict(row)

            if transition_check is not None:
                transition_check(existing)

            if metadata_merge is not None:
                metadata = metadata_merge(existing)

            def pick(field: str, new: Any) -> Any:
                if field == "owner_agent_id" and clear_owner:
                    return None
                if new is _UNSET:
                    return existing[field]
                return new

            merged_metadata = pick("metadata", metadata)

            conn.execute(
                """
                UPDATE tasks
                SET title = ?, description = ?, due = ?, priority = ?, project = ?,
                    status = ?, owner_agent_id = ?, metadata = ?, tags = ?,
                    estimate = ?, custom_fields = ?, links = ?,
                    acceptance_criteria = ?, dependencies = ?, notes = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    pick("title", title),
                    pick("description", description),
                    pick("due", due),
                    pick("priority", priority),
                    pick("project", project),
                    pick("status", status),
                    pick("owner_agent_id", owner_agent_id),
                    json.dumps(merged_metadata, ensure_ascii=False),
                    json.dumps(pick("tags", tags), ensure_ascii=False),
                    pick("estimate", estimate),
                    json.dumps(pick("custom_fields", custom_fields), ensure_ascii=False),
                    json.dumps(pick("links", links), ensure_ascii=False),
                    pick("acceptance_criteria", acceptance_criteria),
                    json.dumps(pick("dependencies", dependencies), ensure_ascii=False),
                    json.dumps(pick("notes", notes), ensure_ascii=False),
                    now,
                    task_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return self.get_task(task_id)

    def claim_task(self, task_id: int, agent_id: str, now: str) -> dict[str, Any] | None:
        """Atomically claim a todo task. Returns None if the task is gone.

        The WHERE guard makes the transition atomic: two agents claiming
        concurrently cannot both win — SQLite serializes writers, and the
        second UPDATE matches zero rows.
        """
        conn = self._connection()
        try:
            cur = conn.execute(
                "UPDATE tasks SET status = 'in_progress', owner_agent_id = ?, updated_at = ? "
                "WHERE id = ? AND status = 'todo' AND owner_agent_id IS NULL",
                (agent_id, now, task_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if cur.rowcount == 0:
            return None
        return self.get_task(task_id)

    def complete_task(self, task_id: int, agent_id: str, now: str) -> dict[str, Any] | None:
        """Atomically complete a task owned by agent_id. Returns None if the
        task is gone or the ownership/status guard fails."""
        conn = self._connection()
        try:
            cur = conn.execute(
                "UPDATE tasks SET status = 'done', updated_at = ? "
                "WHERE id = ? AND owner_agent_id = ? AND status != 'done'",
                (now, task_id, agent_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if cur.rowcount == 0:
            return None
        return self.get_task(task_id)

    def delete_task(self, task_id: int) -> bool:
        conn = self._connection()
        try:
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            conn.rollback()
            raise

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["custom_fields"] = json.loads(d.get("custom_fields") or "{}")
        d["links"] = json.loads(d.get("links") or "[]")
        d["dependencies"] = json.loads(d.get("dependencies") or "[]")
        d["notes"] = json.loads(d.get("notes") or "[]")
        return d
