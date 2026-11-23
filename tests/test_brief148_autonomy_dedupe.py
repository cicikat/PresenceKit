from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace


def _memory(memory_id: str = "ep-1") -> dict:
    return {
        "id": memory_id,
        "narrative_summary": "用户完成了阶段性项目验收",
        "strength": 0.9,
        "timestamp": 1_000.0,
    }


def _memory_job(memory_id: str = "ep-1"):
    from core.autonomy.models import Job, Opportunity
    from core.autonomy.signal_adapters import adapt_memory_reactivation

    signal = adapt_memory_reactivation(
        _memory(memory_id),
        now=1_000.0,
        anchor_context={"turn_id": "new-turn", "user_text": "项目有了新进展"},
    )
    opportunity = Opportunity.merge([signal], now=1_000.0)
    return Job(
        uid="owner",
        char_id="char",
        source="autonomy",
        opportunity=opportunity.to_dict(),
    )


def test_morning_scheduler_and_routine_candidates_merge_without_forcing_talk(
    sandbox, monkeypatch
):
    from core.autonomy import store
    from core.autonomy.models import Opportunity
    from core.autonomy.signal_adapters import adapt_time_background, emit_trigger_signal

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"enabled": True, "morning_greeting": True}},
    )
    now = datetime(2026, 8, 7, 7, 30).timestamp()
    queued, status = emit_trigger_signal(
        "owner",
        "char",
        "morning_greeting",
        evidence=[{"fact": "configured_time_window", "window": "07:00-09:00"}],
        reason="A configured morning time window is eligible for autonomy evaluation.",
        priority=0.1,
        now=now,
    )
    assert queued and status == "queued"

    signals = store.drain_pending_signals("owner", "char")
    signals.append(
        adapt_time_background(
            "morning_greeting", now=now, window="07:00-09:00"
        )
    )
    opportunity = Opportunity.merge(signals, now=now)

    assert len(opportunity.signals) == 1
    assert opportunity.action_mode == "none"
    assert opportunity.suggested_action == "silent"
    assert opportunity.signals[0]["evidence"][0]["routine_key"] == "morning_greeting"


def test_disabled_routine_source_is_rejected_and_pending_fact_is_not_consumed(
    sandbox, monkeypatch
):
    from core.autonomy import runner, store
    from core.autonomy.signal_adapters import adapt_time_background, emit_trigger_signal

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"enabled": True, "morning_greeting": False}},
    )
    assert emit_trigger_signal("owner", "char", "morning_greeting") == (
        False,
        "disabled",
    )

    state = store.load("owner", "char")
    state["config"]["enabled"] = True
    assert store.save("owner", "char", state)
    assert store.enqueue_signal(
        "owner",
        "char",
        adapt_time_background(
            "morning_greeting", now=2_000.0, window="07:00-09:00"
        ),
        dedupe_key="pre-disabled",
    )[0]
    monkeypatch.setattr(runner.time, "time", lambda: 2_001.0)
    monkeypatch.setattr(
        "core.self_management.policy.autonomy_enabled", lambda *_args: True
    )
    monkeypatch.setattr(
        "core.scheduler.triggers.watch.get_last_heart_rate_event", lambda: None
    )
    monkeypatch.setattr(
        "core.scheduler.last_mentioned.recall_last_mentioned", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "core.memory.episodic_memory._load_memories", lambda *_args, **_kwargs: []
    )

    asyncio.run(runner.tick("owner", "char"))

    loaded = store.load("owner", "char")
    assert loaded["pending_signals"] == []
    assert loaded["jobs"] == []


def test_memory_candidate_cooldown_skips_evaluated_memory_but_anchor_can_reactivate(
    sandbox,
):
    from core.autonomy.signal_adapters import adapt_memory_reactivation
    from core.scheduler.last_mentioned import mark_memory_recall_evaluated
    from core.scheduler.triggers.time_based import (
        _spontaneous_recall_candidates,
        memory_key_for_recall,
    )

    memory = _memory()
    key = memory_key_for_recall(memory)
    assert adapt_memory_reactivation(memory, now=1_000.0) is not None

    mark_memory_recall_evaluated(key, now_ts=1_000.0)
    assert adapt_memory_reactivation(memory, now=1_001.0) is None
    assert _spontaneous_recall_candidates(
        [memory], now_ts=1_001.0, shadow=False
    ) == []

    anchored = adapt_memory_reactivation(
        memory,
        now=1_001.0,
        anchor_context={"turn_id": "turn-2", "user_text": "今天有了新进展"},
    )
    assert anchored is not None
    assert anchored.memory_query["memory_key"] == key
    assert any(
        item.get("fact") == "new_anchored_context" for item in anchored.evidence
    )


def test_silent_memory_evaluation_records_read_and_evaluated_not_recalled(
    sandbox, monkeypatch
):
    from core.autonomy import runner, store
    from core.autonomy.models import Run
    from core.scheduler.last_mentioned import (
        load_evaluated_memories,
        load_recalled_memories,
    )

    job = _memory_job("ep-silent")
    state = store.load("owner", "char")
    state["config"]["enabled"] = True
    monkeypatch.setattr(runner, "_runtime_tools", lambda *_args: ([], None))
    monkeypatch.setattr(runner, "_character_for", lambda _char_id: None)
    monkeypatch.setattr(runner, "_user_became_active", lambda _uid: False)
    monkeypatch.setattr(runner.talk_gate, "check", lambda *_args, **_kwargs: ("hard", "blocked"))
    monkeypatch.setattr(
        runner,
        "_context_messages",
        lambda *_args, **_kwargs: [{
            "role": "system",
            "content": "anchored memory",
            "_layer": "autonomy_memory_query",
            "_provenance": {
                "memory_keys": ["episode:ep-silent"],
                "reliable_anchor_count": 1,
            },
        }],
    )

    async def silent_turn(*_args, **_kwargs):
        return SimpleNamespace(tool_calls=[], continuation_items=[], assistant_message={})

    monkeypatch.setattr("core.llm_client.chat_turn", silent_turn)
    run = asyncio.run(
        runner._run_locked(
            job,
            state,
            Run(uid="owner", char_id="char", source="autonomy", job_id=job.id),
        )
    )

    assert run.disposition == "completed_no_op"
    assert [event["status"] for event in run.events] == [
        "talk_unavailable",
        "memory_read",
        "memory_candidate_evaluated",
    ]
    assert "episode:ep-silent" in load_evaluated_memories()
    assert "episode:ep-silent" not in load_recalled_memories()


def test_only_successful_memory_talk_marks_recalled(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Run
    from core.scheduler.last_mentioned import load_recalled_memories

    job = _memory_job("ep-sent")
    state = store.load("owner", "char")
    state["config"]["enabled"] = True
    monkeypatch.setattr(runner, "_runtime_tools", lambda *_args: ([], None))
    monkeypatch.setattr(runner, "_character_for", lambda _char_id: None)
    monkeypatch.setattr(runner, "_user_became_active", lambda _uid: False)
    monkeypatch.setattr(runner.talk_gate, "check", lambda *_args, **_kwargs: ("allow", "ok"))
    monkeypatch.setattr(
        runner,
        "_context_messages",
        lambda *_args, **_kwargs: [{
            "role": "system",
            "content": "anchored memory",
            "_layer": "autonomy_memory_query",
            "_provenance": {
                "memory_keys": ["episode:ep-sent"],
                "reliable_anchor_count": 1,
            },
        }],
    )

    async def talk_turn(*_args, **_kwargs):
        return SimpleNamespace(
            tool_calls=[{
                "id": "talk",
                "name": "talk_owner",
                "arguments": {"text": "项目的新进展听起来很扎实。", "reason": "anchored memory"},
            }],
            continuation_items=[],
            assistant_message={},
        )

    async def send(*_args, **_kwargs):
        return True, "sent"

    monkeypatch.setattr("core.llm_client.chat_turn", talk_turn)
    monkeypatch.setattr(runner.talk_gate, "send", send)
    run = asyncio.run(
        runner._run_locked(
            job,
            state,
            Run(uid="owner", char_id="char", source="autonomy", job_id=job.id),
        )
    )

    assert run.disposition == "completed_talk_sent"
    assert run.talk_sent is True
    assert "episode:ep-sent" in load_recalled_memories()
    assert run.events[-1] == {
        "status": "memory_recall_talk_sent",
        "memory_key": "episode:ep-sent",
    }


def test_failed_memory_evaluation_does_not_mark_evaluated_or_recalled(
    sandbox, monkeypatch
):
    from core.autonomy import runner, store
    from core.autonomy.models import Run
    from core.scheduler.last_mentioned import (
        load_evaluated_memories,
        load_recalled_memories,
    )

    job = _memory_job("ep-failed")
    state = store.load("owner", "char")
    state["config"]["enabled"] = True
    monkeypatch.setattr(runner, "_runtime_tools", lambda *_args: ([], None))
    monkeypatch.setattr(runner, "_character_for", lambda _char_id: None)
    monkeypatch.setattr(runner, "_user_became_active", lambda _uid: False)
    monkeypatch.setattr(runner.talk_gate, "check", lambda *_args, **_kwargs: ("hard", "blocked"))
    monkeypatch.setattr(runner, "_context_messages", lambda *_args, **_kwargs: [])

    async def failed_turn(*_args, **_kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("core.llm_client.chat_turn", failed_turn)
    run = asyncio.run(
        runner._run_locked(
            job,
            state,
            Run(uid="owner", char_id="char", source="autonomy", job_id=job.id),
        )
    )

    assert run.disposition == "llm_failed"
    assert "episode:ep-failed" not in load_evaluated_memories()
    assert "episode:ep-failed" not in load_recalled_memories()


def test_high_urgency_signal_keeps_talk_semantics(sandbox, monkeypatch):
    from core.autonomy import store
    from core.autonomy.models import Opportunity
    from core.autonomy.signal_adapters import emit_trigger_signal

    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"enabled": True}})
    assert emit_trigger_signal(
        "owner", "char", "hr_critical", priority=0.95, urgency=0.95, now=1_000.0
    )[0]
    opportunity = Opportunity.merge(
        store.drain_pending_signals("owner", "char"), now=1_000.0
    )
    assert opportunity.action_mode == "talk"
    assert opportunity.urgency == 0.95
