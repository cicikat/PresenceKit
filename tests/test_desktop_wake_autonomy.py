from __future__ import annotations

from tests.fixtures.session_grants import issue
import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import pytest


def _allow_dream_guard(monkeypatch) -> None:
    from core.dream import dream_state

    monkeypatch.setattr(
        dream_state,
        "get_reality_guard_status",
        lambda _uid: dream_state.DreamGuardStatus.ALLOW,
    )


def _enable_autonomy(uid: str = "owner", char_id: str = "char-a") -> None:
    from core.autonomy import store

    state = store.load(uid, char_id)
    state["config"]["enabled"] = True
    assert store.save(uid, char_id, state)


def _isolate_optional_signal_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.scheduler.triggers.watch.get_last_heart_rate_event", lambda: None
    )
    monkeypatch.setattr(
        "core.scheduler.last_mentioned.recall_last_mentioned",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "core.memory.episodic_memory._load_memories", lambda *_args, **_kwargs: []
    )


def _wake_job(*, now: float | None = None):
    from core.autonomy.models import Job, Opportunity
    from core.autonomy.signal_adapters import adapt_desktop_wake

    now = time.time() if now is None else now
    signal = adapt_desktop_wake(
        last_seen=now - 300,
        now=now,
        event_id="event-wake",
        dedupe_key="private-dedupe-material",
    )
    opportunity = Opportunity.merge([signal], now=now)
    return Job(
        uid="owner",
        char_id="char-a",
        source="autonomy",
        opportunity=opportunity.to_dict(),
        signal_sources=["desktop_wake"],
    )


def test_desktop_wake_adapter_bounds_duration_and_redacts_raw_last_seen():
    from core.autonomy.signal_adapters import adapt_desktop_wake

    now = 5_000_000.0
    raw_dedupe = "desktop_wake:owner:char-a:desktop:wake:hash:bucket"
    signal = adapt_desktop_wake(
        last_seen=now - 45 * 24 * 60 * 60,
        now=now,
        event_id="event-123",
        dedupe_key=raw_dedupe,
    )
    payload = signal.to_dict()
    evidence = payload["evidence"][0]

    assert payload["source"] == "desktop_wake"
    assert payload["reason"] == "session_reopen"
    assert payload["action_mode"] == "reflect"
    assert payload["expires_at"] == now + 10 * 60
    assert evidence["fact"] == "desktop_session_reopened"
    assert evidence["offline_seconds"] == 30 * 24 * 60 * 60
    assert evidence["offline_duration_capped"] is True
    assert evidence["perceive_event_id"] == "event-123"
    assert evidence["perceive_dedupe_fingerprint"] == hashlib.sha256(
        raw_dedupe.encode("utf-8")
    ).hexdigest()[:24]
    assert "last_seen" not in json.dumps(payload)
    assert raw_dedupe not in json.dumps(payload)


@pytest.mark.asyncio
async def test_desktop_wake_path_b_only_queues_signal(sandbox, monkeypatch):
    from core.autonomy import store
    from core.perceive_event import clear_dedup_registry_for_test

    clear_dedup_registry_for_test()
    _allow_dream_guard(monkeypatch)
    _enable_autonomy()
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner"}},
    )
    monkeypatch.setattr(
        "core.perceive_event._resolve_char_id", lambda _uid, _char_id: "char-a"
    )

    def forbidden_sync(*_args, **_kwargs):
        raise AssertionError(
            "desktop wake HTTP must not obtain Pipeline, lock, or write send ledger"
        )

    monkeypatch.setattr("core.pipeline_registry.get", forbidden_sync)
    monkeypatch.setattr(
        "core.conversation_gate.conversation_lock", forbidden_sync
    )
    monkeypatch.setattr(
        "core.scheduler.proactive_ledger.record_send", forbidden_sync
    )

    async def forbidden_async(*_args, **_kwargs):
        raise AssertionError("desktop wake HTTP must not send or record a turn")

    monkeypatch.setattr("core.llm_client.chat_turn", forbidden_async)
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", forbidden_async)
    monkeypatch.setattr("core.autonomy.talk_gate.send", forbidden_async)
    monkeypatch.setattr("channels.registry.broadcast", forbidden_async)
    monkeypatch.setattr("channels.desktop_ws.push_message", forbidden_async)

    from admin.routers.chat import desktop_wake

    result = await desktop_wake({}, x_presence_session=issue(monkeypatch, owner="owner", char="char-a").session_id)

    assert result["reply"] is None
    assert result["source"] == "queued_autonomy_signal"
    assert "turn_id" not in result and "msg_id" not in result
    pending = store.load("owner", "char-a")["pending_signals"]
    assert len(pending) == 1
    signal = pending[0]["signal"]
    assert signal["source"] == "desktop_wake"
    assert signal["signal_id"] == result["correlation_id"]
    assert signal["expires_at"] == result["expires_at"]


@pytest.mark.asyncio
async def test_desktop_wake_dedup_ignores_last_seen_and_audits_both_results(
    sandbox, monkeypatch
):
    from core.autonomy import store
    from core.perceive_event_audit import query

    _allow_dream_guard(monkeypatch)
    _enable_autonomy()
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner"}},
    )
    monkeypatch.setattr(
        "core.perceive_event._resolve_char_id", lambda _uid, _char_id: "char-a"
    )
    monkeypatch.setattr("core.memory.short_term.load", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("channels.desktop_ws.get_connect_time", lambda: 0.0)

    class ActiveAssets:
        def read_text(self, encoding="utf-8"):
            return json.dumps({"active_character": "char-a"})

    monkeypatch.setattr(
        "core.sandbox.DataPaths.active_prompt_assets", lambda _self: ActiveAssets()
    )

    from admin.routers.chat import desktop_wake

    first = await desktop_wake({"last_seen": time.time() - 600}, x_presence_session=issue(monkeypatch, owner="owner", char="char-a").session_id)
    second = await desktop_wake({"last_seen": time.time() - 5}, x_presence_session=issue(monkeypatch, owner="owner", char="char-a").session_id)

    assert first["source"] == "queued_autonomy_signal"
    assert second == {"reply": None, "source": "duplicate_wake"}
    assert len(store.load("owner", "char-a")["pending_signals"]) == 1
    records, total = query(source="desktop_wake", limit=10)
    assert total == 2
    assert {record["gate_result"] for record in records} == {
        "accepted",
        "duplicate",
    }
    assert len({record["dedupe_key"] for record in records}) == 1
    assert all(record["did_generate_reply"] is False for record in records)


@pytest.mark.asyncio
async def test_desktop_wake_autonomy_disabled_leaves_no_replayable_signal(
    sandbox, monkeypatch
):
    from core.autonomy import store

    _allow_dream_guard(monkeypatch)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner"}},
    )
    monkeypatch.setattr(
        "core.perceive_event._resolve_char_id", lambda _uid, _char_id: "char-a"
    )

    from admin.routers.chat import desktop_wake

    result = await desktop_wake({}, x_presence_session=issue(monkeypatch, owner="owner", char="char-a").session_id)

    assert result == {"reply": None, "source": "autonomy_disabled"}
    state = store.load("owner", "char-a")
    assert state["pending_signals"] == []
    assert state["jobs"] == []


@pytest.mark.asyncio
async def test_expired_desktop_wake_is_terminal_and_observable(sandbox, monkeypatch):
    from admin.routers import autonomy as autonomy_router
    from core.autonomy import runner, store
    from core.autonomy.signal_adapters import adapt_desktop_wake

    _enable_autonomy()
    signal = adapt_desktop_wake(
        now=1_000.0,
        last_seen=900.0,
        ttl_seconds=60,
        event_id="expired-event",
        dedupe_key="expired-key",
    )
    assert store.enqueue_signal(
        "owner", "char-a", signal, dedupe_key="expired-wake"
    )[0]
    monkeypatch.setattr(runner.time, "time", lambda: 1_061.0)
    monkeypatch.setattr(
        "core.self_management.policy.autonomy_enabled", lambda *_args: True
    )
    _isolate_optional_signal_sources(monkeypatch)

    async def forbidden_job(_job):
        raise AssertionError("expired wake must not start an autonomy model run")

    monkeypatch.setattr(runner, "run_job", forbidden_job)

    await runner.tick("owner", "char-a")

    state = store.load("owner", "char-a")
    assert state["pending_signals"] == []
    assert state["jobs"][0]["status"] == "done"
    assert state["jobs"][0]["signal_sources"] == ["desktop_wake"]
    assert state["runs"][0]["disposition"] == "expired"
    assert state["runs"][0]["evaluation_status"] == "expired"

    monkeypatch.setattr(
        autonomy_router, "_scope", lambda: ("owner", "char-a")
    )
    observed = await autonomy_router.opportunities(limit=20, auth=object())
    opportunity = next(
        item for item in observed["entries"] if item["kind"] == "opportunity"
    )
    wake_evidence = opportunity["signals"][0]["evidence"][0]
    assert wake_evidence["perceive_event_id"] == "expired-event"
    assert wake_evidence["perceive_dedupe_fingerprint"] == hashlib.sha256(
        b"expired-key"
    ).hexdigest()[:24]
    terminal_run = next(
        item for item in observed["entries"] if item["kind"] == "run"
    )
    assert terminal_run["status"] == "expired"
    assert terminal_run["disposition"] == "expired"


@pytest.mark.asyncio
async def test_disabling_autonomy_after_enqueue_consumes_wake_once(
    sandbox, monkeypatch
):
    from core.autonomy import runner, store
    from core.autonomy.signal_adapters import enqueue_desktop_wake_signal

    _enable_autonomy()
    queued, status, _signal = enqueue_desktop_wake_signal(
        "owner",
        "char-a",
        event_id="disable-event",
        dedupe_key="disable-key",
    )
    assert queued and status == "queued"
    state = store.load("owner", "char-a")
    state["config"]["enabled"] = False
    assert store.save("owner", "char-a", state)

    await runner.tick("owner", "char-a")

    state = store.load("owner", "char-a")
    assert state["pending_signals"] == []
    assert state["runs"][0]["disposition"] == "suppressed_proactive_off"


@pytest.mark.asyncio
async def test_wake_merges_with_other_low_signal_into_one_opportunity(
    sandbox, monkeypatch
):
    from core.autonomy import runner, store
    from core.autonomy.models import Run
    from core.autonomy.signal_adapters import (
        emit_trigger_signal,
        enqueue_desktop_wake_signal,
    )

    _enable_autonomy()
    monkeypatch.setattr(
        "core.self_management.policy.autonomy_enabled", lambda *_args: True
    )
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"enabled": True, "random_message": True}},
    )
    _isolate_optional_signal_sources(monkeypatch)
    assert enqueue_desktop_wake_signal(
        "owner",
        "char-a",
        event_id="merge-event",
        dedupe_key="merge-key",
    )[0]
    assert emit_trigger_signal("owner", "char-a", "random_message")[0]
    claimed = []

    async def silent_job(job):
        claimed.append(job)
        return Run(
            uid=job.uid,
            char_id=job.char_id,
            source=job.source,
            job_id=job.id,
            disposition="completed_no_op",
            finished_at=time.time(),
            opportunity_id=job.opportunity["id"],
            signal_count=len(job.opportunity["signals"]),
        )

    monkeypatch.setattr(runner, "run_job", silent_job)

    await runner.tick("owner", "char-a")

    assert len(claimed) == 1
    assert set(claimed[0].signal_sources) == {"desktop_wake", "scheduler"}
    assert len(claimed[0].opportunity["signals"]) == 2
    state = store.load("owner", "char-a")
    assert len(state["jobs"]) == 1
    assert state["runs"][0]["disposition"] == "completed_no_op"


@pytest.mark.asyncio
async def test_wake_dream_block_after_enqueue_is_not_retried(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Disposition, Run
    from core.autonomy.signal_adapters import adapt_desktop_wake

    _enable_autonomy()
    signal = adapt_desktop_wake(event_id="dream-race", dedupe_key="dream-race-key")
    job, status = store.enqueue_opportunity(
        "owner", "char-a", [signal], dedupe_key="dream-race-job"
    )
    assert status == "queued" and job is not None
    monkeypatch.setattr(
        "core.self_management.policy.autonomy_enabled", lambda *_args: True
    )
    _isolate_optional_signal_sources(monkeypatch)

    async def blocked(claimed):
        return Run(
            uid=claimed.uid,
            char_id=claimed.char_id,
            source=claimed.source,
            job_id=claimed.id,
            disposition=Disposition.BLOCKED_DREAM.value,
            finished_at=time.time(),
            opportunity_id=claimed.opportunity["id"],
            signal_count=1,
        )

    monkeypatch.setattr(runner, "run_job", blocked)

    await runner.tick("owner", "char-a")

    persisted = store.load("owner", "char-a")["jobs"][0]
    assert persisted["status"] == "done"
    assert persisted["next_attempt_at"] == 0


@pytest.mark.asyncio
async def test_wake_runner_can_finish_silent_without_talk(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Run

    state = store.load("owner", "char-a")
    state["config"]["enabled"] = True
    job = _wake_job()
    monkeypatch.setattr(runner, "_runtime_tools", lambda *_args: ([], None))
    monkeypatch.setattr(runner, "_context_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_character_for", lambda _char_id: None)
    monkeypatch.setattr(runner, "_user_became_active", lambda _uid: False)
    monkeypatch.setattr(runner.talk_gate, "check", lambda *_args, **_kwargs: ("allow", "ok"))

    async def silent(*_args, **_kwargs):
        return SimpleNamespace(tool_calls=[], continuation_items=[], assistant_message={})

    async def forbidden_send(*_args, **_kwargs):
        raise AssertionError("silent run must not call talk_owner")

    monkeypatch.setattr("core.llm_client.chat_turn", silent)
    monkeypatch.setattr(runner.talk_gate, "send", forbidden_send)
    run = await runner._run_locked(
        job,
        state,
        Run(uid="owner", char_id="char-a", source="autonomy", job_id=job.id),
    )

    assert run.disposition == "completed_no_op"
    assert run.talk_sent is False


@pytest.mark.asyncio
async def test_wake_runner_tools_only_does_not_talk(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Run

    state = store.load("owner", "char-a")
    state["config"].update({"enabled": True, "max_tools": 1})
    job = _wake_job()
    schema = {
        "type": "function",
        "function": {"name": "safe_read", "parameters": {}},
    }
    monkeypatch.setattr(runner, "_runtime_tools", lambda *_args: ([schema], None))
    monkeypatch.setattr(runner, "_context_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_character_for", lambda _char_id: None)
    monkeypatch.setattr(runner, "_user_became_active", lambda _uid: False)
    monkeypatch.setattr(
        runner.talk_gate,
        "check",
        lambda *_args, **_kwargs: ("hard", "suppressed_dnd"),
    )
    turns = iter(
        [
            SimpleNamespace(
                tool_calls=[{"id": "read", "name": "safe_read", "arguments": {}}],
                continuation_items=[],
                assistant_message={},
            ),
            SimpleNamespace(tool_calls=[], continuation_items=[], assistant_message={}),
        ]
    )

    async def chat_turn(*_args, **_kwargs):
        return next(turns)

    async def execute(*_args, **_kwargs):
        return "current fact", "ok"

    async def forbidden_send(*_args, **_kwargs):
        raise AssertionError("tools-only run must not call talk_owner")

    monkeypatch.setattr("core.llm_client.chat_turn", chat_turn)
    monkeypatch.setattr(runner, "_execute_tool", execute)
    monkeypatch.setattr(runner.talk_gate, "send", forbidden_send)
    monkeypatch.setattr(runner, "_is_write_tool", lambda _name: False)
    run = await runner._run_locked(
        job,
        state,
        Run(uid="owner", char_id="char-a", source="autonomy", job_id=job.id),
    )

    assert run.disposition == "completed_tools_only"
    assert run.tool_names == ["safe_read"]
    assert run.events[0] == {"status": "talk_unavailable", "reason": "suppressed_dnd"}


@pytest.mark.asyncio
async def test_wake_runner_talks_at_most_once_through_talk_owner(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Run

    state = store.load("owner", "char-a")
    state["config"]["enabled"] = True
    job = _wake_job()
    monkeypatch.setattr(runner, "_runtime_tools", lambda *_args: ([], None))
    monkeypatch.setattr(runner, "_context_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_character_for", lambda _char_id: None)
    monkeypatch.setattr(runner, "_user_became_active", lambda _uid: False)
    monkeypatch.setattr(runner.talk_gate, "check", lambda *_args, **_kwargs: ("allow", "ok"))

    async def talk(*_args, **_kwargs):
        return SimpleNamespace(
            tool_calls=[{
                "id": "talk",
                "name": "talk_owner",
                "arguments": {"text": "I noticed the session reopened.", "reason": "reopen fact"},
            }],
            continuation_items=[],
            assistant_message={},
        )

    calls = []

    async def send(*args, **kwargs):
        calls.append((args, kwargs))
        return True, "sent"

    monkeypatch.setattr("core.llm_client.chat_turn", talk)
    monkeypatch.setattr(runner.talk_gate, "send", send)
    run = await runner._run_locked(
        job,
        state,
        Run(uid="owner", char_id="char-a", source="autonomy", job_id=job.id),
    )

    assert run.disposition == "completed_talk_sent"
    assert run.talk_sent is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_wake_runner_cancels_when_user_becomes_active(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Run

    state = store.load("owner", "char-a")
    state["config"]["enabled"] = True
    job = _wake_job()
    monkeypatch.setattr(runner, "_runtime_tools", lambda *_args: ([], None))
    monkeypatch.setattr(runner, "_context_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_character_for", lambda _char_id: None)
    monkeypatch.setattr(runner, "_user_became_active", lambda _uid: True)
    monkeypatch.setattr(runner.talk_gate, "check", lambda *_args, **_kwargs: ("allow", "ok"))

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("active user must cancel before model or talk_owner")

    monkeypatch.setattr("core.llm_client.chat_turn", forbidden)
    monkeypatch.setattr(runner.talk_gate, "send", forbidden)
    run = await runner._run_locked(
        job,
        state,
        Run(uid="owner", char_id="char-a", source="autonomy", job_id=job.id),
    )

    assert run.disposition == "canceled_by_user_activity"
    assert run.talk_sent is False


@pytest.mark.parametrize(
    "reason",
    ["suppressed_dnd", "suppressed_unanswered_cap", "suppressed_daily_budget"],
)
@pytest.mark.asyncio
async def test_wake_runner_hard_talk_limits_are_observable_without_send(
    sandbox, monkeypatch, reason
):
    from core.autonomy import runner, store
    from core.autonomy.models import Run

    state = store.load("owner", "char-a")
    state["config"]["enabled"] = True
    job = _wake_job()
    monkeypatch.setattr(runner, "_runtime_tools", lambda *_args: ([], None))
    monkeypatch.setattr(runner, "_context_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_character_for", lambda _char_id: None)
    monkeypatch.setattr(runner, "_user_became_active", lambda _uid: False)
    monkeypatch.setattr(
        runner.talk_gate, "check", lambda *_args, **_kwargs: ("hard", reason)
    )

    async def silent(*_args, **_kwargs):
        return SimpleNamespace(tool_calls=[], continuation_items=[], assistant_message={})

    async def forbidden_send(*_args, **_kwargs):
        raise AssertionError("hard talk limit must not call talk_owner")

    monkeypatch.setattr("core.llm_client.chat_turn", silent)
    monkeypatch.setattr(runner.talk_gate, "send", forbidden_send)
    run = await runner._run_locked(
        job,
        state,
        Run(uid="owner", char_id="char-a", source="autonomy", job_id=job.id),
    )

    assert run.disposition == "completed_no_op"
    assert {"status": "talk_unavailable", "reason": reason} in run.events
    assert run.talk_sent is False


@pytest.mark.asyncio
async def test_wake_runner_talk_disabled_reason_is_observable(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Run

    state = store.load("owner", "char-a")
    state["config"].update({"enabled": True, "talk_enabled": False})
    job = _wake_job()
    monkeypatch.setattr(runner, "_runtime_tools", lambda *_args: ([], None))
    monkeypatch.setattr(runner, "_context_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_character_for", lambda _char_id: None)
    monkeypatch.setattr(runner, "_user_became_active", lambda _uid: False)
    monkeypatch.setattr(
        runner.talk_gate, "check", lambda *_args, **_kwargs: ("allow", "ok")
    )

    async def silent(*_args, **_kwargs):
        return SimpleNamespace(tool_calls=[], continuation_items=[], assistant_message={})

    async def forbidden_send(*_args, **_kwargs):
        raise AssertionError("disabled talk must not call talk_owner")

    monkeypatch.setattr("core.llm_client.chat_turn", silent)
    monkeypatch.setattr(runner.talk_gate, "send", forbidden_send)
    run = await runner._run_locked(
        job,
        state,
        Run(uid="owner", char_id="char-a", source="autonomy", job_id=job.id),
    )

    assert run.disposition == "completed_no_op"
    assert run.events == [{"status": "talk_unavailable", "reason": "talk_disabled"}]
    assert "because talk_disabled" in run.prompt_snapshot[0]["content"]
    assert run.talk_sent is False
