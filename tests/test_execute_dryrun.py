import json
import sys
from types import SimpleNamespace
from datetime import date, datetime

import pytest


async def _run_dry(proposal):
    assert proposal is not None
    assert proposal.execute is not None
    result = await proposal.execute(dry_run=True)
    assert result.would_send_prompt
    return result


def _write_event_log(paths, uid: str, date_text: str, body: str) -> None:
    day_dir = paths.event_log() / uid
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{date_text}.md").write_text(body, encoding="utf-8")


@pytest.mark.asyncio
async def test_native_proposal_executes_dryrun_for_each_registered_trigger(monkeypatch, sandbox):
    from core.scheduler.triggers import birthday, diary, festival, garden_daily, garden_water
    from core.scheduler.triggers import memory, period, reminders, timenode, time_based, watch

    monkeypatch.setattr(time_based, "_cfg", lambda: {
        "enabled": True,
        "morning_greeting": True,
        "night_reminder": True,
        "random_message": True,
        "timenode": True,
        "festival": True,
        "holiday_boost": True,
        "owner_birthday": "04-24",
    })
    monkeypatch.setattr(diary, "_cfg", time_based._cfg)
    monkeypatch.setattr(period, "_cfg", time_based._cfg)
    monkeypatch.setattr(timenode, "_cfg", time_based._cfg)
    monkeypatch.setattr(festival, "_cfg", time_based._cfg)
    monkeypatch.setattr(memory, "_cfg", time_based._cfg)
    monkeypatch.setattr(birthday, "_cfg", time_based._cfg)

    for mod in (time_based, diary, period, timenode, festival, memory):
        monkeypatch.setattr(mod, "_owner_id", lambda: "u1")
    monkeypatch.setattr("core.scheduler.rhythm.is_present", lambda now=None: True)
    monkeypatch.setattr("core.scheduler.rhythm.quiet_floor_elapsed", lambda uid, now_ts=None: True)
    monkeypatch.setattr("core.scheduler.rhythm.triggered_on_logical_day", lambda name, now=None: False)
    monkeypatch.setattr("core.scheduler.loop._owner_id", lambda: "u1")
    monkeypatch.setattr("core.scheduler.loop._last_diary_share", 0.0)
    monkeypatch.setattr(diary, "_scheduler_start_time", 0.0)
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"tools": {"weather": {"enabled": True}}})
    monkeypatch.setattr(period, "_days_elapsed", lambda uid, today=None: today.day)
    monkeypatch.setattr(time_based, "_user_talked_today", lambda uid: False)
    monkeypatch.setattr(time_based, "_weather_location", lambda: "杭州")
    monkeypatch.setattr("core.tools.diary_reader.yesterday_missing", lambda: True)
    monkeypatch.setattr("core.memory.episodic_memory._load_memories", lambda uid: [
        {"id": "ep_exec_recall", "summary": "她说起实习", "yexuan_feeling": "有些在意", "strength": 0.8}
    ])
    monkeypatch.setattr(timenode, "_get_timenode", lambda today=None: "monday")
    monkeypatch.setattr(festival, "_get_today_festival", lambda today=None: ("x", "（节日 prompt）"))
    monkeypatch.setattr(festival, "_is_holiday_period", lambda today=None: True)
    monkeypatch.setattr(memory, "_char_name", lambda: "叶瑄")
    _write_event_log(
        sandbox,
        "u1",
        "2026-05-25",
        """
## 14:30
**用户**：我准备继续改实习材料
> turn_id:t1
**叶瑄**：我记得。
> emotion:gentle intensity:1 turn_id:t1
---
""",
    )

    now = datetime(2026, 5, 25, 15, 0)
    weather_detail = {
        "temp_c": 31,
        "humidity": 50,
        "precip_mm": 0.0,
        "cloud_cover": 50,
        "wind_kmph": 10,
        "desc": "晴",
        "is_day": True,
        "uv_index": 3,
        "received_at": now.timestamp(),
    }
    ctx = {"now_dt": now, "now_ts": now.timestamp(), "uid": "u1", "weather_detail": weather_detail}

    proposals = [
        birthday.propose({"now_dt": datetime(2026, 4, 23, 20, 0)}),
        birthday.propose({"now_dt": datetime(2026, 4, 24, 0, 4)}),
        birthday.propose({"now_dt": datetime(2026, 4, 24, 14, 0)}),
        birthday.propose({"now_dt": datetime(2026, 4, 24, 21, 0)}),
        period.propose({"uid": "u1", "today": date(2026, 5, 3)}),
        time_based.propose_morning_greeting({"now_dt": datetime(2026, 5, 25, 8, 0), "now_ts": now.timestamp()}),
        time_based.propose_night_reminder({"now_dt": datetime(2026, 5, 25, 23, 30), "now_ts": now.timestamp()}),
        time_based.propose_daily_journal({"now_dt": datetime(2026, 5, 25, 23, 30), "now_ts": now.timestamp()}),
        diary.propose_diary_reminder({"now_dt": datetime(2026, 5, 25, 10, 0), "now_ts": now.timestamp()}),
        diary.propose_diary_share_reminder({"now_dt": datetime(2026, 5, 25, 22, 30), "now_ts": now.timestamp()}),
        time_based.propose_random_message(ctx),
        time_based.propose_weather_alert(ctx),
        time_based.propose_spontaneous_recall(ctx),
        timenode.propose(ctx),
        festival.propose_festival(ctx),
        festival.propose_holiday_boost(ctx),
        reminders.propose({"now_dt": datetime(2026, 5, 25, 12, 30), "due_reminders": [
            {"id": "r1", "content": "交材料", "remind_at": "2026-05-25 12:00"}
        ]}),
        memory.propose(ctx),
        garden_water.propose_garden_bloom({"now_ts": 1_000.0, "garden_bloom_events": [
            {"type": "bloom", "name": "雏菊", "received_at": 990.0}
        ]}),
        garden_daily.propose_garden_harvest_expired({"now_ts": 1_000.0, "garden_daily_events": [
            {"type": "harvest_expired", "name": "玫瑰", "received_at": 990.0}
        ]}),
        garden_daily.propose_garden_handle_ask({"now_ts": 1_000.0, "garden_daily_events": [
            {"type": "harvest_handle", "handle_action": "ask", "name": "玫瑰", "received_at": 990.0}
        ]}),
        garden_daily.propose_garden_handle_gift({"now_ts": 1_000.0, "garden_daily_events": [
            {"type": "harvest_handle", "handle_action": "gift", "name": "玫瑰", "language": "珍重", "received_at": 990.0}
        ]}),
        garden_daily.propose_garden_handle_self({"now_ts": 1_000.0, "garden_daily_events": [
            {"type": "harvest_handle", "handle_action": "dry", "name": "玫瑰", "received_at": 990.0}
        ]}),
        garden_daily.propose_garden_vase_wilted({"now_ts": 1_000.0, "garden_daily_events": [
            {"type": "vase_wilted", "name": "玫瑰", "received_at": 990.0}
        ]}),
        watch.propose({"now_ts": 1_000.0, "heart_rate_event": {"value": 140, "hour": 14, "received_at": 990.0}}),
        watch.propose_hr_high({"now_ts": 1_000.0, "heart_rate_event": {"value": 110, "hour": 14, "received_at": 990.0}}),
        watch.propose_sleep_end({"now_ts": 1_000.0, "sleep_end_event": {"duration_minutes": 420, "received_at": 990.0}}),
    ]

    names = []
    for index, proposal in enumerate(proposals):
        assert proposal is not None, index
        result = await _run_dry(proposal)
        names.append(result.trigger_name)

    assert set(names) == {
        "hr_critical",
        "birthday_midnight",
        "birthday_eve",
        "birthday_afternoon",
        "birthday_night",
        "period_reminder",
        "morning_greeting",
        "night_reminder",
        "daily_journal",
        "diary_reminder",
        "diary_share_reminder",
        "random_message",
        "hr_high",
        "sleep_end",
        "weather_alert",
        "topic_followup",
        "timenode",
        "festival",
        "holiday_boost",
        "spontaneous_recall",
        "garden_bloom",
        "garden_harvest_expired",
        "garden_handle_ask",
        "garden_handle_gift",
        "garden_handle_self",
        "garden_vase_wilted",
        "reminders",
    }
    assert sandbox.execute_dryrun_log().exists()


@pytest.mark.asyncio
async def test_sleep_end_execute_false_preserves_cross_marks(monkeypatch):
    from core.scheduler import loop
    from core.scheduler.triggers import watch

    sent = []
    marks = []

    async def fake_send(prompt, search_query="", trigger_name="", **kwargs):
        sent.append((prompt, trigger_name))

    monkeypatch.setattr(loop, "_pipeline_send", fake_send)
    monkeypatch.setattr(loop, "_mark", lambda name: marks.append(name))

    proposal = watch.propose_sleep_end({
        "now_ts": 1_000.0,
        "sleep_end_event": {"duration_minutes": 420, "received_at": 990.0},
    })

    result = await proposal.execute(dry_run=False)

    assert result.would_mark == ["sleep_end", "morning_greeting"]
    assert marks == ["sleep_end", "morning_greeting"]
    assert sent and sent[0][1] == "sleep_end"


@pytest.mark.asyncio
async def test_topic_followup_execute_dryrun_writes_shadow_only(monkeypatch, sandbox):
    from core.scheduler.triggers import memory
    from core.scheduler.last_mentioned import load_followed_topics, load_followed_topics_shadow

    monkeypatch.setattr(memory, "_cfg", lambda: {"topic_followup": True})
    monkeypatch.setattr(memory, "_owner_id", lambda: "u1")
    _write_event_log(
        sandbox,
        "u1",
        "2026-05-25",
        """
## 14:30
**用户**：我准备继续改实习材料
> turn_id:t1
**叶瑄**：我记得。
> emotion:gentle intensity:1 turn_id:t1
---
""",
    )

    proposal = memory.propose({"now_dt": datetime(2026, 5, 25, 16, 0), "uid": "u1"})
    result = await proposal.execute(dry_run=True)

    assert "继续改实习材料" in result.would_send_prompt
    assert result.topic_key == "继续改实习材料"
    assert load_followed_topics() == {}
    assert "继续改实习材料" in load_followed_topics_shadow()
    row = json.loads(sandbox.execute_dryrun_log().read_text(encoding="utf-8").splitlines()[-1])
    assert row["topic_key"] == "继续改实习材料"


@pytest.mark.asyncio
async def test_topic_followup_dryrun_shadow_blocks_second_propose(monkeypatch, sandbox):
    from core.scheduler.triggers import memory

    monkeypatch.setattr(memory, "_cfg", lambda: {"topic_followup": True})
    monkeypatch.setattr(memory, "_owner_id", lambda: "u1")
    _write_event_log(
        sandbox,
        "u1",
        "2026-05-25",
        """
## 14:30
**用户**：我准备继续改实习材料
> turn_id:t1
**叶瑄**：我记得。
> emotion:gentle intensity:1 turn_id:t1
---
""",
    )
    ctx = {"now_dt": datetime(2026, 5, 25, 16, 0), "now_ts": 1_000.0, "uid": "u1"}

    first = memory.propose(ctx)
    assert first is not None
    await first.execute(dry_run=True)

    second = memory.propose({**ctx, "now_ts": 1_000.0 + 60})
    assert second is None


@pytest.mark.asyncio
async def test_topic_followup_execute_live_writes_followed_topics(monkeypatch, sandbox):
    from core.scheduler import execution
    from core.scheduler import loop
    from core.scheduler.triggers import memory
    from core.scheduler.last_mentioned import (
        load_followed_topics,
        load_followed_topics_shadow,
        mark_topic_followed_shadow,
    )

    async def fake_send(prompt, search_query="", trigger_name="", **kwargs):
        return None

    monkeypatch.setattr(execution, "EXECUTE_MODE", "live")
    monkeypatch.setattr(loop, "_pipeline_send", fake_send)
    monkeypatch.setattr(loop, "_mark", lambda name: None)
    monkeypatch.setattr(memory, "_cfg", lambda: {"topic_followup": True})
    monkeypatch.setattr(memory, "_owner_id", lambda: "u1")
    _write_event_log(
        sandbox,
        "u1",
        "2026-05-25",
        """
## 14:30
**用户**：我准备继续改实习材料
> turn_id:t1
**叶瑄**：我记得。
> emotion:gentle intensity:1 turn_id:t1
---
""",
    )
    mark_topic_followed_shadow("继续改实习材料", now_ts=1_000.0)

    proposal = memory.propose({"now_dt": datetime(2026, 5, 25, 16, 0), "uid": "u1"})
    result = await proposal.execute(dry_run=False)

    assert result.sent is True
    assert "继续改实习材料" in load_followed_topics()
    assert "继续改实习材料" in load_followed_topics_shadow()


@pytest.mark.asyncio
async def test_spontaneous_recall_dryrun_shadow_blocks_second_propose(monkeypatch, sandbox):
    from core.scheduler.triggers import time_based
    from core.scheduler.last_mentioned import load_recalled_memories, load_recalled_memories_shadow

    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr("core.scheduler.rhythm.silence_ratio", lambda uid, now_ts=None: 1.0)
    memory = {
        "id": "ep_repeat",
        "narrative_summary": "她说起实习",
        "emotion_texture": "被认真接住的安心",
        "strength": 0.9,
    }
    ctx = {
        "now_dt": datetime(2026, 5, 25, 16, 0),
        "now_ts": 1_000.0,
        "uid": "u1",
        "episodic_memories": [memory],
    }

    first = time_based.propose_spontaneous_recall(ctx)
    assert first is not None
    result = await first.execute(dry_run=True)

    assert result.topic_key == "episode:ep_repeat"
    assert "她说起实习" in result.would_send_prompt
    assert load_recalled_memories() == {}
    assert "episode:ep_repeat" in load_recalled_memories_shadow()

    second = time_based.propose_spontaneous_recall({**ctx, "now_ts": 1_000.0 + 60})
    assert second is None


@pytest.mark.asyncio
async def test_reminder_execute_captures_mark_done_id(monkeypatch, sandbox):
    from core.scheduler.triggers import reminders

    monkeypatch.setattr("core.scheduler.loop._owner_id", lambda: "u1")

    proposal = reminders.propose({
        "now_dt": datetime(2026, 5, 25, 12, 30),
        "due_reminders": [{"id": "r42", "content": "交材料", "remind_at": "2026-05-25 12:00"}],
    })

    result = await proposal.execute(dry_run=True)

    assert result.would_mark_done == ["r42"]


@pytest.mark.asyncio
async def test_reminder_execute_live_marks_done(monkeypatch, sandbox):
    from core.scheduler import loop
    from core.scheduler.triggers import reminders

    done = []

    async def fake_send(prompt, search_query="", trigger_name="", **kwargs):
        return None

    monkeypatch.setattr(loop, "_pipeline_send", fake_send)
    monkeypatch.setattr("core.scheduler.loop._owner_id", lambda: "u1")
    monkeypatch.setattr("core.tools.reminder.mark_done", lambda uid, rid: done.append((uid, rid)))

    proposal = reminders.propose({
        "now_dt": datetime(2026, 5, 25, 12, 30),
        "due_reminders": [{"id": "r42", "content": "交材料", "remind_at": "2026-05-25 12:00"}],
    })

    result = await proposal.execute(dry_run=False)

    assert result.sent is True
    assert done == [("u1", "r42")]


@pytest.mark.asyncio
async def test_diary_share_execute_live_marks_last_share(monkeypatch, sandbox):
    from core.scheduler import loop
    from core.scheduler.triggers import diary

    async def fake_send(prompt, search_query="", trigger_name="", **kwargs):
        return None

    monkeypatch.setattr(loop, "_pipeline_send", fake_send)
    monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
    monkeypatch.setattr(loop, "_last_diary_share", 0.0)
    monkeypatch.setattr(diary, "_owner_id", lambda: "u1")
    monkeypatch.setattr(diary, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(diary, "_scheduler_start_time", 0.0)
    monkeypatch.setattr("core.scheduler.rhythm.quiet_floor_elapsed", lambda uid, now_ts=None: True)
    monkeypatch.setattr("core.scheduler.rhythm.triggered_on_logical_day", lambda name, now=None: False)

    proposal = diary.propose_diary_share_reminder({
        "now_dt": datetime(2026, 5, 25, 22, 30),
        "now_ts": datetime(2026, 5, 25, 22, 30).timestamp(),
    })

    result = await proposal.execute(dry_run=False)

    assert result.sent is True
    assert loop._last_diary_share > 0
    raw = json.loads(sandbox.scheduler_state().read_text(encoding="utf-8"))
    assert raw["last_diary_share"] == loop._last_diary_share


@pytest.mark.asyncio
async def test_weather_execute_dryrun_reads_cache_without_fetch(monkeypatch):
    from core.scheduler.triggers import time_based

    monkeypatch.setattr(time_based, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"tools": {"weather": {"enabled": True}}})
    monkeypatch.setattr(time_based, "_weather_location", lambda: "杭州")
    called = []

    async def fake_fetch(location):
        called.append(location)
        raise AssertionError("weather execute must not fetch")

    monkeypatch.setitem(
        sys.modules,
        "core.tools.weather",
        SimpleNamespace(get_weather_detail=fake_fetch),
    )
    now = datetime(2026, 5, 25, 12, 0)
    proposal = time_based.propose_weather_alert({
        "now_dt": now,
        "now_ts": now.timestamp(),
        "weather_detail": {
            "temp_c": 31,
            "humidity": 50,
            "precip_mm": 0.0,
            "cloud_cover": 50,
            "wind_kmph": 10,
            "desc": "晴",
            "is_day": True,
            "uv_index": 3,
            "received_at": now.timestamp(),
        },
    })

    result = await proposal.execute(dry_run=True)

    assert result.reads_cache_ok is True
    assert not called


@pytest.mark.asyncio
async def test_weather_live_old_tick_refreshes_cache_without_sending(monkeypatch):
    from core.scheduler import execution, loop
    from core.scheduler.triggers import time_based

    sent = []
    detail = {
        "temp_c": 31,
        "humidity": 50,
        "precip_mm": 0.0,
        "cloud_cover": 50,
        "wind_kmph": 10,
        "desc": "晴",
        "is_day": True,
        "uv_index": 3,
    }

    async def fake_send(prompt, search_query="", trigger_name="", **kwargs):
        sent.append((prompt, trigger_name))

    async def fake_fetch(location):
        return dict(detail)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 5, 25, 12, 0)

    monkeypatch.setattr(execution, "EXECUTE_MODE", "live")
    monkeypatch.setattr(loop, "_pipeline_send", fake_send)
    monkeypatch.setattr(time_based, "_pipeline_send", fake_send)
    monkeypatch.setattr(time_based, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr(time_based, "datetime", FakeDatetime)
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"tools": {"weather": {"enabled": True}}})
    monkeypatch.setattr("core.memory.user_profile.load", lambda uid: {"location": "杭州"})
    monkeypatch.setitem(sys.modules, "core.tools.weather", SimpleNamespace(get_weather_detail=fake_fetch))

    await time_based._check_weather(force=False)

    cached = time_based.get_last_weather_detail()
    assert cached and cached["desc"] == "晴"
    assert sent == []


@pytest.mark.asyncio
async def test_gating_dryrun_executes_only_winner(monkeypatch, sandbox):
    from core.scheduler import gating, proposer_registry
    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState

    executed = []

    async def make_execute(name):
        async def execute(*, dry_run: bool):
            executed.append((name, dry_run))
            from core.scheduler.execution import ExecuteResult, write_execute_dryrun

            result = ExecuteResult(trigger_name=name, would_send_prompt=name, dry_run=dry_run)
            if dry_run:
                write_execute_dryrun(result)
            return result

        return execute

    proposer_registry._reset_for_tests()
    monkeypatch.setattr(proposer_registry, "_BUILTINS_LOADED", True)
    proposer_registry.register_proposer(
        "low",
        lambda ctx: TriggerProposal("low", 0.2, "random", [TriggerState.QUIET], execute=low_execute),
    )
    proposer_registry.register_proposer(
        "high",
        lambda ctx: TriggerProposal("high", 0.9, "random", [TriggerState.QUIET], execute=high_execute),
    )
    monkeypatch.setattr(gating, "get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr(gating, "is_trigger_ready", lambda name: True)

    low_execute = await make_execute("low")
    high_execute = await make_execute("high")
    await gating.run_shadow_tick("u1")

    assert executed == [("high", True)]
    proposer_registry._reset_for_tests()


@pytest.mark.asyncio
async def test_gating_live_executes_winner_live(monkeypatch, sandbox):
    from core.scheduler import execution, gating, proposer_registry
    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState

    executed = []

    async def execute(*, dry_run: bool):
        executed.append(dry_run)
        from core.scheduler.execution import ExecuteResult

        return ExecuteResult(trigger_name="live", would_send_prompt="live", dry_run=dry_run, sent=not dry_run)

    proposer_registry._reset_for_tests()
    monkeypatch.setattr(proposer_registry, "_BUILTINS_LOADED", True)
    monkeypatch.setattr(execution, "EXECUTE_MODE", "live")
    proposer_registry.register_proposer(
        "live",
        lambda ctx: TriggerProposal("live", 0.9, "random", [TriggerState.QUIET], execute=execute),
    )
    monkeypatch.setattr(gating, "get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr(gating, "is_trigger_ready", lambda name: True)

    await gating.run_shadow_tick("u1")

    assert executed == [False]
    proposer_registry._reset_for_tests()
