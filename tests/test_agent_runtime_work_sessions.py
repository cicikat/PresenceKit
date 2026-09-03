from __future__ import annotations

import asyncio

import pytest


def _create(sandbox, capability="authored_diary", idempotency_key="work-request", **task_kwargs):
    from core.agent_runtime import CausationRef, TaskPrincipal
    from core.agent_runtime.task_manager import create_task

    principal = TaskPrincipal.reality("work-owner", "work-character")
    task, _ = create_task(
        principal,
        capability=capability,
        source="scheduler",
        idempotency_key=idempotency_key,
        ttl_seconds=300,
        causation_ref=CausationRef("signal", "inner_diary_write"),
        **task_kwargs,
    )
    return principal, task


def test_work_session_is_reality_only_and_metadata_redacted(sandbox):
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.work_sessions import (
        WorkSessionError,
        create_work_session,
        get_work_session,
    )

    principal, task = _create(sandbox)
    row = create_work_session(
        principal,
        task_id=task["task_id"],
        capability="authored_diary",
        artifact_kind="authored_diary",
        context="private diary context",
        idempotency_key="session-request",
    )
    assert row["status"] == "created"
    assert "private diary context" not in str(get_work_session(principal, row["work_session_id"]))
    duplicate = create_work_session(
        principal,
        task_id=task["task_id"], capability="authored_diary",
        artifact_kind="authored_diary", context="private diary context",
        idempotency_key="session-request",
    )
    assert duplicate["work_session_id"] == row["work_session_id"]
    with pytest.raises(WorkSessionError, match="idempotency_conflict"):
        create_work_session(
            principal,
            task_id=task["task_id"], capability="authored_diary",
            artifact_kind="authored_diary", context="different context",
            idempotency_key="session-request",
        )
    with pytest.raises(WorkSessionError, match="realm_forbidden"):
        create_work_session(
            TaskPrincipal(uid=principal.uid, char_id=principal.char_id, realm="dream"),
            task_id=task["task_id"], capability="authored_diary",
            artifact_kind="authored_diary", context="x", idempotency_key="dream",
        )


def test_run_work_session_failure_is_terminal_without_event_context(sandbox):
    from core.agent_runtime.work_sessions import create_work_session, run_work_session
    from core.agent_runtime.task_manager import claim_next
    from core.event_context_observer import snapshot

    principal, task = _create(sandbox, "document_summary", "summary-task")
    row = create_work_session(
        principal, task_id=task["task_id"], capability="document_summary",
        artifact_kind="document_summary", context="bounded", idempotency_key="run",
    )
    assert claim_next(principal, task_id=task["task_id"], capabilities={"document_summary"}) is not None
    before = snapshot()

    async def broken_worker():
        raise RuntimeError("worker boom")

    with pytest.raises(Exception):
        asyncio.run(run_work_session(principal, row["work_session_id"], broken_worker))
    after = snapshot()
    assert after["counts"] == before["counts"]


def test_running_session_recovers_unknown_and_manifest_is_enforced(sandbox):
    from core.agent_runtime.work_sessions import (
        WorkSessionError,
        create_work_session,
        get_work_session,
        recover_all_work_sessions,
        start_work_session,
    )
    from core.agent_runtime.task_manager import claim_next

    principal, task = _create(sandbox)
    with pytest.raises(WorkSessionError, match="artifact_kind_forbidden"):
        create_work_session(
            principal, task_id=task["task_id"], capability="authored_diary",
            artifact_kind="workspace_artifact", context="bounded", idempotency_key="wrong-kind",
        )
    row = create_work_session(
        principal, task_id=task["task_id"], capability="authored_diary",
        artifact_kind="authored_diary", context="bounded", idempotency_key="recover",
    )
    assert claim_next(principal, task_id=task["task_id"], capabilities={"authored_diary"}) is not None
    start_work_session(principal, row["work_session_id"])
    assert recover_all_work_sessions()["recovered"] == 1
    recovered = get_work_session(principal, row["work_session_id"])
    assert recovered["status"] == "outcome_unknown"
    assert recovered["error_code"] == "worker_process_lost"


def test_work_session_requires_task_lease_and_explicit_retry(sandbox):
    from core.agent_runtime.task_manager import claim_next, fail_task
    from core.agent_runtime.work_sessions import (
        WorkSessionError,
        create_work_session,
        get_work_session,
        retry_work_session,
        run_work_session,
    )

    principal, task = _create(
        sandbox, "workspace_artifact", "retry-task", retry_policy="safe", max_attempts=2,
    )
    row = create_work_session(
        principal, task_id=task["task_id"], capability="workspace_artifact",
        artifact_kind="workspace_artifact", context="bounded", idempotency_key="retry-session",
    )
    with pytest.raises(WorkSessionError, match="artifact_not_created"):
        from core.agent_runtime.work_sessions import complete_work_session
        complete_work_session(principal, row["work_session_id"])

    async def worker_without_artifact():
        return {}

    with pytest.raises(WorkSessionError, match="task_not_running"):
        asyncio.run(run_work_session(principal, row["work_session_id"], worker_without_artifact))
    lease = claim_next(principal, task_id=task["task_id"], capabilities={"workspace_artifact"})
    assert lease is not None
    with pytest.raises(WorkSessionError, match="artifact_not_created"):
        asyncio.run(run_work_session(principal, row["work_session_id"], worker_without_artifact))
    fail_task(principal, lease, error_code="artifact_not_created", retry=True)
    refreshed = create_work_session(
        principal, task_id=task["task_id"], capability="workspace_artifact",
        artifact_kind="workspace_artifact", context="fresher bounded input",
        idempotency_key="retry-session",
    )
    assert refreshed["context_digest"] != row["context_digest"]
    retried = retry_work_session(principal, row["work_session_id"])
    assert retried["status"] == "created"
    assert retried["attempt_count"] == 1
    assert get_work_session(principal, row["work_session_id"])["artifact_id"] is None


def test_work_session_module_has_no_interaction_or_memory_writer_dependency():
    import inspect
    from core.agent_runtime import work_sessions

    source = inspect.getsource(work_sessions)
    forbidden = (
        "record_assistant_turn", "capture_turn", "short_term", "event_log",
        "episodic_memory", "user_identity", "event_context_observer",
    )
    assert all(name not in source for name in forbidden)


def test_work_session_observability_requires_state_read(sandbox, monkeypatch):
    from fastapi.testclient import TestClient

    secret = "work-session-observability-secret"
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: secret)
    principal, task = _create(sandbox, idempotency_key="observed-task")
    from core.agent_runtime.work_sessions import create_work_session
    create_work_session(
        principal, task_id=task["task_id"], capability="authored_diary",
        artifact_kind="authored_diary", context="redacted input", idempotency_key="observed-session",
    )
    from admin.admin_server import app

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/observability/agent-runtime-work-sessions").status_code == 401
    response = client.get(
        "/observability/agent-runtime-work-sessions?uid=work-owner",
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert "redacted input" not in str(payload)
