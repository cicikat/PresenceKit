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
    assert "letter_writer" in ACTIVE_TRIGGERS
    assert "letter_writer" not in MIGRATED_TRIGGERS
    assert trigger_migration_status("letter_writer") == "active"
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


def test_migrated_speech_checks_are_gone_from_legacy_gather():
    import inspect
    from core.scheduler import loop
    from core.scheduler.triggers import birthday, diary, festival, memory, period, time_based, timenode

    source = inspect.getsource(loop._loop)
    # Only live gather calls matter; comments may still mention retired names.
    call_block = source.split("asyncio.gather", 1)[1]
    for name in (
        "_check_morning()",
        "_check_night()",
        "_check_random_message()",
        "_check_daily_journal()",
        "_check_spontaneous_recall()",
        "_check_period()",
        "_check_diary_reminder()",
        "_check_diary_share_reminder()",
        "_check_topic_followup()",
        "_check_birthday_midnight()",
        "_check_timenode()",
        "_check_festival()",
        "_check_holiday_boost()",
    ):
        assert name not in call_block
    assert "_check_weather()" in call_block
    assert "_check_reminders()" in call_block
    assert "_check_diary_inject()" in call_block
    assert "_check_inner_diary_write()" in call_block
    assert "_check_sensor_aware()" in call_block
    assert "letter_writer" not in call_block
    assert "legacy_tick_should_send" not in call_block
    assert not hasattr(time_based, "_check_morning")
    assert not hasattr(time_based, "_check_random_message")
    assert not hasattr(period, "_check_period")
    assert not hasattr(diary, "_check_diary_reminder")
    assert not hasattr(memory, "_check_topic_followup")
    assert not hasattr(birthday, "_check_birthday_midnight")
    assert not hasattr(festival, "_check_festival")
    assert not hasattr(timenode, "_check_timenode")
    assert hasattr(time_based, "_check_inner_diary_write")
    assert hasattr(time_based, "_check_weather")
    assert hasattr(diary, "_check_diary_inject")


@pytest.mark.asyncio
async def test_weather_cache_refresh_does_not_speak(sandbox, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    from core.scheduler.triggers import time_based

    sent = []

    async def forbidden(*_args, **_kwargs):
        sent.append(True)
        raise AssertionError("weather cache refresh must not speak")

    class FakeDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 5, 25, 12, 0)

    async def fake_fetch(_location):
        return {"desc": "晴", "temp_c": 22, "humidity": 40, "precip_mm": 0,
                "cloud_cover": 10, "wind_kmph": 5, "is_day": True, "uv_index": 3}

    monkeypatch.setattr(time_based, "datetime", FakeDatetime)
    monkeypatch.setattr(time_based, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(time_based, "_owner_id", lambda: "owner")
    monkeypatch.setattr("core.scheduler.loop._pipeline_send", forbidden)
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"tools": {"weather": {"enabled": True}}})
    monkeypatch.setattr("core.memory.user_profile.load", lambda uid: {"location": "杭州"})
    monkeypatch.setitem(__import__("sys").modules, "core.tools.weather", SimpleNamespace(get_weather_detail=fake_fetch))

    await time_based._check_weather(force=False)

    cached = time_based.get_last_weather_detail()
    assert cached and cached["desc"] == "晴"
    assert sent == []


@pytest.mark.asyncio
async def test_execute_prompt_marks_per_character_without_global_dual_write(sandbox, monkeypatch):
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID
    from core.scheduler import execution, loop

    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: TEST_CHAR_ID)
    monkeypatch.setattr(loop, "_owner_id", lambda: "owner")

    async def fake_send(*_args, **_kwargs):
        return "sent"

    monkeypatch.setattr(loop, "_pipeline_send", fake_send)
    monkeypatch.setattr("core.scheduler.proactive_ledger.record_send", lambda *_args, **_kwargs: None)
    loop._last_trigger.clear()

    result = await execution.execute_prompt(
        trigger_name="legacy_dream_guard_active",
        prompt_factory=lambda: "compat",
        dry_run=False,
        would_mark=["legacy_dream_guard_active"],
        char_id=TEST_CHAR_ID,
        write_trigger_stub=False,
    )

    assert result.sent is True
    assert f"{TEST_CHAR_ID}:legacy_dream_guard_active" in loop._last_trigger
    assert "legacy_dream_guard_active" not in loop._last_trigger
    assert loop._is_ready("legacy_dream_guard_active", char_id=TEST_CHAR_ID) is False
    assert loop._is_ready("legacy_dream_guard_active", char_id=TEST_PEER_CHAR_ID) is True


def test_logical_day_dedupe_is_per_character(sandbox, monkeypatch):
    from datetime import datetime
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID
    from core.scheduler import loop, rhythm

    now = datetime(2026, 5, 24, 8, 0)
    monkeypatch.setattr(loop.time, "time", lambda: now.timestamp())
    loop._last_trigger.clear()
    loop._mark("night_reminder", char_id=TEST_CHAR_ID)

    assert rhythm.triggered_on_logical_day("night_reminder", now, char_id=TEST_CHAR_ID) is True
    assert rhythm.triggered_on_logical_day("night_reminder", now, char_id=TEST_PEER_CHAR_ID) is False
    assert rhythm.triggered_on_logical_day("night_reminder", now) is False


def test_proactive_ledger_still_rate_limits_across_characters(sandbox, monkeypatch):
    from core.scheduler import proactive_ledger

    monkeypatch.setattr(proactive_ledger, "_gap_seconds", lambda: 90 * 60)
    monkeypatch.setattr(proactive_ledger, "_daily_budget", lambda: 8)
    proactive_ledger.record_send("morning_greeting", uid="owner", char_id="char-a")
    allowed, reason = proactive_ledger.can_send("night_reminder", uid="owner")
    assert allowed is False
    assert reason == "gap_not_elapsed"


def test_legacy_tick_should_send_force_still_works_in_live_mode(monkeypatch):
    from core.scheduler import execution

    monkeypatch.setattr(execution, "EXECUTE_MODE", "live")
    assert execution.legacy_tick_should_send(force=True) is True
    assert execution.legacy_tick_should_send(force=False) is False


@pytest.mark.asyncio
async def test_manual_maintenance_and_unregistered_never_speak(sandbox, monkeypatch):
    from core.scheduler import loop

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("manual maintenance or unregistered names must not speak")

    monkeypatch.setattr(loop, "_pipeline_send", forbidden)
    monkeypatch.setattr(loop, "_owner_id", lambda: "owner")
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: "char")

    assert "maintenance task queued" in await loop.manual_trigger("inner_diary_write")
    assert "未知或未注册" in await loop.manual_trigger("not_registered")


@pytest.mark.asyncio
async def test_shadow_tick_migrated_winner_queues_signal_without_executor(sandbox, monkeypatch):
    from core.autonomy import store
    from core.scheduler import gating
    from core.scheduler.state_machine import TriggerState

    executed = []

    async def execute(*, dry_run):
        executed.append(dry_run)
        raise AssertionError("migrated winner must not run historical executor")

    proposal = gating.TriggerProposal(
        trigger_name="topic_followup",
        urgency=0.9,
        topic_source="random",
        requires_state=[TriggerState.QUIET],
        execute=execute,
        char_id="char",
    )
    monkeypatch.setattr(gating, "_collect_native_proposals", lambda _ctx: [proposal])
    monkeypatch.setattr(gating, "_shadow_cfg", lambda: {"enabled": True, "max_size_mb": 5, "keep": 3})
    monkeypatch.setattr(gating, "get_current_state", lambda _uid: TriggerState.QUIET)
    monkeypatch.setattr(gating, "is_trigger_ready", lambda _name, **_kwargs: True)
    monkeypatch.setattr("core.character_loader.is_proactive_disabled", lambda **_k: False)
    monkeypatch.setattr("core.scheduler.proactive_ledger.can_send", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr("core.scheduler.loop._user_active_recently", lambda: False)
    monkeypatch.setattr("core.scheduler.triggers.dnd.is_dnd", lambda _uid: False)
    monkeypatch.setattr("core.scheduler.loop._active_char_id_or_none", lambda: "char")

    picked = await gating.run_shadow_tick("owner")

    assert picked is not None
    assert picked.trigger_name == "topic_followup"
    assert executed == []
    pending = store.load("owner", "char")["pending_signals"]
    assert pending
    assert pending[0]["signal"]["evidence"][0]["trigger"] == "topic_followup"


@pytest.mark.asyncio
async def test_sensor_aware_handle_tick_queues_signal_without_delivery(sandbox, monkeypatch):
    from core.autonomy import store
    from core.scheduler.triggers import sensor_aware

    sent = []

    async def forbidden(*_args, **_kwargs):
        sent.append(True)
        raise AssertionError("sensor_aware must not deliver through _pipeline_send")

    event = {"type": "IDLE_LONG", "narrative": "idle", "context": {}}

    async def fake_judge(_ev):
        return {"score": 80, "reason": "ok", "intent_tier": "act"}

    monkeypatch.setattr(sensor_aware.sensor_events, "tick", lambda: [event])
    monkeypatch.setattr(sensor_aware.sensor_judge, "judge", fake_judge)
    monkeypatch.setattr("core.scheduler.loop._pipeline_send", forbidden)
    monkeypatch.setattr(sensor_aware, "_owner_id", lambda: "owner")
    monkeypatch.setattr("core.scheduler.loop._owner_id", lambda: "owner")
    monkeypatch.setattr("core.scheduler.loop._active_char_id_or_none", lambda: "char")
    monkeypatch.setattr("core.scheduler.proactive_ledger.can_send", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr("core.scheduler.triggers.dnd.is_dnd", lambda *_a, **_k: False)

    await sensor_aware.handle_tick()

    assert sent == []
    pending = store.load("owner", "char")["pending_signals"]
    assert pending
    evidence = pending[0]["signal"]["evidence"][0]
    assert evidence["fact"] == "sensor_candidate"
    assert evidence["event_type"] == "IDLE_LONG"
