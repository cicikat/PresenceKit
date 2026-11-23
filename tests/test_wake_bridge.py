"""Focused P0.5 contracts for the durable external Wake Bridge inbox."""

from __future__ import annotations

import asyncio
import inspect
import json
import time

import pytest


def _run(coro):
    return asyncio.run(coro)


def _event(**overrides):
    raw = {
        "provider": "example_forum",
        "external_id": "message-123",
        "uid": "owner-1",
        "char_id": "char-a",
        "occurred_at": 1_700_000_000,
        "title": "A title",
        "content": "A short external post",
        "url": "https://forum.example/messages/123",
        "author": "poster",
    }
    raw.update(overrides)
    return raw


@pytest.fixture
def bridge_env(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"owner_id": "owner-1"}})
    monkeypatch.setattr("core.character_context.selected_character_id_or_none", lambda: "char-a")


def _state(sandbox, *, uid="owner-1", char_id="char-a", provider="example_forum"):
    path = sandbox.wake_bridge_state(uid, char_id=char_id, provider=provider)
    return path, json.loads(path.read_text(encoding="utf-8"))


def _only_record(state):
    assert len(state["events"]) == 1
    return next(iter(state["events"].values()))


def _allow_execute(monkeypatch, *, perceive_status="accepted", sent=True, delay=0.0):
    calls = []

    async def fake_execute_prompt(**kwargs):
        calls.append(kwargs)
        if delay:
            await asyncio.sleep(delay)
        outcome = kwargs["pipeline_outcome"]
        outcome["perceive_status"] = perceive_status
        outcome["sent"] = sent
        from core.scheduler.execution import ExecuteResult
        return ExecuteResult(trigger_name=kwargs["trigger_name"], would_send_prompt="", dry_run=False, sent=sent)

    async def fake_decide(uid, proposals, *, dry_run):
        picked = proposals[0]
        result = await picked.execute(dry_run=dry_run)
        return picked, "picked_highest_urgency", result

    monkeypatch.setattr("core.scheduler.execution.execute_prompt", fake_execute_prompt)
    monkeypatch.setattr("core.scheduler.gating.decide_and_execute_event", fake_decide)
    return calls


def test_owner_active_stays_pending_then_gate_open_executes_once(bridge_env, sandbox, monkeypatch):
    from core.wake_bridge import PENDING, WakeBridge

    calls = []

    async def owner_active(uid, proposals, *, dry_run):
        return None, "active_window_filtered", None

    monkeypatch.setattr("core.scheduler.gating.decide_and_execute_event", owner_active)
    first = _run(WakeBridge().submit_mapping(_event()))
    assert first.status == "gated"
    path, state = _state(sandbox)
    assert _only_record(state)["status"] == PENDING
    assert calls == []

    calls = _allow_execute(monkeypatch)
    record = _only_record(state)
    record["next_attempt_at"] = 0
    path.write_text(json.dumps(state), encoding="utf-8")
    result = _run(WakeBridge().drain_due())
    assert result[0].status == "accepted"
    assert len(calls) == 1
    assert _only_record(_state(sandbox)[1])["status"] == "consumed"
    assert _run(WakeBridge().drain_due()) == []


def test_duplicate_receipt_never_resets_ttl_attempts_or_reexecutes(bridge_env, sandbox, monkeypatch):
    from core.wake_bridge import WakeBridge

    calls = _allow_execute(monkeypatch)
    bridge = WakeBridge()
    assert _run(bridge.submit_mapping(_event())).status == "accepted"
    path, state = _state(sandbox)
    original = dict(_only_record(state))
    duplicate = _run(bridge.submit_mapping(_event(event_id="random-new-id")))
    assert duplicate.status == "duplicate"
    assert len(calls) == 1
    persisted = _only_record(json.loads(path.read_text(encoding="utf-8")))
    assert persisted["attempts"] == original["attempts"]
    assert persisted["expires_at"] == original["expires_at"]


def test_source_cursor_commits_only_after_durable_receipt(bridge_env, sandbox, monkeypatch):
    from core.wake_bridge import FakeForumSource, WakeBridge

    _allow_execute(monkeypatch)
    source = FakeForumSource("example_forum", [([_event()], "cursor-2")])
    result = _run(WakeBridge().poll_source(source, uid="owner-1", char_id="char-a"))
    assert result[0].status == "accepted"
    assert source.seen_cursors == [None]
    assert _state(sandbox)[1]["last_cursor"] == "cursor-2"


def test_cursor_does_not_advance_when_durable_write_fails(bridge_env, sandbox, monkeypatch):
    from core.wake_bridge import FakeForumSource, WakeBridge

    monkeypatch.setattr("core.wake_bridge.safe_write_json", lambda *args, **kwargs: False)
    source = FakeForumSource("example_forum", [([_event()], "cursor-2")])
    result = _run(WakeBridge().poll_source(source, uid="owner-1", char_id="char-a"))
    assert result[0].status == "source_error"
    assert not sandbox.wake_bridge_state("owner-1", char_id="char-a", provider="example_forum").exists()


def test_cursor_commit_failure_keeps_already_received_event_pending(bridge_env, sandbox, monkeypatch):
    from core.wake_bridge import FakeForumSource, WakeBridge

    writes = 0
    from core.safe_write import safe_write_json as actual_safe_write

    def fail_second_write(*args, **kwargs):
        nonlocal writes
        writes += 1
        return actual_safe_write(*args, **kwargs) if writes == 1 else False

    monkeypatch.setattr("core.wake_bridge.safe_write_json", fail_second_write)
    source = FakeForumSource("example_forum", [([_event()], "cursor-2")])
    results = _run(WakeBridge().poll_source(source, uid="owner-1", char_id="char-a"))
    assert results[-1].status == "source_error"
    state = _state(sandbox)[1]
    assert state["last_cursor"] == ""
    assert _only_record(state)["status"] == "pending"


@pytest.mark.parametrize("gate_reason", [
    "active_window_filtered",
    "dnd_filtered",
    "global_gap_filtered",
        "daily_budget_filtered",
        "state_filtered",
])
def test_temporary_gate_rejections_are_pending_not_dropped(bridge_env, sandbox, monkeypatch, gate_reason):
    from core.wake_bridge import PENDING, WakeBridge

    async def reject(uid, proposals, *, dry_run):
        return None, gate_reason, None

    monkeypatch.setattr("core.scheduler.gating.decide_and_execute_event", reject)
    result = _run(WakeBridge().submit_mapping(_event()))
    record = _only_record(_state(sandbox)[1])
    assert result.status == "gated" and result.reason == gate_reason
    assert record["status"] == PENDING
    assert record["next_attempt_at"] > record["last_attempt_at"]


@pytest.mark.parametrize("gate_reason", ["proactive_off", "suppressed_unanswered_cap", "talk_canceled", "expired_before_talk"])
def test_terminal_talk_dispositions_are_not_retried(bridge_env, sandbox, monkeypatch, gate_reason):
    """Long-lived speech permission closure must not resurrect a stale external event."""
    from core.wake_bridge import CONSUMED, WakeBridge

    async def reject(uid, proposals, *, dry_run):
        return None, gate_reason, None

    monkeypatch.setattr("core.scheduler.gating.decide_and_execute_event", reject)
    result = _run(WakeBridge().submit_mapping(_event()))
    record = _only_record(_state(sandbox)[1])
    assert result.reason == gate_reason
    assert record["status"] == CONSUMED
    assert record["last_disposition"] == gate_reason


def test_pending_survives_restart_and_processing_lease_recovers(bridge_env, sandbox, monkeypatch):
    from core.wake_bridge import PROCESSING, WakeBridge

    bridge = WakeBridge()
    assert _run(bridge.submit_mapping(_event(), attempt_immediately=False)).status == "accepted"
    path, state = _state(sandbox)
    record = _only_record(state)
    record.update({"status": PROCESSING, "attempts": 1, "lease_until": time.time() - 1, "claim_token": "crashed"})
    path.write_text(json.dumps(state), encoding="utf-8")

    calls = _allow_execute(monkeypatch)
    result = _run(WakeBridge().drain_due())
    assert result[0].status == "accepted"
    persisted = _only_record(_state(sandbox)[1])
    assert persisted["status"] == "consumed"
    assert persisted["attempts"] == 2
    assert len(calls) == 1


def test_concurrent_drain_claims_an_event_once(bridge_env, monkeypatch):
    from core.wake_bridge import WakeBridge

    bridge = WakeBridge()
    assert _run(bridge.submit_mapping(_event(), attempt_immediately=False)).status == "accepted"
    calls = _allow_execute(monkeypatch, delay=0.03)

    async def drain_twice():
        return await asyncio.gather(bridge.drain_due(max_items=1), bridge.drain_due(max_items=1))

    _run(drain_twice())
    assert len(calls) == 1


def test_dream_guard_and_temporary_error_return_to_pending_with_backoff(bridge_env, sandbox, monkeypatch):
    from core.wake_bridge import PENDING, WakeBridge

    _allow_execute(monkeypatch, perceive_status="blocked_dream", sent=False)
    assert _run(WakeBridge().submit_mapping(_event())).status == "blocked_dream"
    record = _only_record(_state(sandbox)[1])
    assert record["status"] == PENDING
    assert record["next_attempt_at"] > record["last_attempt_at"]

    # A separate receipt demonstrates that an execution exception is also retryable.
    async def broken(uid, proposals, *, dry_run):
        raise RuntimeError("temporary scheduler failure")

    monkeypatch.setattr("core.scheduler.gating.decide_and_execute_event", broken)
    assert _run(WakeBridge().submit_mapping(_event(external_id="message-2"))).status == "source_error"
    path, state = _state(sandbox)
    record = state["events"][_run_key("example_forum", "message-2")]
    assert record["status"] == PENDING
    assert record["attempts"] == 1
    assert record["next_attempt_at"] > record["last_attempt_at"]


def _run_key(provider, external_id):
    from core.wake_bridge import ExternalStimulus
    return ExternalStimulus.from_mapping(_event(provider=provider, external_id=external_id)).stable_event_key


def test_ttl_expires_without_llm_and_invalid_durable_record_is_rejected(bridge_env, sandbox, monkeypatch):
    from core.wake_bridge import WakeBridge

    bridge = WakeBridge()
    assert _run(bridge.submit_mapping(_event(), attempt_immediately=False)).status == "accepted"
    path, state = _state(sandbox)
    _only_record(state)["expires_at"] = time.time() - 1
    path.write_text(json.dumps(state), encoding="utf-8")

    async def forbidden(*args, **kwargs):
        raise AssertionError("expired event must not execute")

    monkeypatch.setattr("core.scheduler.gating.decide_and_execute_event", forbidden)
    assert _run(bridge.drain_due()) == []
    assert _only_record(_state(sandbox)[1])["status"] == "expired"

    assert _run(bridge.submit_mapping(_event(external_id="bad-record"), attempt_immediately=False)).status == "accepted"
    path, state = _state(sandbox)
    state["events"][_run_key("example_forum", "bad-record")]["stable_event_key"] = "not-the-real-key"
    path.write_text(json.dumps(state), encoding="utf-8")
    result = _run(bridge.drain_due())
    assert result[0].status == "rejected"
    assert state["events"][_run_key("example_forum", "bad-record")]["status"] == "pending"
    assert _state(sandbox)[1]["events"][_run_key("example_forum", "bad-record")]["status"] == "rejected"


def test_provider_uid_and_character_scopes_are_isolated(bridge_env, sandbox, monkeypatch):
    from core.wake_bridge import WakeBridge

    calls = _allow_execute(monkeypatch)
    bridge = WakeBridge()
    assert _run(bridge.submit_mapping(_event())).status == "accepted"
    assert _run(bridge.submit_mapping(_event(provider="other_forum"))).status == "accepted"
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"owner_id": "owner-2"}})
    monkeypatch.setattr("core.character_context.selected_character_id_or_none", lambda: "char-b")
    assert _run(bridge.submit_mapping(_event(uid="owner-2", char_id="char-b"))).status == "accepted"
    assert len(calls) == 3
    assert sandbox.wake_bridge_state("owner-1", char_id="char-a", provider="example_forum").exists()
    assert sandbox.wake_bridge_state("owner-1", char_id="char-a", provider="other_forum").exists()
    assert sandbox.wake_bridge_state("owner-2", char_id="char-b", provider="example_forum").exists()


def test_tick_limit_and_untrusted_history_boundary(bridge_env, sandbox, monkeypatch):
    from core.wake_bridge import MAX_CONTENT_CHARS, WakeBridge, query_state

    bridge = WakeBridge()
    long_secret = "do-not-persist-full-source:" + "x" * (MAX_CONTENT_CHARS + 50)
    assert _run(bridge.submit_mapping(_event(content=long_secret), attempt_immediately=False)).status == "accepted"
    assert _run(bridge.submit_mapping(_event(provider="other_forum", external_id="message-2"), attempt_immediately=False)).status == "accepted"
    calls = _allow_execute(monkeypatch)
    result = _run(bridge.drain_due(max_items=1))
    assert len(result) == 1
    assert len(calls) == 1
    assert calls[0]["write_trigger_stub"] is False
    assert calls[0]["recall_policy"] == "none"
    assert "不可信的外部论坛事件摘要" in calls[0]["prompt_factory"]()
    persisted = _state(sandbox)[0].read_text(encoding="utf-8")
    assert long_secret not in persisted
    assert "content_excerpt" in persisted
    observed = query_state(uid="owner-1", char_id="char-a", provider="example_forum")
    assert long_secret not in json.dumps(observed, ensure_ascii=False)
    assert "content_excerpt" not in json.dumps(observed, ensure_ascii=False)
    assert observed[0]["pending_count"] + observed[0]["consumed_count"] == 1


@pytest.mark.parametrize("bad", [
    {},
    _event(provider="../../bad"),
    _event(external_id=""),
    _event(occurred_at="not-a-time"),
    _event(metadata={"nested": {"not": "scalar"}}),
])
def test_malformed_ingress_fails_closed(bridge_env, bad):
    from core.wake_bridge import WakeBridge

    assert _run(WakeBridge().submit_mapping(bad)).status == "malformed"


def test_http_ingress_requires_integration_scope():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin.routers.wake_bridge import router

    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).post("/integrations/forum/events", json=_event()).status_code == 401


def test_bridge_has_no_direct_llm_or_turn_sink_call():
    import core.wake_bridge as bridge

    source = inspect.getsource(bridge.WakeBridge)
    assert "run_llm(" not in source
    assert "record_assistant_turn(" not in source
