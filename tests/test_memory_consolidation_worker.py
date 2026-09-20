from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


def _principal(uid: str = "consolidation-owner", char_id: str = TEST_CHAR_ID):
    from core.agent_runtime.models import TaskPrincipal

    return TaskPrincipal.reality(uid, char_id)


def _scope(uid: str = "consolidation-owner", char_id: str = TEST_CHAR_ID):
    from core.memory.scope import MemoryScope

    return MemoryScope.reality_scope(uid, char_id)


def _event(uid: str = "consolidation-owner", char_id: str = TEST_CHAR_ID, *, suffix: str = "1") -> str:
    from core.memory.event_store import append_event

    event_id = f"consolidation-event-{suffix}"
    scope = _scope(uid, char_id)
    result = append_event(scope, {
        "event_id": event_id,
        "turn_id": event_id,
        "seq": 1,
        "occurred_at": 100.0,
        "ingested_at": 101.0,
        "uid": uid,
        "char_id": char_id,
        "realm": "reality",
        "kind": "owner_chat",
        "actor": "user",
        "channel": "desktop",
        "source": "fixture",
        "visible_text": f"bounded evidence {suffix}",
        "memory_text": f"bounded evidence {suffix}",
        "redaction_state": "scrubbed",
    })
    assert result.ok
    return event_id


def _task(principal, *, key: str = "consolidate"):
    from core.agent_runtime.task_manager import create_task

    task, _ = create_task(
        principal,
        capability="memory.consolidation",
        source="maintenance_trigger",
        idempotency_key=key,
        ttl_seconds=86400,
        retry_policy="safe",
        max_attempts=3,
    )
    return task


def _cfg(**overrides):
    from core.memory.consolidation_worker import _DEFAULTS

    value = dict(_DEFAULTS)
    value.update({"enabled": True, "call_timeout_seconds": 10})
    value.update(overrides)
    return value


def _patch_runtime(monkeypatch, *, response: str = "[]"):
    async def fake_chat(*_args, **_kwargs):
        return response

    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    monkeypatch.setattr(
        "core.model_registry.resolve_category_info",
        lambda *_args, **_kwargs: {"model": "fixture-model", "effective_preset": "fixture-preset"},
    )
    monkeypatch.setattr(
        "core.memory.consolidation_worker._identity_context",
        lambda _char_id: ('{"name":"fixture"}', "identity-revision"),
    )
    monkeypatch.setattr("core.memory.consolidation_worker._foreground_active", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("core.memory.consolidation_worker.config", lambda: _cfg())


def test_no_evidence_completes_without_model_call(sandbox, monkeypatch):
    from core.agent_runtime import task_manager
    from core.memory import consolidation_worker

    principal = _principal()
    task = _task(principal)
    lease = task_manager.claim_next(principal, task_id=task["task_id"], capabilities={"memory.consolidation"})
    calls = 0

    async def forbidden(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("zero evidence must not call a model")

    monkeypatch.setattr("core.llm_client.chat", forbidden)
    result = asyncio.run(consolidation_worker._run_claimed(principal, lease, _cfg()))
    assert result["status"] == "succeeded", json.dumps(result, sort_keys=True)
    assert result["result_metadata"]["counters"]["model_calls"] == 0
    assert calls == 0


def test_success_is_silent_and_commits_checkpoint(sandbox, monkeypatch):
    from core.agent_runtime import task_manager
    from core.event_context_observer import snapshot
    from core.memory import consolidation_worker, dossiers

    _patch_runtime(monkeypatch)
    principal = _principal()
    _event()
    task = _task(principal)
    lease = task_manager.claim_next(principal, task_id=task["task_id"], capabilities={"memory.consolidation"})
    before = snapshot()
    result = asyncio.run(consolidation_worker._run_claimed(principal, lease, _cfg()))

    assert result["status"] == "succeeded", json.dumps(result, sort_keys=True)
    assert dossiers.maintenance_checkpoint(_scope()) == 1
    assert dossiers.maintenance_candidates(_scope()) == []
    assert snapshot()["counts"] == before["counts"]
    assert result["result_metadata"]["outcome_code"] == "consolidation_committed"


def test_related_dossiers_are_injected_into_maintenance_prompt(sandbox, monkeypatch):
    from core.agent_runtime import task_manager
    from core.memory import consolidation_worker, dossiers

    principal = _principal(uid="related-prompt-owner")
    event_id = _event(principal.uid, suffix="related")
    from tests.test_memory_dossiers import _create, _op_id, _scope as dossier_scope
    scope = dossier_scope(uid=principal.uid)
    dossier_id, _ = _create(scope, title="Existing Topic")
    occurrence_id = uuid.uuid4().hex
    dossiers.apply_operations(scope, [
        {"action": "create_occurrence", "occurrence_id": occurrence_id, "participants": [],
         "time_certainty": "unknown", "assertion_kind": "user_stated",
         "evidence": [{"reference_kind": "event", "source_id": event_id, "source_revision": "1"}]},
        {"action": "attach_occurrence", "dossier_id": dossier_id, "occurrence_id": occurrence_id,
         "expected_revision": 1},
    ], operation_id=_op_id(), actor="character", chain="owner_chat")
    captured = {}

    async def fake_chat(messages, **_kwargs):
        captured["prompt"] = messages[0]["content"]
        return "[]"

    _patch_runtime(monkeypatch)
    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    task = _task(principal, key="related-prompt")
    lease = task_manager.claim_next(principal, task_id=task["task_id"], capabilities={"memory.consolidation"})
    result = asyncio.run(consolidation_worker._run_claimed(principal, lease, _cfg()))
    assert result["status"] == "succeeded", json.dumps(result, sort_keys=True)
    assert dossier_id in captured["prompt"]
    assert "Related dossiers for this batch" in captured["prompt"]
    assert "not idempotency keys" in captured["prompt"]


@pytest.mark.parametrize("control", ["cancel", "pause"])
def test_running_control_is_honored_before_commit(sandbox, monkeypatch, control):
    from core.agent_runtime import task_manager
    from core.memory import consolidation_worker, dossiers

    principal = _principal(uid=f"control-{control}")
    _event(principal.uid, suffix=control)
    task = _task(principal, key=control)
    lease = task_manager.claim_next(principal, task_id=task["task_id"], capabilities={"memory.consolidation"})

    async def controlled_chat(*_args, **_kwargs):
        if control == "cancel":
            task_manager.request_cancel(principal, task["task_id"])
        else:
            task_manager.request_pause(principal, task["task_id"])
        return "[]"

    _patch_runtime(monkeypatch)
    monkeypatch.setattr("core.llm_client.chat", controlled_chat)
    result = asyncio.run(consolidation_worker._run_claimed(principal, lease, _cfg()))
    assert result["status"] == ("canceled" if control == "cancel" else "paused")
    assert dossiers.maintenance_checkpoint(_scope(principal.uid)) == 0


def test_foreground_and_scope_budget_prevent_model_call(sandbox, monkeypatch):
    from core.agent_runtime import task_manager
    from core.memory import consolidation_worker, dossiers

    principal = _principal(uid="foreground-owner")
    _event(principal.uid, suffix="foreground")
    task = _task(principal, key="foreground")
    lease = task_manager.claim_next(principal, task_id=task["task_id"], capabilities={"memory.consolidation"})
    monkeypatch.setattr("core.memory.consolidation_worker._foreground_active", lambda *_args, **_kwargs: True)
    result = asyncio.run(consolidation_worker._run_claimed(principal, lease, _cfg()))
    assert result["status"] == "queued"
    assert result["error_code"] == "foreground_active"

    budget_principal = _principal(uid="budget-owner")
    _event(budget_principal.uid, suffix="budget")
    budget_task = _task(budget_principal, key="budget")
    budget_lease = task_manager.claim_next(
        budget_principal, task_id=budget_task["task_id"], capabilities={"memory.consolidation"},
    )
    dossiers.begin_maintenance_run(
        _scope(budget_principal.uid), run_id=uuid.uuid4().hex, task_id=uuid.uuid4().hex,
        work_session_id=uuid.uuid4().hex, input_count=1, input_chars=1, token_budget=1200,
        model="fixture", preset="fixture", identity_revision="identity", prompt_revision="prompt",
    )
    _patch_runtime(monkeypatch)
    result = asyncio.run(consolidation_worker._run_claimed(
        budget_principal, budget_lease, _cfg(per_scope_daily_calls=1),
    ))
    assert result["status"] == "queued"
    assert result["error_code"] == "scope_budget_exhausted"


def test_provider_limit_is_counted_and_backed_off(sandbox, monkeypatch):
    from core.agent_runtime import task_manager
    from core.memory import consolidation_worker

    principal = _principal(uid="limited-owner")
    _event(principal.uid, suffix="limited")
    task = _task(principal, key="limited")
    lease = task_manager.claim_next(principal, task_id=task["task_id"], capabilities={"memory.consolidation"})

    async def limited(*_args, **_kwargs):
        raise RuntimeError("provider 429 rate limit")

    _patch_runtime(monkeypatch)
    monkeypatch.setattr("core.llm_client.chat", limited)
    result = asyncio.run(consolidation_worker._run_claimed(principal, lease, _cfg()))
    assert result["status"] == "queued"
    assert result["error_code"] == "provider_limited"
    state = consolidation_worker._state()
    assert state["last_error"] == "provider_limited"
    assert state["backoff_until"] > 0
    assert sum(day.get("calls", 0) for day in state["days"].values()) == 1


def test_committed_unknown_outcome_reconciles_from_durable_receipt(sandbox):
    from core.agent_runtime import task_manager, work_sessions
    from core.memory import consolidation_worker, dossiers

    principal = _principal(uid="reconcile-owner")
    scope = _scope(principal.uid)
    event_id = _event(principal.uid, suffix="receipt")
    task = _task(principal, key="receipt")
    lease = task_manager.claim_next(principal, task_id=task["task_id"], capabilities={"memory.consolidation"})
    session = work_sessions.create_work_session(
        principal, task_id=task["task_id"], capability="memory.consolidation",
        artifact_kind="memory_consolidation_receipt", context="bounded", idempotency_key="receipt-session",
    )
    work_sessions.start_work_session(principal, session["work_session_id"])
    run_id = uuid.uuid4().hex
    dossiers.begin_maintenance_run(
        scope, run_id=run_id, task_id=task["task_id"], work_session_id=session["work_session_id"],
        input_count=1, input_chars=10, token_budget=100, model="fixture", preset="fixture",
        identity_revision="identity", prompt_revision="prompt",
    )
    candidate = dossiers.maintenance_candidates(scope)[0]
    dossiers.apply_operations(
        scope, [], operation_id=uuid.uuid4().hex, actor="character", chain="maintenance",
        processing_items=[{key: candidate[key] for key in (
            "store_kind", "source_id", "source_revision", "ingest_sequence", "input_digest",
        )} | {"semantic_outcomes": ["evidence_only"]}], maintenance_run_id=run_id,
    )
    assert candidate["source_id"] == event_id
    work_sessions.unknown_work_session(principal, session["work_session_id"])
    task_manager.unknown_task(principal, lease)

    consolidation_worker._reconcile_unknown(principal)
    assert task_manager.get_task(principal, task["task_id"])["status"] == "succeeded"
    assert work_sessions.get_work_session(principal, session["work_session_id"])["status"] == "succeeded"


def test_same_scope_tick_is_single_flight_and_scopes_are_isolated(sandbox, monkeypatch):
    from core.memory import consolidation_worker, dossiers

    first = _principal(uid="single-flight", char_id=TEST_CHAR_ID)
    second = _principal(uid="single-flight", char_id=TEST_PEER_CHAR_ID)
    _event(first.uid, first.char_id, suffix="first")
    _event(second.uid, second.char_id, suffix="second")
    _task(first, key="first")
    assert {item["source_id"] for item in dossiers.maintenance_candidates(_scope(first.uid, first.char_id))} == {
        "consolidation-event-first"
    }
    assert {item["source_id"] for item in dossiers.maintenance_candidates(_scope(second.uid, second.char_id))} == {
        "consolidation-event-second"
    }

    entered = asyncio.Event()
    release = asyncio.Event()

    async def held_run(principal, lease, cfg):
        entered.set()
        await release.wait()
        return {"status": "succeeded", "task_id": lease.task_id}

    monkeypatch.setattr("core.memory.consolidation_worker.config", lambda: _cfg())
    monkeypatch.setattr("core.memory.consolidation_worker._night_window", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("core.memory.consolidation_worker._backoff_active", lambda: False)
    monkeypatch.setattr("core.memory.consolidation_worker._global_budget_allows", lambda _cfg: True)
    monkeypatch.setattr("core.memory.consolidation_worker._foreground_active", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("core.memory.consolidation_worker._discover_scopes", lambda: [first])
    monkeypatch.setattr("core.memory.consolidation_worker._run_claimed", held_run)

    async def scenario():
        consolidation_worker._GLOBAL_SEMAPHORE = asyncio.Semaphore(1)
        running = asyncio.create_task(consolidation_worker.tick())
        await entered.wait()
        concurrent = await consolidation_worker.tick()
        release.set()
        completed = await running
        return concurrent, completed

    concurrent, completed = asyncio.run(scenario())
    assert concurrent["status"] == "busy"
    assert completed["status"] == "succeeded"


def test_v1_store_is_upgraded_and_trigger_is_maintenance_only(sandbox):
    import sqlite3

    from core.memory import dossiers
    from core.memory.path_resolver import resolve_path
    from core.scheduler.gating import trigger_migration_status

    scope = _scope(uid="upgrade-owner")
    assert dossiers.initialize(scope).healthy
    path = resolve_path(scope, "memory_dossiers")
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE maintenance_runs")
        connection.execute("DROP TABLE maintenance_state")
        connection.execute("PRAGMA user_version=1")
        connection.commit()

    assert dossiers.maintenance_checkpoint(scope) == 0
    assert dossiers.schema_status(scope).schema_version == dossiers.SCHEMA_VERSION
    assert trigger_migration_status("memory_consolidation") == "maintenance-only"


def test_unknown_without_receipt_blocks_automatic_replay(sandbox, monkeypatch):
    from core.agent_runtime import task_manager
    from core.memory import consolidation_worker

    principal = _principal(uid="unknown-owner")
    _event(principal.uid, suffix="unknown")
    task = _task(principal, key="unknown")
    lease = task_manager.claim_next(
        principal, task_id=task["task_id"], capabilities={"memory.consolidation"},
    )
    task_manager.unknown_task(principal, lease)

    monkeypatch.setattr("core.memory.consolidation_worker.config", lambda: _cfg())
    monkeypatch.setattr("core.memory.consolidation_worker._night_window", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("core.memory.consolidation_worker._backoff_active", lambda: False)
    monkeypatch.setattr("core.memory.consolidation_worker._global_budget_allows", lambda _cfg: True)
    monkeypatch.setattr("core.memory.consolidation_worker._foreground_active", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("core.memory.consolidation_worker._discover_scopes", lambda: [principal])

    async def scenario():
        consolidation_worker._GLOBAL_SEMAPHORE = asyncio.Semaphore(1)
        return await consolidation_worker.tick()

    assert asyncio.run(scenario())["status"] == "no_work"
    tasks = task_manager.list_tasks(principal, limit=100)
    assert [(item["task_id"], item["status"]) for item in tasks] == [(task["task_id"], "outcome_unknown")]
def test_reconcile_unknown_closes_durable_failed_run(sandbox, monkeypatch):
    from core.memory import consolidation_worker

    principal = _principal(uid="failed-reconcile", char_id=TEST_CHAR_ID)
    task = {"capability": "memory.consolidation", "status": "outcome_unknown", "task_id": "a" * 32}
    seen = {}

    monkeypatch.setattr("core.agent_runtime.task_manager.list_tasks", lambda *args, **kwargs: [task])
    monkeypatch.setattr("core.memory.dossiers.committed_maintenance_run", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.memory.dossiers.latest_maintenance_run", lambda *args, **kwargs: {
        "status": "failed", "work_session_id": "b" * 32, "error_code": "provider_timeout",
    })
    monkeypatch.setattr("core.agent_runtime.work_sessions.reconcile_unknown_work_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.agent_runtime.task_manager.reconcile_outcome_unknown",
                        lambda *args, **kwargs: seen.update(kwargs))
    consolidation_worker._reconcile_unknown(principal)
    assert seen["succeeded"] is False
    assert seen["error_code"] == "provider_timeout"


def test_reopen_evidence_only_requeues_event_checkpoint(sandbox):
    from core.memory import dossiers
    from core.memory.event_store import append_event

    scope = _scope(uid="reopen-evidence")
    event_id = _event(scope.uid, scope.character_id, suffix="reopen")
    candidate = dossiers.maintenance_candidates(scope)[0]
    dossiers.apply_operations(
        scope, [], operation_id="c" * 32, actor="character:test", chain="admin_recovery",
        processing_items=[{key: candidate[key] for key in (
            "store_kind", "source_id", "source_revision", "ingest_sequence", "input_digest",
        )} | {"semantic_outcomes": ["evidence_only"]}],
    )
    assert dossiers.maintenance_candidates(scope) == []
    assert dossiers.reopen_evidence_only(scope, source_ids=[event_id]) == 1
    assert dossiers.maintenance_candidates(scope)[0]["source_id"] == event_id
