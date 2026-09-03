from __future__ import annotations

import asyncio

import pytest


def _create(sandbox):
    from core.agent_runtime import CausationRef, TaskPrincipal
    from core.agent_runtime.task_manager import create_task

    principal = TaskPrincipal.reality("work-owner", "work-character")
    task, _ = create_task(
        principal,
        capability="authored_diary",
        source="scheduler",
        idempotency_key="work-request",
        ttl_seconds=300,
        causation_ref=CausationRef("signal", "inner_diary_write"),
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
    from core.event_context_observer import snapshot

    principal, task = _create(sandbox)
    row = create_work_session(
        principal, task_id=task["task_id"], capability="document_summary",
        artifact_kind="document_summary", context="bounded", idempotency_key="run",
    )
    before = snapshot()

    async def broken_worker():
        raise RuntimeError("worker boom")

    with pytest.raises(Exception):
        asyncio.run(run_work_session(principal, row["work_session_id"], broken_worker))
    after = snapshot()
    assert after["counts"] == before["counts"]
