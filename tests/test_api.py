"""Tests for BoardAgent service and API layers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from boardagent.api import create_app
from boardagent.models import Priority, Status, TaskClaim, TaskComplete, TaskCreate, TaskUpdate
from boardagent.service import (
    AlreadyClaimedError,
    NotOwnerError,
    TaskService,
)
from boardagent.store import TaskStore


@pytest.fixture
def temp_store(tmp_path):
    db = tmp_path / "test.db"
    return TaskStore(db)


@pytest.fixture
def service(temp_store):
    return TaskService(temp_store)


@pytest.fixture
def client(service):
    return TestClient(create_app(service))


class TestService:
    def test_create_and_get_task(self, service):
        task = service.create_task(
            TaskCreate(title="Test task", priority=Priority.RED)
        )
        assert task["title"] == "Test task"
        assert task["priority"] == "red"
        assert task["status"] == "todo"
        got = service.get_task(task["id"])
        assert got["title"] == "Test task"

    def test_metadata_namespaced(self, service):
        task = service.create_task(
            TaskCreate(title="Meta task", agent_id="agent_a", metadata={"note": "hello"})
        )
        assert task["metadata"]["agent_a"]["note"] == "hello"
        service.update_task(
            task["id"],
            TaskUpdate(agent_id="agent_b", metadata={"note": "world"}),
        )
        got = service.get_task(task["id"])
        assert got["metadata"]["agent_a"]["note"] == "hello"
        assert got["metadata"]["agent_b"]["note"] == "world"

    def test_claim_complete_lifecycle(self, service):
        task = service.create_task(TaskCreate(title="Lifecycle"))
        claimed = service.claim_task(task["id"], TaskClaim(agent_id="agent_1"))
        assert claimed["status"] == "in_progress"
        assert claimed["owner_agent_id"] == "agent_1"

        with pytest.raises(AlreadyClaimedError):
            service.claim_task(task["id"], TaskClaim(agent_id="agent_2"))

        with pytest.raises(NotOwnerError):
            service.complete_task(task["id"], TaskComplete(agent_id="agent_2"))

        completed = service.complete_task(
            task["id"], TaskComplete(agent_id="agent_1")
        )
        assert completed["status"] == "done"

    def test_metadata_requires_agent_id(self, service):
        task = service.create_task(TaskCreate(title="Needs agent"))
        with pytest.raises(Exception):
            service.update_task(task["id"], TaskUpdate(metadata={"x": 1}))

    def test_unclaim_clears_owner(self, service):
        task = service.create_task(TaskCreate(title="Unclaim me"))
        service.claim_task(task["id"], TaskClaim(agent_id="agent_1"))
        got = service.get_task(task["id"])
        assert got["owner_agent_id"] == "agent_1"
        # releasing back to todo must clear the owner claim
        service.update_task(task["id"], TaskUpdate(status=Status.TODO))
        got = service.get_task(task["id"])
        assert got["status"] == "todo"
        assert got["owner_agent_id"] is None


class TestAPI:
    def test_healthz(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_create_task(self, client):
        r = client.post(
            "/tasks",
            json={"title": "API task", "priority": "yellow", "status": "todo"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "API task"
        assert data["priority"] == "yellow"

    def test_list_and_filter(self, client):
        client.post("/tasks", json={"title": "A", "project": "p1"})
        client.post("/tasks", json={"title": "B", "project": "p2"})
        r = client.get("/tasks?project=p1")
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_update_metadata(self, client):
        r = client.post(
            "/tasks",
            json={
                "title": "Meta",
                "agent_id": "alpha",
                "metadata": {"retry": 1},
            },
        )
        tid = r.json()["id"]
        r = client.patch(
            f"/tasks/{tid}",
            json={"agent_id": "alpha", "metadata": {"retry": 2}},
        )
        assert r.status_code == 200
        assert r.json()["metadata"]["alpha"]["retry"] == 2

    def test_claim_409(self, client):
        r = client.post("/tasks", json={"title": "Lock"})
        tid = r.json()["id"]
        r = client.post(f"/tasks/{tid}/claim", json={"agent_id": "a1"})
        assert r.status_code == 200
        r = client.post(f"/tasks/{tid}/claim", json={"agent_id": "a2"})
        assert r.status_code == 409

    def test_complete_forbidden(self, client):
        r = client.post("/tasks", json={"title": "Complete"})
        tid = r.json()["id"]
        client.post(f"/tasks/{tid}/claim", json={"agent_id": "owner"})
        r = client.post(f"/tasks/{tid}/complete", json={"agent_id": "other"})
        assert r.status_code == 403
        r = client.post(f"/tasks/{tid}/complete", json={"agent_id": "owner"})
        assert r.status_code == 200
        assert r.json()["status"] == "done"

    def test_delete(self, client):
        r = client.post("/tasks", json={"title": "Delete me"})
        tid = r.json()["id"]
        r = client.delete(f"/tasks/{tid}")
        assert r.status_code == 204
        r = client.get(f"/tasks/{tid}")
        assert r.status_code == 404

    def test_validation(self, client):
        r = client.post("/tasks", json={"title": ""})
        assert r.status_code == 422


class TestPriorityColor:
    def test_priority_enum_roundtrip(self, client):
        for color in ["red", "orange", "yellow", "green", "blue", "white"]:
            r = client.post("/tasks", json={"title": f"c-{color}", "priority": color})
            assert r.status_code == 201, color
            assert r.json()["priority"] == color
