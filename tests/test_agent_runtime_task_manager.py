from __future__ import annotations

import json
import time

import pytest

from core.agent_runtime import CausationRef, TaskPrincipal, TaskStatus
from core.agent_runtime import task_store
from core.agent_runtime.task_manager import (
    TaskManagerError,
    acknowledge_cancel,
    claim_next,
    complete_task,
    create_task,
    get_task,
    observability_snapshot,
    request_cancel,
)


UID = "owner-task-tests"
CHAR = "character_task_tests"


@pytest.fixture(autouse=True)
def _fresh_process():
    task_store.reset_process_instance_for_tests("process-a")
    yield
    task_store.reset_process_instance_for_tests()


def _create(*, now: float = 1_000.0, **kwargs):
    return create_task(
        TaskPrincipal.reality(UID, CHAR),
        capability=kwargs.pop("capability", "local.read"),
        source=kwargs.pop("source", "reality_turn"),
        idempotency_key=kwargs.pop("idempotency_key", "request-1"),
        ttl_seconds=kwargs.pop("ttl_seconds", 300),
        now=now,
        **kwargs,
    )


def test_create_is_idempotent_and_conflicting_payload_is_rejected(sandbox):
    first, created = _create(causation_ref=CausationRef("reality_turn", "private-turn"))
    second, duplicate = _create(causation_ref=CausationRef("reality_turn", "private-turn"))

    assert created is True
    assert duplicate is False
    assert second["task_id"] == first["task_id"]
    assert second["status"] == TaskStatus.QUEUED.value
    assert second["causation_ref"]["kind"] == "reality_turn"
    assert "private-turn" not in sandbox.agent_runtime_task_state(UID, char_id=CHAR).read_text()

    with pytest.raises(TaskManagerError, match="idempotency_conflict"):
        _create(capability="local.write")


def test_tool_request_causation_is_not_labeled_reality_turn(sandbox):
    fingerprint = "a" * 64
    created, _ = _create(
        source="tool",
        causation_ref=CausationRef("tool_request", fingerprint),
        request_fingerprint=fingerprint,
        idempotency_key="tool-request-1",
    )
    assert created["causation_ref"]["kind"] == "tool_request"
    assert created["request_fingerprint"] == fingerprint
    with pytest.raises(TaskManagerError, match="invalid_causation_ref"):
        _create(
            source="tool",
            causation_ref=CausationRef("request_hash", fingerprint),
            idempotency_key="tool-request-bad",
        )
    raw = sandbox.agent_runtime_task_state(UID, char_id=CHAR).read_text(encoding="utf-8")
    snapshot = observability_snapshot(uid=UID, char_id=CHAR)
    assert snapshot["entries"][0]["causation_ref"]["kind"] == "tool_request"
    assert snapshot["entries"][0]["request_fingerprint"] == fingerprint
    assert snapshot["entries"][0]["causation_ref"]["kind"] != "reality_turn"
    assert "request_hash" not in raw


def test_restart_recovery_marks_running_unknown_without_replay(sandbox):
    created, _ = _create()
    lease = claim_next(TaskPrincipal.reality(UID, CHAR), now=1_001.0, lease_seconds=60)
    assert lease is not None

    task_store.reset_process_instance_for_tests("process-b")
    recovered = get_task(TaskPrincipal.reality(UID, CHAR), created["task_id"], now=1_002.0)
    assert recovered["status"] == TaskStatus.OUTCOME_UNKNOWN.value
    assert recovered["error_code"] == "worker_process_lost"
    assert recovered["recovery_reason"] == "process_restart"
    assert claim_next(TaskPrincipal.reality(UID, CHAR), now=1_002.0) is None
    with pytest.raises(TaskManagerError, match="lease_lost"):
        complete_task(TaskPrincipal.reality(UID, CHAR), lease, now=1_002.0)


def test_cancel_queued_and_running_tasks_have_stable_states(sandbox):
    queued, _ = _create(idempotency_key="queued")
    canceled = request_cancel(TaskPrincipal.reality(UID, CHAR), queued["task_id"], now=1_001.0)
    assert canceled["status"] == TaskStatus.CANCELED.value
    assert request_cancel(TaskPrincipal.reality(UID, CHAR), queued["task_id"], now=1_002.0)["status"] == TaskStatus.CANCELED.value

    running, _ = _create(idempotency_key="running")
    lease = claim_next(TaskPrincipal.reality(UID, CHAR), now=1_003.0)
    assert lease is not None and lease.task_id == running["task_id"]
    requested = request_cancel(TaskPrincipal.reality(UID, CHAR), running["task_id"], now=1_004.0)
    assert requested["status"] == TaskStatus.RUNNING.value
    assert requested["cancel_requested"] is True
    done = acknowledge_cancel(TaskPrincipal.reality(UID, CHAR), lease, now=1_005.0)
    assert done["status"] == TaskStatus.CANCELED.value


def test_ttl_and_lease_loss_are_observable_terminal_states(sandbox):
    expired, _ = _create(idempotency_key="expires", ttl_seconds=10)
    observed = get_task(TaskPrincipal.reality(UID, CHAR), expired["task_id"], now=1_010.0)
    assert observed["status"] == TaskStatus.EXPIRED.value
    assert observed["error_code"] == "task_ttl_expired"

    lease_lost, _ = _create(idempotency_key="lease-lost")
    lease = claim_next(TaskPrincipal.reality(UID, CHAR), now=1_011.0, lease_seconds=2)
    assert lease is not None and lease.task_id == lease_lost["task_id"]
    observed = get_task(TaskPrincipal.reality(UID, CHAR), lease_lost["task_id"], now=1_013.0)
    assert observed["status"] == TaskStatus.OUTCOME_UNKNOWN.value
    assert observed["error_code"] == "lease_lost"


def test_dream_principal_is_rejected_before_store_mutation(sandbox):
    with pytest.raises(TaskManagerError, match="realm_forbidden"):
        create_task(
            TaskPrincipal(uid=UID, char_id=CHAR, realm="dream"),
            capability="local.read",
            source="reality_turn",
            idempotency_key="dream-request",
            ttl_seconds=30,
        )
    assert not sandbox.agent_runtime_reality_root().exists()


def test_result_metadata_is_bounded_and_observation_is_content_free(sandbox):
    created, _ = _create(
        causation_ref=CausationRef("signal", "private-signal-id"),
        idempotency_key="bounded",
    )
    lease = claim_next(TaskPrincipal.reality(UID, CHAR), now=1_001.0)
    assert lease is not None
    receipt = complete_task(
        TaskPrincipal.reality(UID, CHAR),
        lease,
        result_metadata={"outcome_code": "ok", "counters": {"items": 2}},
        now=1_002.0,
    )
    assert receipt["result_metadata"] == {"outcome_code": "ok", "counters": {"items": 2}}

    raw = json.loads(sandbox.agent_runtime_task_state(UID, char_id=CHAR).read_text())
    text = json.dumps(raw)
    assert "private-signal-id" not in text
    assert lease.lease_token not in text
    assert "idempotency_key" not in text

    snapshot = observability_snapshot(uid=UID, char_id=CHAR)
    assert snapshot["status_counts"] == {"succeeded": 1}
    assert snapshot["entries"][0]["scope"]["uid_digest"] != UID
    assert "uid" not in snapshot["entries"][0]
    assert "lease_token" not in snapshot["entries"][0]


def test_event_context_observer_counters_are_unchanged(sandbox, monkeypatch):
    from core.event_context_observer import snapshot

    before = snapshot()
    _create(now=time.time())
    lease = claim_next(TaskPrincipal.reality(UID, CHAR), now=1_001.0)
    assert lease is not None
    complete_task(TaskPrincipal.reality(UID, CHAR), lease, now=1_002.0)
    assert snapshot()["counts"] == before["counts"]


def test_observability_endpoint_requires_state_read_and_returns_metadata(sandbox, monkeypatch):
    from fastapi.testclient import TestClient

    secret = "agent-runtime-observability-secret"
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: secret)
    _create(now=time.time())
    from admin.admin_server import app

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/observability/agent-runtime-tasks").status_code == 401
    response = client.get(
        "/observability/agent-runtime-tasks?uid=" + UID,
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["entries"][0]["status"] == "queued"
