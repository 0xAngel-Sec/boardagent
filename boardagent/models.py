"""Pydantic models for BoardAgent."""
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


class Role(str, Enum):
    """API key permission level."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class AgentKind(str, Enum):
    """Whether an agent is an AI or a human user."""

    AI = "ai"
    USER = "user"


# Default fields every agent gets on creation. Users can override the
# values and add their own custom fields (name -> default value).
DEFAULT_AGENT_FIELDS: dict[str, str] = {
    "role": "",
    "model": "",
    "system_prompt": "",
    "temperature": "0.7",
    "max_tokens": "4096",
}


class AgentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    kind: AgentKind = AgentKind.AI
    description: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    kind: AgentKind | None = None
    description: str | None = None
    fields: dict[str, str] | None = None

    model_config = ConfigDict(extra="forbid")


class Agent(AgentBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    role: Role = Role.READ


class ApiKey(BaseModel):
    key: str
    name: str
    role: Role


class ServerSettings(BaseModel):
    host: str = Field(default="127.0.0.1", max_length=255)
    port: int = Field(default=7373, ge=1, le=65535)
    api_enabled: bool = True
    mcp_enabled: bool = True


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
