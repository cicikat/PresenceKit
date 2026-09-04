from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_migrated_pipeline_send_queues_a_signal_without_touching_pipeline(sandbox, monkeypatch):
    from core.autonomy import store
    from core.scheduler import loop

    monkeypatch.setattr(loop, "_owner_id", lambda: "owner")
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: "char")
    monkeypatch.setattr(
        "core.pipeline_registry.get",
        lambda: (_ for _ in ()).throw(AssertionError("migrated trigger must not start a pipeline turn")),
    )

    result = await loop._pipeline_send("legacy template", trigger_name="morning_greeting")

    assert result is None
    state = store.load("owner", "char")
    pending = state["pending_signals"]
    assert len(pending) == 1
    signal = pending[0]["signal"]
    assert signal["source"] == "scheduler"
    assert signal["evidence"] == [{
        "fact": "legacy_trigger_candidate",
        "trigger": "morning_greeting",
        "routine_key": "morning_greeting",
    }]
    assert signal["action_mode"] == "none"


def test_pending_trigger_signals_merge_into_one_opportunity(sandbox):
    from core.autonomy import store
    from core.autonomy.models import Opportunity
    from core.autonomy.signal_adapters import emit_trigger_signal

    assert emit_trigger_signal("owner", "char", "morning_greeting")[1] == "queued"
    assert emit_trigger_signal("owner", "char", "random_message")[1] == "queued"

    signals = store.drain_pending_signals("owner", "char")
    opportunity = Opportunity.merge(signals)

    assert len(opportunity.signals) == 2
    assert {item["source"] for item in opportunity.signals} == {"scheduler"}
    assert store.load("owner", "char")["pending_signals"] == []


def test_scheduler_alias_is_canonicalized_before_signal_admission(sandbox):
    from types import SimpleNamespace
    from core.autonomy import store
    from core.autonomy.signal_adapters import emit_scheduler_proposal_signal

    proposal = SimpleNamespace(trigger_name="morning", urgency=0.2, metadata={})
    assert emit_scheduler_proposal_signal("owner", "char", proposal)[1] == "queued"
    signal = store.load("owner", "char")["pending_signals"][0]["signal"]
    assert signal["evidence"][0]["trigger"] == "morning_greeting"


def test_trigger_migration_registry_separates_speech_and_maintenance():
    from core.scheduler.gating import (
        MAINTENANCE_ONLY_TRIGGERS,
        MIGRATED_TRIGGERS,
        RETIRED_TRIGGER_EXECUTORS,
        ACTIVE_TRIGGERS,
        trigger_migration_status,
    )

    assert MIGRATED_TRIGGERS.isdisjoint(MAINTENANCE_ONLY_TRIGGERS)
    assert trigger_migration_status("morning_greeting") == "migrated"
    assert trigger_migration_status("memory_janitor") == "maintenance-only"
    assert trigger_migration_status("manual_direct_trigger") == "retired"
    assert "practice_help" in MIGRATED_TRIGGERS
    assert "dream_postcards" in ACTIVE_TRIGGERS
    assert trigger_migration_status("dream_postcards") == "active"


@pytest.mark.asyncio
async def test_manual_trigger_never_falls_back_to_direct_executor(sandbox, monkeypatch):
    from core.scheduler import loop

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("retired manual trigger must not execute a scheduler trigger")

    monkeypatch.setattr(loop, "_pipeline_send", forbidden)
    assert "不支持直接执行" in await loop.manual_trigger("manual_direct_trigger")
    assert "未知或未注册" in await loop.manual_trigger("not_registered")


def test_reminder_runtime_failure_does_not_write_legacy_store(sandbox, monkeypatch):
    from core.tools import reminder

    monkeypatch.setattr(
        "core.agent_runtime.scheduler_capability.create_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("runtime down")),
    )
    result = reminder.add_reminder("owner", "test reminder", "23:59")
    assert "无法创建" in result
    assert reminder._load("owner") == []


def test_scheduler_reminder_checker_has_no_legacy_pipeline_send():
    from core.scheduler import loop
    import inspect

    assert "_pipeline_send" not in inspect.getsource(loop._check_reminders)


@pytest.mark.asyncio
async def test_talk_owner_delivery_is_idempotent_per_correlation(sandbox, monkeypatch):
    from core.autonomy import talk_gate

    monkeypatch.setattr(talk_gate, "check", lambda *_args, **_kwargs: ("allow", "ok"))
    monkeypatch.setattr("core.pipeline_registry.get", lambda: object())
    monkeypatch.setattr("channels.registry.get_active", lambda: [object()])
    monkeypatch.setattr("core.response_processor.strip_render_tags", lambda text: text)
    monkeypatch.setattr("core.reality_output_scrubber.scrub_reality_output_text", lambda text: text)
    monkeypatch.setattr("core.scheduler.proactive_ledger.record_send", lambda *_args, **_kwargs: None)
    sent = []

    async def record(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(fanout_targets=["desktop"])

    monkeypatch.setattr("core.turn_sink.record_assistant_turn", record)
    first = await talk_gate.send("owner", "char", "one message", source="scheduler", run_id="run-1", correlation_id="op-1")
    second = await talk_gate.send("owner", "char", "one message", source="scheduler", run_id="run-2", correlation_id="op-1")

    assert first == (True, "sent")
    assert second == (False, "duplicate")
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_manual_migrated_trigger_queues_an_opportunity(sandbox, monkeypatch):
    from core.autonomy import store
    from core.scheduler import loop

    monkeypatch.setattr(loop, "_owner_id", lambda: "owner")
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: "char")

    result = await loop.manual_trigger("random_message")

    assert "autonomy opportunity queued" in result
    assert store.load("owner", "char")["pending_signals"]


def test_pending_signal_queue_is_observable_without_prompt_text(sandbox, monkeypatch):
    from core.autonomy import store
    from core.autonomy.signal_adapters import emit_trigger_signal
    import admin.routers.autonomy as api

    monkeypatch.setattr(api, "_scope", lambda: ("owner", "char"))
    monkeypatch.setattr("core.autonomy.talk_gate.check", lambda _uid: ("allow", "ok"))
    emit_trigger_signal("owner", "char", "morning_greeting")
    payload = asyncio.run(api.status(auth=None))

    assert payload["queued_signals"][0]["source"] == "scheduler"
    assert "legacy template" not in str(payload["queued_signals"])


@pytest.mark.asyncio
async def test_time_based_migrated_checks_do_not_call_legacy_send(sandbox, monkeypatch):
    from core.autonomy import store
    from core.scheduler.triggers import time_based

    monkeypatch.setattr(time_based, "_cfg", lambda: {"morning_greeting": True, "random_message": True})
    monkeypatch.setattr(time_based, "_is_ready", lambda _name: True)
    monkeypatch.setattr(time_based, "_owner_id", lambda: "owner")
    monkeypatch.setattr(time_based, "_active_char_id_or_none", lambda: "char")
    monkeypatch.setattr(time_based, "_user_talked_today", lambda _uid: False)
    monkeypatch.setattr(time_based, "_mark", lambda _name: None)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("migrated time trigger must not call _pipeline_send")

    monkeypatch.setattr(time_based, "_pipeline_send", forbidden)
    await time_based._check_morning(force=True)
    await time_based._check_random_message(force=True)

    assert len(store.load("owner", "char")["pending_signals"]) == 2
