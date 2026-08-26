"""Tests for BoardAgent service and API layers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from boardagent.api import create_app
from boardagent.config import (
    ROLE_ADMIN,
    ROLE_READ,
    ROLE_WRITE,
    generate_api_key,
    save_api_keys,
    save_server_settings,
)
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
def keys_file(tmp_path, monkeypatch):
    """Isolate keys.json in a temp dir and seed an admin key."""
    from boardagent import config as cfg

    monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
    admin_key = generate_api_key()
    save_api_keys({admin_key: {"name": "admin", "role": ROLE_ADMIN}})
    return admin_key


@pytest.fixture
def client(service, keys_file):
    return TestClient(create_app(service))


def _auth(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


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

    def test_create_task(self, client, keys_file):
        r = client.post(
            "/tasks",
            json={"title": "API task", "priority": "yellow", "status": "todo"},
            headers=_auth(keys_file),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "API task"
        assert data["priority"] == "yellow"

    def test_list_and_filter(self, client, keys_file):
        client.post("/tasks", json={"title": "A", "project": "p1"}, headers=_auth(keys_file))
        client.post("/tasks", json={"title": "B", "project": "p2"}, headers=_auth(keys_file))
        r = client.get("/tasks?project=p1", headers=_auth(keys_file))
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_update_metadata(self, client, keys_file):
        r = client.post(
            "/tasks",
            json={
                "title": "Meta",
                "agent_id": "alpha",
                "metadata": {"retry": 1},
            },
            headers=_auth(keys_file),
        )
        tid = r.json()["id"]
        r = client.patch(
            f"/tasks/{tid}",
            json={"agent_id": "alpha", "metadata": {"retry": 2}},
            headers=_auth(keys_file),
        )
        assert r.status_code == 200
        assert r.json()["metadata"]["alpha"]["retry"] == 2

    def test_claim_409(self, client, keys_file):
        r = client.post("/tasks", json={"title": "Lock"}, headers=_auth(keys_file))
        tid = r.json()["id"]
        r = client.post(f"/tasks/{tid}/claim", json={"agent_id": "a1"}, headers=_auth(keys_file))
        assert r.status_code == 200
        r = client.post(f"/tasks/{tid}/claim", json={"agent_id": "a2"}, headers=_auth(keys_file))
        assert r.status_code == 409

    def test_complete_forbidden(self, client, keys_file):
        r = client.post("/tasks", json={"title": "Complete"}, headers=_auth(keys_file))
        tid = r.json()["id"]
        client.post(f"/tasks/{tid}/claim", json={"agent_id": "owner"}, headers=_auth(keys_file))
        r = client.post(f"/tasks/{tid}/complete", json={"agent_id": "other"}, headers=_auth(keys_file))
        assert r.status_code == 403
        r = client.post(f"/tasks/{tid}/complete", json={"agent_id": "owner"}, headers=_auth(keys_file))
        assert r.status_code == 200
        assert r.json()["status"] == "done"

    def test_delete(self, client, keys_file):
        r = client.post("/tasks", json={"title": "Delete me"}, headers=_auth(keys_file))
        tid = r.json()["id"]
        r = client.delete(f"/tasks/{tid}", headers=_auth(keys_file))
        assert r.status_code == 204
        r = client.get(f"/tasks/{tid}", headers=_auth(keys_file))
        assert r.status_code == 404

    def test_validation(self, client, keys_file):
        r = client.post("/tasks", json={"title": ""}, headers=_auth(keys_file))
        assert r.status_code == 422


class TestAuth:
    def test_no_key_rejected(self, client):
        r = client.get("/tasks")
        assert r.status_code == 401
        r = client.post("/tasks", json={"title": "x"})
        assert r.status_code == 401

    def test_bad_key_rejected(self, client):
        r = client.get("/tasks", headers=_auth("ba_wrong"))
        assert r.status_code == 401

    def test_read_key_cannot_write(self, client, keys_file, tmp_path):
        from boardagent import config as cfg

        read_key = generate_api_key()
        save_api_keys(
            {
                keys_file: {"name": "admin", "role": ROLE_ADMIN},
                read_key: {"name": "reader", "role": ROLE_READ},
            }
        )
        # read is fine
        r = client.get("/tasks", headers=_auth(read_key))
        assert r.status_code == 200
        # write is forbidden
        r = client.post("/tasks", json={"title": "nope"}, headers=_auth(read_key))
        assert r.status_code == 403
        # admin endpoints forbidden
        r = client.get("/keys", headers=_auth(read_key))
        assert r.status_code == 403

    def test_write_key_cannot_admin(self, client, keys_file):
        from boardagent import config as cfg

        write_key = generate_api_key()
        save_api_keys(
            {
                keys_file: {"name": "admin", "role": ROLE_ADMIN},
                write_key: {"name": "writer", "role": ROLE_WRITE},
            }
        )
        r = client.post("/tasks", json={"title": "ok"}, headers=_auth(write_key))
        assert r.status_code == 201
        r = client.delete("/tasks/1", headers=_auth(write_key))
        assert r.status_code == 403
        r = client.get("/keys", headers=_auth(write_key))
        assert r.status_code == 403

    def test_admin_can_delete(self, client, keys_file):
        r = client.post("/tasks", json={"title": "doomed"}, headers=_auth(keys_file))
        tid = r.json()["id"]
        r = client.delete(f"/tasks/{tid}", headers=_auth(keys_file))
        assert r.status_code == 204


class TestSettingsAndKeys:
    def test_get_settings(self, client, keys_file):
        r = client.get("/settings", headers=_auth(keys_file))
        assert r.status_code == 200
        data = r.json()
        assert data["api_enabled"] is True
        assert data["mcp_enabled"] is True

    def test_put_settings_requires_admin(self, client, keys_file):
        r = client.put(
            "/settings",
            json={"host": "0.0.0.0", "port": 9999, "api_enabled": False, "mcp_enabled": True},
            headers=_auth(keys_file),
        )
        assert r.status_code == 200
        assert r.json()["port"] == 9999

    def test_create_list_delete_key(self, client, keys_file):
        r = client.post(
            "/keys", json={"name": "agent-1", "role": "write"}, headers=_auth(keys_file)
        )
        assert r.status_code == 201
        new_key = r.json()["key"]
        assert new_key.startswith("ba_")
        assert r.json()["role"] == "write"

        r = client.get("/keys", headers=_auth(keys_file))
        assert r.status_code == 200
        keys = r.json()
        assert any(k["key"] == new_key for k in keys)

        r = client.delete(f"/keys/{new_key}", headers=_auth(keys_file))
        assert r.status_code == 204
        r = client.get("/keys", headers=_auth(keys_file))
        assert not any(k["key"] == new_key for k in r.json())

    def test_new_key_works_with_role(self, client, keys_file):
        r = client.post(
            "/keys", json={"name": "reader-2", "role": "read"}, headers=_auth(keys_file)
        )
        read_key = r.json()["key"]
        r = client.get("/tasks", headers=_auth(read_key))
        assert r.status_code == 200
        r = client.post("/tasks", json={"title": "x"}, headers=_auth(read_key))
        assert r.status_code == 403


class TestPriorityColor:
    def test_priority_enum_roundtrip(self, client, keys_file):
        for color in ["red", "orange", "yellow", "green", "blue", "white"]:
            r = client.post("/tasks", json={"title": f"c-{color}", "priority": color}, headers=_auth(keys_file))
            assert r.status_code == 201, color
            assert r.json()["priority"] == color


class TestTaskFields:
    def test_create_task_with_all_fields(self, client, keys_file):
        r = client.post(
            "/tasks",
            json={
                "title": "ship it",
                "description": "do the thing",
                "tags": ["urgent", "backend"],
                "estimate": "2h",
                "custom_fields": {"reviewer": "kimi", "ticket": "BA-42"},
            },
            headers=_auth(keys_file),
        )
        assert r.status_code == 201
        task = r.json()
        assert task["description"] == "do the thing"
        assert task["tags"] == ["urgent", "backend"]
        assert task["estimate"] == "2h"
        assert task["custom_fields"] == {"reviewer": "kimi", "ticket": "BA-42"}

    def test_task_fields_default_empty(self, client, keys_file):
        r = client.post("/tasks", json={"title": "bare"}, headers=_auth(keys_file))
        assert r.status_code == 201
        task = r.json()
        assert task["tags"] == []
        assert task["estimate"] is None
        assert task["custom_fields"] == {}

    def test_update_task_fields(self, client, keys_file):
        r = client.post("/tasks", json={"title": "t"}, headers=_auth(keys_file))
        tid = r.json()["id"]
        r = client.patch(
            f"/tasks/{tid}",
            json={
                "description": "updated",
                "tags": ["a", "b"],
                "estimate": "1d",
                "custom_fields": {"x": "1"},
            },
            headers=_auth(keys_file),
        )
        assert r.status_code == 200
        task = r.json()
        assert task["description"] == "updated"
        assert task["tags"] == ["a", "b"]
        assert task["estimate"] == "1d"
        assert task["custom_fields"] == {"x": "1"}

    def test_update_task_fields_partial(self, client, keys_file):
        r = client.post(
            "/tasks",
            json={"title": "t", "tags": ["keep"], "estimate": "30m"},
            headers=_auth(keys_file),
        )
        tid = r.json()["id"]
        r = client.patch(
            f"/tasks/{tid}", json={"estimate": "1h"}, headers=_auth(keys_file)
        )
        assert r.status_code == 200
        task = r.json()
        # untouched fields survive a partial update
        assert task["tags"] == ["keep"]
        assert task["estimate"] == "1h"

    def test_clear_task_fields(self, client, keys_file):
        r = client.post(
            "/tasks",
            json={"title": "t", "tags": ["x"], "estimate": "2h", "custom_fields": {"k": "v"}},
            headers=_auth(keys_file),
        )
        tid = r.json()["id"]
        r = client.patch(
            f"/tasks/{tid}",
            json={"tags": [], "estimate": "", "custom_fields": {}},
            headers=_auth(keys_file),
        )
        assert r.status_code == 200
        task = r.json()
        assert task["tags"] == []
        assert task["estimate"] == ""
        assert task["custom_fields"] == {}

    def test_task_fields_survive_claim_complete(self, client, keys_file):
        r = client.post(
            "/tasks",
            json={"title": "t", "tags": ["keep"], "estimate": "1h", "custom_fields": {"k": "v"}},
            headers=_auth(keys_file),
        )
        tid = r.json()["id"]
        r = client.post(f"/tasks/{tid}/claim", json={"agent_id": "angel"}, headers=_auth(keys_file))
        assert r.status_code == 200
        r = client.post(f"/tasks/{tid}/complete", json={"agent_id": "angel"}, headers=_auth(keys_file))
        assert r.status_code == 200
        task = r.json()
        assert task["tags"] == ["keep"]
        assert task["estimate"] == "1h"
        assert task["custom_fields"] == {"k": "v"}
