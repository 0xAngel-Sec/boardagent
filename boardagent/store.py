"""SQLite persistence layer."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


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
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
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
            if "category" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN category TEXT")
            if "links" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN links TEXT NOT NULL DEFAULT '[]'")
            if "acceptance_criteria" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN acceptance_criteria TEXT")
            if "dependencies" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN dependencies TEXT NOT NULL DEFAULT '[]'")
            if "notes" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN notes TEXT NOT NULL DEFAULT '[]'")
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
        category: str | None = None,
        links: list[str] | None = None,
        acceptance_criteria: str | None = None,
        dependencies: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> int:
        conn = self._connection()
        cur = conn.execute(
            """
            INSERT INTO tasks (title, description, due, priority, project, status, owner_agent_id, metadata, tags, estimate, custom_fields, category, links, acceptance_criteria, dependencies, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                category,
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
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def update_task(
        self,
        task_id: int,
        title: str | None,
        description: str | None,
        due: str | None,
        priority: str | None,
        project: str | None,
        status: str | None,
        owner_agent_id: str | None,
        metadata: dict[str, Any] | None,
        now: str,
        clear_owner: bool = False,
        tags: list[str] | None = None,
        estimate: str | None = None,
        custom_fields: dict[str, str] | None = None,
        category: str | None = None,
        links: list[str] | None = None,
        acceptance_criteria: str | None = None,
        dependencies: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> dict[str, Any] | None:
        conn = self._connection()
        existing = self.get_task(task_id)
        if existing is None:
            return None

        def pick(field: str, new: Any) -> Any:
            if field == "owner_agent_id" and clear_owner:
                return None
            return new if new is not None else existing[field]

        merged_metadata = pick("metadata", metadata)

        conn.execute(
            """
            UPDATE tasks
            SET title = ?, description = ?, due = ?, priority = ?, project = ?,
                status = ?, owner_agent_id = ?, metadata = ?, tags = ?,
                estimate = ?, custom_fields = ?, category = ?, links = ?,
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
                pick("category", category),
                json.dumps(pick("links", links), ensure_ascii=False),
                pick("acceptance_criteria", acceptance_criteria),
                json.dumps(pick("dependencies", dependencies), ensure_ascii=False),
                json.dumps(pick("notes", notes), ensure_ascii=False),
                now,
                task_id,
            ),
        )
        conn.commit()
        return self.get_task(task_id)

    def delete_task(self, task_id: int) -> bool:
        conn = self._connection()
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        return cur.rowcount > 0

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["custom_fields"] = json.loads(d.get("custom_fields") or "{}")
        d["links"] = json.loads(d.get("links") or "[]")
        d["dependencies"] = json.loads(d.get("dependencies") or "[]")
        d["notes"] = json.loads(d.get("notes") or "[]")
        return d
