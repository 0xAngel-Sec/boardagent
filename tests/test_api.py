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
    load_api_keys,
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
        # releasing back to todo must clear the owner claim; the owning
        # agent (or an admin) is required to release
        service.update_task(
            task["id"], TaskUpdate(status=Status.TODO, agent_id="agent_1")
        )
        got = service.get_task(task["id"])
        assert got["status"] == "todo"
        assert got["owner_agent_id"] is None

    def test_unclaim_requires_owner(self, service):
        # Ownership is enforced even without a caller_role (direct service
        # calls / unauthenticated MCP): a non-owner cannot release a claim.
        task = service.create_task(TaskCreate(title="Locked"))
        service.claim_task(task["id"], TaskClaim(agent_id="agent_1"))
        with pytest.raises(NotOwnerError):
            service.update_task(
                task["id"], TaskUpdate(status=Status.TODO, agent_id="agent_2")
            )
        got = service.get_task(task["id"])
        assert got["status"] == "in_progress"
        assert got["owner_agent_id"] == "agent_1"


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

                "links": ["https://example.com", "C:/repo/file.py"],
                "acceptance_criteria": "works on prod",
                "dependencies": ["#12", "#34"],
                "notes": ["started", "blocked on review"],
                "custom_fields": {"reviewer": "kimi", "ticket": "BA-42"},
            },
            headers=_auth(keys_file),
        )
        assert r.status_code == 201
        task = r.json()
        assert task["description"] == "do the thing"
        assert task["tags"] == ["urgent", "backend"]
        assert task["estimate"] == "2h"

        assert task["links"] == ["https://example.com", "C:/repo/file.py"]
        assert task["acceptance_criteria"] == "works on prod"
        assert task["dependencies"] == ["#12", "#34"]
        assert task["notes"] == ["started", "blocked on review"]
        assert task["custom_fields"] == {"reviewer": "kimi", "ticket": "BA-42"}

    def test_task_fields_default_empty(self, client, keys_file):
        r = client.post("/tasks", json={"title": "bare"}, headers=_auth(keys_file))
        assert r.status_code == 201
        task = r.json()
        assert task["tags"] == []
        assert task["estimate"] is None

        assert task["links"] == []
        assert task["acceptance_criteria"] is None
        assert task["dependencies"] == []
        assert task["notes"] == []
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

                "links": ["https://x.dev"],
                "acceptance_criteria": "no regressions",
                "dependencies": ["#1"],
                "notes": ["reproduced"],
                "custom_fields": {"x": "1"},
            },
            headers=_auth(keys_file),
        )
        assert r.status_code == 200
        task = r.json()
        assert task["description"] == "updated"
        assert task["tags"] == ["a", "b"]
        assert task["estimate"] == "1d"

        assert task["links"] == ["https://x.dev"]
        assert task["acceptance_criteria"] == "no regressions"
        assert task["dependencies"] == ["#1"]
        assert task["notes"] == ["reproduced"]
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
            json={
                "title": "t",
                "tags": ["x"],
                "estimate": "2h",

                "links": ["https://x.dev"],
                "acceptance_criteria": "ac",
                "dependencies": ["#1"],
                "notes": ["n1"],
                "custom_fields": {"k": "v"},
            },
            headers=_auth(keys_file),
        )
        tid = r.json()["id"]
        r = client.patch(
            f"/tasks/{tid}",
            json={
                "tags": [],
                "estimate": "",

                "links": [],
                "acceptance_criteria": "",
                "dependencies": [],
                "notes": [],
                "custom_fields": {},
            },
            headers=_auth(keys_file),
        )
        assert r.status_code == 200
        task = r.json()
        assert task["tags"] == []
        assert task["estimate"] == ""

        assert task["links"] == []
        assert task["acceptance_criteria"] == ""
        assert task["dependencies"] == []
        assert task["notes"] == []
        assert task["custom_fields"] == {}

    def test_task_fields_survive_claim_complete(self, client, keys_file):
        r = client.post(
            "/tasks",
            json={
                "title": "t",
                "tags": ["keep"],
                "estimate": "1h",

                "links": ["https://x.dev"],
                "acceptance_criteria": "ac",
                "dependencies": ["#2"],
                "notes": ["n"],
                "custom_fields": {"k": "v"},
            },
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

        assert task["links"] == ["https://x.dev"]
        assert task["acceptance_criteria"] == "ac"
        assert task["dependencies"] == ["#2"]
        assert task["notes"] == ["n"]
        assert task["custom_fields"] == {"k": "v"}


class TestReviewFixes:
    """Regression tests for the ds-v4-pro adversarial review findings."""

    def test_patch_status_cannot_bypass_ownership(self, client, keys_file):
        # Finding #2: PATCH status must not bypass claim/complete ownership.
        # Use a write-role key (admin legitimately bypasses ownership).
        r = client.post(
            "/keys", json={"name": "writer", "role": "write"}, headers=_auth(keys_file)
        )
        write_key = r.json()["key"]

        r = client.post("/tasks", json={"title": "owned"}, headers=_auth(write_key))
        tid = r.json()["id"]
        client.post(f"/tasks/{tid}/claim", json={"agent_id": "owner"}, headers=_auth(write_key))

        # A write-role caller cannot mark someone else's task done via PATCH.
        r = client.patch(f"/tasks/{tid}", json={"status": "done"}, headers=_auth(write_key))
        assert r.status_code == 400
        # Cannot release someone else's claim.
        r = client.patch(f"/tasks/{tid}", json={"status": "todo"}, headers=_auth(write_key))
        assert r.status_code == 400
        # Cannot set in_progress on an unowned task (must use claim).
        r = client.post("/tasks", json={"title": "fresh"}, headers=_auth(write_key))
        tid2 = r.json()["id"]
        r = client.patch(f"/tasks/{tid2}", json={"status": "in_progress"}, headers=_auth(write_key))
        assert r.status_code == 400

    def test_null_clears_field(self, client, keys_file):
        # Finding #3: explicit null must clear a nullable field.
        r = client.post(
            "/tasks",
            json={"title": "t", "description": "keep me", "estimate": "2h"},
            headers=_auth(keys_file),
        )
        tid = r.json()["id"]
        r = client.patch(
            f"/tasks/{tid}", json={"description": None, "estimate": None}, headers=_auth(keys_file)
        )
        assert r.status_code == 200
        task = r.json()
        assert task["description"] is None
        assert task["estimate"] is None

    def test_extra_forbid_on_create(self, client, keys_file):
        # Finding #16: typo'd fields must be rejected loudly, not silently dropped.
        r = client.post(
            "/tasks", json={"title": "t", "priorty": "red"}, headers=_auth(keys_file)
        )
        assert r.status_code == 422

    def test_pagination(self, client, keys_file):
        # Finding #11: limit/offset must work.
        for i in range(5):
            client.post("/tasks", json={"title": f"t{i}"}, headers=_auth(keys_file))
        r = client.get("/tasks?limit=2&offset=1", headers=_auth(keys_file))
        assert r.status_code == 200
        assert r.json()["count"] == 2
        r = client.get("/tasks?limit=1000", headers=_auth(keys_file))
        assert r.status_code == 422  # cap at 500

    def test_due_normalized_utc(self, client, keys_file):
        # Finding #13: naive due datetimes are stored UTC-aware.
        r = client.post(
            "/tasks",
            json={"title": "t", "due": "2026-09-01T10:00:00"},
            headers=_auth(keys_file),
        )
        assert r.status_code == 201
        due = r.json()["due"]
        assert due.endswith("+00:00") or due.endswith("Z"), due

    def test_console_key_cannot_be_deleted(self, client, keys_file):
        """The console key is protected: deleting it via API is rejected.

        Without this, a user could delete every key (including the TUI's
        own credential) and lock the local UI out of the API.
        """
        from boardagent.api import _ensure_console_key

        console_key = _ensure_console_key()
        r = client.delete(f"/keys/{console_key}", headers=_auth(keys_file))
        assert r.status_code == 400
        # key still works
        assert console_key in load_api_keys()

    def test_console_key_self_heals(self, tmp_path, monkeypatch):
        """If the console key was deleted from keys.json, re-register it.

        The TUI's _load_console_key and the server's _ensure_console_key both
        heal this state, so the app can never be locked out permanently.
        """
        from boardagent import config as cfg
        from boardagent.api import _ensure_console_key

        monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
        admin_key = generate_api_key()
        save_api_keys({admin_key: {"name": "admin", "role": ROLE_ADMIN}})

        console_key = _ensure_console_key()
        assert console_key in load_api_keys()

        # Simulate the lockout: console key wiped from keys.json.
        save_api_keys({admin_key: {"name": "admin", "role": ROLE_ADMIN}})
        assert console_key not in load_api_keys()

        # Self-heal: same settings key is re-registered.
        healed = _ensure_console_key()
        assert healed == console_key
        assert healed in load_api_keys()

    def test_claim_race_guard(self, service):
        # Finding #1: second claim on the same task must fail atomically.
        task = service.create_task(TaskCreate(title="race"))
        service.claim_task(task["id"], TaskClaim(agent_id="a1"))
        with pytest.raises(AlreadyClaimedError):
            service.claim_task(task["id"], TaskClaim(agent_id="a2"))
        got = service.get_task(task["id"])
        assert got["owner_agent_id"] == "a1"

    def test_owner_can_transition_own_task_via_patch(self, client, keys_file):
        # GLM finding #2: the owning agent (via agent_id) can PATCH their own
        # claimed task's status — block, release, etc.
        r = client.post(
            "/keys", json={"name": "writer", "role": "write"}, headers=_auth(keys_file)
        )
        write_key = r.json()["key"]
        r = client.post("/tasks", json={"title": "mine"}, headers=_auth(write_key))
        tid = r.json()["id"]
        client.post(f"/tasks/{tid}/claim", json={"agent_id": "me"}, headers=_auth(write_key))

        r = client.patch(
            f"/tasks/{tid}",
            json={"status": "blocked", "agent_id": "me"},
            headers=_auth(write_key),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "blocked"

        # Release back to todo clears the claim.
        r = client.patch(
            f"/tasks/{tid}",
            json={"status": "todo", "agent_id": "me"},
            headers=_auth(write_key),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "todo"
        assert r.json()["owner_agent_id"] is None

    def test_admin_cannot_deadlock_unowned_task(self, client, keys_file):
        # GLM finding #3: even admin cannot set in_progress on an unowned
        # task — that would create an unclaimable, uncompletable deadlock.
        r = client.post("/tasks", json={"title": "fresh"}, headers=_auth(keys_file))
        tid = r.json()["id"]
        r = client.patch(f"/tasks/{tid}", json={"status": "in_progress"}, headers=_auth(keys_file))
        assert r.status_code == 400
        got = client.get(f"/tasks/{tid}", headers=_auth(keys_file)).json()
        assert got["status"] == "todo"
        assert got["owner_agent_id"] is None

    def test_null_custom_fields_clears_not_500(self, client, keys_file):
        # GLM re-check finding #1: custom_fields:null must clear to {} (dict),
        # not [] (list) — [] breaks the response model with a 500.
        r = client.post(
            "/tasks",
            json={"title": "t", "custom_fields": {"k": "v"}},
            headers=_auth(keys_file),
        )
        tid = r.json()["id"]
        r = client.patch(
            f"/tasks/{tid}", json={"custom_fields": None}, headers=_auth(keys_file)
        )
        assert r.status_code == 200
        assert r.json()["custom_fields"] == {}

    def test_patch_done_on_unowned_rejected(self, client, keys_file):
        # GLM re-check finding #3: PATCH done on an unowned task must be
        # rejected — it would bypass the complete_task ownership invariant.
        r = client.post(
            "/keys", json={"name": "writer", "role": "write"}, headers=_auth(keys_file)
        )
        write_key = r.json()["key"]
        r = client.post("/tasks", json={"title": "unowned"}, headers=_auth(write_key))
        tid = r.json()["id"]
        r = client.patch(f"/tasks/{tid}", json={"status": "done"}, headers=_auth(write_key))
        assert r.status_code == 400
        got = client.get(f"/tasks/{tid}", headers=_auth(write_key)).json()
        assert got["status"] == "todo"
