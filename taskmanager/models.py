"""Pydantic models for TaskManager."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Priority(str, Enum):
    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    WHITE = "white"


class Status(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"


class TaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    due: datetime | None = None
    priority: Priority = Priority.WHITE
    project: str | None = Field(default=None, max_length=100)
    status: Status = Status.TODO


class TaskCreate(TaskBase):
    metadata: dict[str, Any] | None = None
    agent_id: str | None = Field(default=None, max_length=100)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    due: datetime | None = None
    priority: Priority | None = None
    project: str | None = Field(default=None, max_length=100)
    status: Status | None = None
    metadata: dict[str, Any] | None = None
    agent_id: str | None = Field(default=None, max_length=100)

    model_config = ConfigDict(extra="forbid")


class Task(TaskBase):
    id: int
    owner_agent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskClaim(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=100)


class TaskComplete(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=100)


class Healthz(BaseModel):
    status: str
    version: str


class TaskList(BaseModel):
    tasks: list[Task]
    count: int
