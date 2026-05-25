from datetime import date, datetime
import inspect


def _write_event_log(paths, uid: str, date_text: str, body: str) -> None:
    day_dir = paths.event_log() / uid
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{date_text}.md").write_text(body, encoding="utf-8")


def test_hr_critical_propose_absent_when_heart_rate_normal():
    from core.scheduler.triggers import watch

    proposal = watch.propose({
        "now_ts": 1_000.0,
        "heart_rate_event": {"value": 85, "hour": 14, "received_at": 990.0},
    })

    assert proposal is None


def test_hr_critical_propose_uses_must_not_miss_tier_when_over_threshold():
    from core.scheduler.triggers import watch

    proposal = watch.propose({
        "now_ts": 1_000.0,
        "heart_rate_event": {"value": 140, "hour": 14, "received_at": 990.0},
    })

    assert proposal.trigger_name == "hr_critical"
    assert 0.90 <= proposal.urgency <= 1.00
    assert proposal.bypass_state_machine is True


def test_birthday_propose_preserves_four_time_windows(monkeypatch):
    from core.scheduler.triggers import birthday

    monkeypatch.setattr(birthday, "_cfg", lambda: {"owner_birthday": "04-24"})

    cases = [
        (datetime(2026, 4, 23, 20, 0), "birthday_eve"),
        (datetime(2026, 4, 24, 0, 4), "birthday_midnight"),
        (datetime(2026, 4, 24, 14, 0), "birthday_afternoon"),
        (datetime(2026, 4, 24, 21, 0), "birthday_night"),
    ]
    for now_dt, trigger_name in cases:
        proposal = birthday.propose({"now_dt": now_dt})
        assert proposal.trigger_name == trigger_name
        assert 0.90 <= proposal.urgency <= 1.00
        assert proposal.bypass_state_machine is True

    assert birthday.propose({"now_dt": datetime(2026, 4, 24, 8, 0)}) is None


def test_period_propose_uses_real_windows(monkeypatch):
    from core.scheduler.triggers import period

    monkeypatch.setattr(period, "_days_elapsed", lambda uid, today=None: today.day)

    in_period = period.propose({"uid": "u1", "today": date(2026, 5, 3)})
    upcoming = period.propose({"uid": "u1", "today": date(2026, 5, 29)})
    outside = period.propose({"uid": "u1", "today": date(2026, 5, 12)})

    assert in_period.trigger_name == "period_reminder"
    assert upcoming.trigger_name == "period_reminder"
    assert 0.70 <= in_period.urgency <= 0.89
    assert 0.70 <= upcoming.urgency <= 0.89
    assert in_period.bypass_state_machine is True
    assert outside is None


def test_gating_shadow_collects_native_from_registry(monkeypatch):
    from core.scheduler import gating
    from core.scheduler import proposer_registry
    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState

    native = TriggerProposal(
        trigger_name="period_reminder",
        urgency=0.8,
        topic_source="mood_match",
        requires_state=[TriggerState.CHATTING, TriggerState.QUIET, TriggerState.RESTLESS],
        bypass_state_machine=True,
    )

    proposer_registry._reset_for_tests()
    monkeypatch.setattr(proposer_registry, "_BUILTINS_LOADED", True)
    proposer_registry.register_proposer("period_reminder", lambda ctx: native)

    proposals = gating._collect_native_proposals({"uid": "u1"})

    assert [p.trigger_name for p in proposals] == ["period_reminder"]
    proposer_registry._reset_for_tests()


def test_window_event_proposals_use_window_tier(monkeypatch):
    from core.scheduler.triggers import festival, timenode

    monkeypatch.setattr(timenode, "_cfg", lambda: {"timenode": True})
    monkeypatch.setattr(timenode, "_owner_id", lambda: "u1")
    monkeypatch.setattr(timenode, "_get_timenode", lambda today=None: "monday")
    t = timenode.propose({"now_dt": datetime(2026, 5, 25, 18, 0)})

    monkeypatch.setattr(festival, "_cfg", lambda: {"festival": True, "holiday_boost": True})
    monkeypatch.setattr(festival, "_owner_id", lambda: "u1")
    monkeypatch.setattr(festival, "_get_today_festival", lambda today=None: ("x", "prompt"))
    f = festival.propose_festival({"now_dt": datetime(2026, 5, 25, 18, 0)})

    assert 0.70 <= t.urgency <= 0.89
    assert 0.70 <= f.urgency <= 0.89


def test_weather_heavy_propose_uses_window_event_tier(monkeypatch):
    from core.scheduler.triggers import time_based

    monkeypatch.setattr(time_based, "_cfg", lambda: {"enabled": True})
    detail = {
        "temp_c": 31,
        "humidity": 50,
        "precip_mm": 0.0,
        "cloud_cover": 50,
        "wind_kmph": 10,
        "desc": "晴",
        "is_day": True,
        "uv_index": 3,
        "received_at": datetime(2026, 5, 25, 12, 0).timestamp(),
    }

    proposal = time_based.propose_weather_alert({
        "now_dt": datetime(2026, 5, 25, 12, 0),
        "now_ts": datetime(2026, 5, 25, 12, 0).timestamp(),
        "weather_detail": detail,
    })

    assert proposal.trigger_name == "weather_alert"
    assert 0.70 <= proposal.urgency <= 0.89


def test_reminders_propose_bypasses_state_machine(monkeypatch):
    from core.scheduler.triggers import reminders

    monkeypatch.setattr("core.scheduler.loop._owner_id", lambda: "u1")
    proposal = reminders.propose({
        "now_dt": datetime(2026, 5, 25, 12, 30),
        "due_reminders": [{"id": "r1", "content": "x", "remind_at": "2026-05-25 12:00"}],
    })

    assert proposal.trigger_name == "reminders"
    assert proposal.bypass_state_machine is True
    assert 0.70 <= proposal.urgency <= 0.89


def test_reactive_watch_proposals_use_recent_events():
    from core.scheduler.triggers import watch

    hr = watch.propose_hr_high({
        "now_ts": 1_000.0,
        "heart_rate_event": {"value": 110, "hour": 14, "received_at": 990.0},
    })
    sleep = watch.propose_sleep_end({
        "now_ts": 1_000.0,
        "sleep_end_event": {"duration_minutes": 420, "received_at": 990.0},
    })

    assert hr.trigger_name == "hr_high"
    assert sleep.trigger_name == "sleep_end"
    assert 0.30 <= hr.urgency <= 0.49
    assert 0.30 <= sleep.urgency <= 0.49


def test_topic_followup_propose_uses_event_log_last_mentioned(monkeypatch, sandbox):
    from core.scheduler.triggers import memory

    monkeypatch.setattr(memory, "_cfg", lambda: {"topic_followup": True})
    monkeypatch.setattr(memory, "_owner_id", lambda: "u1")
    _write_event_log(
        sandbox,
        "u1",
        "2026-05-25",
        """
## 15:00
**用户**：我准备继续改实习材料
> turn_id:t1
**叶瑄**：我记得。
> emotion:gentle intensity:1 turn_id:t1
---
""",
    )

    proposal = memory.propose({
        "now_dt": datetime(2026, 5, 25, 16, 0),
        "uid": "u1",
    })

    assert proposal.trigger_name == "topic_followup"
    assert 0.30 <= proposal.urgency <= 0.49


def test_topic_followup_new_propose_has_no_character_growth_dependency():
    from core.scheduler.triggers import memory

    assert "character_growth" not in inspect.getsource(memory.propose)


def test_topic_followup_propose_skips_recently_followed_topic(monkeypatch, sandbox):
    from core.scheduler.triggers import memory
    from core.scheduler.last_mentioned import mark_topic_followed_shadow

    monkeypatch.setattr(memory, "_cfg", lambda: {"topic_followup": True})
    monkeypatch.setattr(memory, "_owner_id", lambda: "u1")
    _write_event_log(
        sandbox,
        "u1",
        "2026-05-25",
        """
## 15:00
**用户**：我准备继续改实习材料
> turn_id:t1
**叶瑄**：我记得。
> emotion:gentle intensity:1 turn_id:t1
---
""",
    )
    mark_topic_followed_shadow("继续改实习材料", now_ts=1_000.0)

    proposal = memory.propose({
        "now_dt": datetime(2026, 5, 25, 16, 0),
        "now_ts": 1_000.0 + 3600,
        "uid": "u1",
    })

    assert proposal is None


def test_topic_followup_propose_allows_different_topic_key(monkeypatch, sandbox):
    from core.scheduler.triggers import memory
    from core.scheduler.last_mentioned import mark_topic_followed_shadow

    monkeypatch.setattr(memory, "_cfg", lambda: {"topic_followup": True})
    monkeypatch.setattr(memory, "_owner_id", lambda: "u1")
    _write_event_log(
        sandbox,
        "u1",
        "2026-05-25",
        """
## 15:00
**用户**：我明天要测试桌宠通道
> turn_id:t1
**叶瑄**：那我陪你看结果。
> emotion:gentle intensity:1 turn_id:t1
---
""",
    )
    mark_topic_followed_shadow("继续改实习材料", now_ts=1_000.0)

    proposal = memory.propose({
        "now_dt": datetime(2026, 5, 25, 16, 0),
        "now_ts": 1_000.0 + 3600,
        "uid": "u1",
    })

    assert proposal.trigger_name == "topic_followup"


def test_no_recent_topic_followup_leaves_spontaneous_recall_available(monkeypatch, sandbox):
    from core.scheduler.triggers import memory, time_based

    monkeypatch.setattr(memory, "_cfg", lambda: {"topic_followup": True})
    monkeypatch.setattr(memory, "_owner_id", lambda: "u1")
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr("core.scheduler.rhythm.silence_ratio", lambda uid, now_ts=None: 1.0)
    _write_event_log(
        sandbox,
        "u1",
        "2026-05-25",
        """
## 15:00
**用户**：嗯。叶瑄。
> turn_id:t1
**叶瑄**：我在。
> emotion:neutral intensity:0 turn_id:t1
---
""",
    )
    ctx = {
        "now_dt": datetime(2026, 5, 25, 16, 0),
        "now_ts": datetime(2026, 5, 25, 16, 0).timestamp(),
        "uid": "u1",
        "episodic_memories": [{"summary": "她说起实习", "strength": 0.8}],
    }

    assert memory.propose(ctx) is None
    assert time_based.propose_spontaneous_recall(ctx).trigger_name == "spontaneous_recall"


def test_sleep_end_morning_mark_blocks_new_morning_greeting_at_gating(monkeypatch, sandbox):
    from core.scheduler import gating, loop
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.triggers import time_based

    monkeypatch.setattr(time_based, "_cfg", lambda: {"morning_greeting": True})
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr(time_based, "_user_talked_today", lambda uid: False)
    monkeypatch.setattr("core.scheduler.rhythm.is_present", lambda now_ts=None: True)
    monkeypatch.setattr(gating, "get_current_state", lambda uid: TriggerState.QUIET)

    loop._mark("morning_greeting")
    proposal = time_based.propose_morning_greeting({
        "now_dt": datetime(2026, 5, 25, 8, 0),
        "now_ts": datetime(2026, 5, 25, 8, 0).timestamp(),
    })

    assert proposal is not None
    assert gating.collect_and_decide("u1", [proposal]) is None


def test_garden_reactive_proposals_use_cached_events():
    from core.scheduler.triggers import garden_daily, garden_water

    bloom = garden_water.propose_garden_bloom({
        "now_ts": 1_000.0,
        "garden_bloom_events": [{"type": "bloom", "name": "雏菊", "received_at": 990.0}],
    })
    ask = garden_daily.propose_garden_handle_ask({
        "now_ts": 1_000.0,
        "garden_daily_events": [
            {"type": "harvest_handle", "handle_action": "ask", "name": "雏菊", "received_at": 990.0}
        ],
    })

    assert bloom.trigger_name == "garden_bloom"
    assert ask.trigger_name == "garden_handle_ask"
    assert 0.30 <= bloom.urgency <= 0.49
    assert 0.30 <= ask.urgency <= 0.49


def test_weather_light_propose_uses_reactive_tier(monkeypatch):
    from core.scheduler.triggers import time_based

    monkeypatch.setattr(time_based, "_cfg", lambda: {"enabled": True})
    now = datetime(2026, 5, 25, 12, 0)
    detail = {
        "temp_c": 22,
        "humidity": 50,
        "precip_mm": 1.0,
        "cloud_cover": 50,
        "wind_kmph": 10,
        "desc": "小雨",
        "is_day": True,
        "uv_index": 3,
        "received_at": now.timestamp(),
    }

    proposal = time_based.propose_weather_alert_light({
        "now_dt": now,
        "now_ts": now.timestamp(),
        "weather_detail": detail,
    })

    assert proposal.trigger_name == "weather_alert"
    assert 0.30 <= proposal.urgency <= 0.49


def test_filler_proposals_use_silence_ratio(monkeypatch):
    from core.scheduler.triggers import time_based

    now = datetime(2026, 5, 25, 15, 0)
    monkeypatch.setattr(time_based, "_cfg", lambda: {"random_message": True})
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr("core.scheduler.rhythm.silence_ratio", lambda uid, now_ts=None: 1.0)
    monkeypatch.setattr(
        "core.memory.episodic_memory._load_memories",
        lambda uid: [{"id": "ep_1", "narrative_summary": "她说起实习", "strength": 0.8}],
    )

    random_message = time_based.propose_random_message({"now_dt": now, "now_ts": now.timestamp()})
    recall = time_based.propose_spontaneous_recall({"now_dt": now, "now_ts": now.timestamp()})

    assert random_message.trigger_name == "random_message"
    assert recall.trigger_name == "spontaneous_recall"
    assert 0.10 <= random_message.urgency <= 0.29
    assert 0.10 <= recall.urgency <= 0.29


def test_spontaneous_recall_empty_content_returns_none(monkeypatch, sandbox):
    from core.scheduler.triggers import time_based

    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    proposal = time_based.propose_spontaneous_recall({
        "now_dt": datetime(2026, 5, 25, 16, 0),
        "now_ts": 1_000.0,
        "uid": "u1",
        "episodic_memories": [{"id": "ep_empty", "summary": "", "strength": 0.9}],
    })

    assert proposal is None


def test_spontaneous_recall_all_recently_recalled_returns_none(monkeypatch, sandbox):
    from core.scheduler.last_mentioned import mark_memory_recalled_shadow
    from core.scheduler.triggers import time_based

    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    memory = {"id": "ep_recent", "narrative_summary": "她说起实习", "strength": 0.9}
    mark_memory_recalled_shadow("episode:ep_recent", now_ts=1_000.0)

    proposal = time_based.propose_spontaneous_recall({
        "now_dt": datetime(2026, 5, 25, 16, 0),
        "now_ts": 1_000.0 + 60,
        "uid": "u1",
        "episodic_memories": [memory],
    })

    assert proposal is None


def test_memory_key_for_recall_is_stable():
    from core.scheduler.triggers import time_based

    memory = {"id": "ep_stable", "narrative_summary": "她说起实习", "strength": 0.9}

    assert time_based.memory_key_for_recall(memory) == "episode:ep_stable"
    assert time_based.memory_key_for_recall(memory) == "episode:ep_stable"
