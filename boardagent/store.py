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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL DEFAULT 'ai',
                    description TEXT,
                    fields TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
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
    ) -> int:
        conn = self._connection()
        cur = conn.execute(
            """
            INSERT INTO tasks (title, description, due, priority, project, status, owner_agent_id, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                status = ?, owner_agent_id = ?, metadata = ?, updated_at = ?
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

    # ---- agents -----------------------------------------------------------

    def create_agent(
        self,
        name: str,
        kind: str,
        description: str | None,
        fields: dict[str, str],
        now: str,
    ) -> dict[str, Any]:
        conn = self._connection()
        cur = conn.execute(
            """
            INSERT INTO agents (name, kind, description, fields, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                kind,
                description,
                json.dumps(fields, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
        return self.get_agent(cur.lastrowid)  # type: ignore[return-value]

    def get_agent(self, agent_id: int) -> dict[str, Any] | None:
        conn = self._connection()
        row = conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        return self._agent_row_to_dict(row)

    def get_agent_by_name(self, name: str) -> dict[str, Any] | None:
        conn = self._connection()
        row = conn.execute(
            "SELECT * FROM agents WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return self._agent_row_to_dict(row)

    def list_agents(self) -> list[dict[str, Any]]:
        conn = self._connection()
        rows = conn.execute("SELECT * FROM agents ORDER BY name").fetchall()
        return [self._agent_row_to_dict(row) for row in rows]

    def update_agent(
        self,
        agent_id: int,
        name: str | None,
        kind: str | None,
        description: str | None,
        fields: dict[str, str] | None,
        now: str,
    ) -> dict[str, Any] | None:
        conn = self._connection()
        existing = self.get_agent(agent_id)
        if existing is None:
            return None
        conn.execute(
            """
            UPDATE agents
            SET name = ?, kind = ?, description = ?, fields = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name if name is not None else existing["name"],
                kind if kind is not None else existing["kind"],
                description if description is not None else existing["description"],
                json.dumps(
                    fields if fields is not None else existing["fields"],
                    ensure_ascii=False,
                ),
                now,
                agent_id,
            ),
        )
        conn.commit()
        return self.get_agent(agent_id)

    def delete_agent(self, agent_id: int) -> bool:
        conn = self._connection()
        cur = conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        conn.commit()
        return cur.rowcount > 0

    def _agent_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["fields"] = json.loads(d.get("fields") or "{}")
        return d

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        return d
