from datetime import datetime, date


def test_logical_day_before_cutoff_returns_previous_day():
    from core.scheduler.rhythm import logical_day

    assert logical_day(datetime(2026, 5, 24, 2, 0)) == date(2026, 5, 23)


def test_logical_day_after_cutoff_returns_current_day():
    from core.scheduler.rhythm import logical_day

    assert logical_day(datetime(2026, 5, 23, 23, 0)) == date(2026, 5, 23)


def test_is_present_requires_fresh_snapshot_and_low_idle(monkeypatch):
    from core.memory import realtime_state
    from core.scheduler.rhythm import is_present

    monkeypatch.setattr(
        realtime_state,
        "get",
        lambda: {"received_at": 1_000.0, "input": {"idle_seconds": 42}},
    )
    assert is_present(now=1_030.0)

    monkeypatch.setattr(
        realtime_state,
        "get",
        lambda: {"received_at": 800.0, "input": {"idle_seconds": 42}},
    )
    assert not is_present(now=1_000.0)

    monkeypatch.setattr(
        realtime_state,
        "get",
        lambda: {"received_at": 1_000.0, "input": {"idle_seconds": 300}},
    )
    assert not is_present(now=1_000.0)


def test_morning_propose_requires_presence_and_window(monkeypatch):
    from core.scheduler.triggers import time_based

    now = datetime(2026, 5, 23, 8, 30)
    monkeypatch.setattr(time_based, "_cfg", lambda: {"morning_greeting": True})
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr(time_based, "_user_talked_today", lambda uid: False)
    monkeypatch.setattr("core.scheduler.rhythm.is_present", lambda now=None: True)

    proposal = time_based.propose_morning_greeting({"now_dt": now, "now_ts": now.timestamp()})

    assert proposal.trigger_name == "morning_greeting"
    assert 0.50 <= proposal.urgency <= 0.69

    monkeypatch.setattr("core.scheduler.rhythm.is_present", lambda now=None: False)
    assert time_based.propose_morning_greeting({"now_dt": now, "now_ts": now.timestamp()}) is None


def test_night_propose_uses_logical_day_dedupe(monkeypatch):
    from core.scheduler.triggers import time_based

    now = datetime(2026, 5, 24, 1, 30)
    monkeypatch.setattr(time_based, "_cfg", lambda: {"night_reminder": True})
    monkeypatch.setattr("core.scheduler.rhythm.is_present", lambda now=None: True)
    monkeypatch.setattr("core.scheduler.rhythm.triggered_on_logical_day", lambda name, now=None: False)

    proposal = time_based.propose_night_reminder({"now_dt": now, "now_ts": now.timestamp()})

    assert proposal.trigger_name == "night_reminder"
    assert 0.50 <= proposal.urgency <= 0.69

    monkeypatch.setattr("core.scheduler.rhythm.triggered_on_logical_day", lambda name, now=None: True)
    assert time_based.propose_night_reminder({"now_dt": now, "now_ts": now.timestamp()}) is None


def test_quiet_floor_uses_state_machine_last_owner_turn(monkeypatch):
    from core.scheduler import rhythm

    monkeypatch.setattr(
        "core.scheduler.state_machine.snapshot",
        lambda uid: {"last_owner_turn_ts": 1_000.0},
    )

    assert not rhythm.quiet_floor_elapsed("u1", now_ts=1_000.0 + 11 * 60)
    assert rhythm.quiet_floor_elapsed("u1", now_ts=1_000.0 + 12 * 60)


def test_daily_journal_propose_requires_quiet_floor(monkeypatch):
    from core.scheduler.triggers import time_based

    now = datetime(2026, 5, 23, 23, 30)
    monkeypatch.setattr(time_based, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr("core.scheduler.rhythm.triggered_on_logical_day", lambda name, now=None: False)
    monkeypatch.setattr("core.scheduler.rhythm.quiet_floor_elapsed", lambda uid, now_ts=None: False)

    assert time_based.propose_daily_journal({"now_dt": now, "now_ts": now.timestamp()}) is None

    monkeypatch.setattr("core.scheduler.rhythm.quiet_floor_elapsed", lambda uid, now_ts=None: True)
    proposal = time_based.propose_daily_journal({"now_dt": now, "now_ts": now.timestamp()})

    assert proposal.trigger_name == "daily_journal"
    assert 0.50 <= proposal.urgency <= 0.69


def test_diary_reminder_propose_keeps_diary_quiet_and_missing_gates(monkeypatch):
    from core.scheduler.triggers import diary

    now = datetime(2026, 5, 23, 10, 0)
    monkeypatch.setattr(diary, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(diary, "_owner_id", lambda: "u1")
    monkeypatch.setattr("core.scheduler.rhythm.quiet_floor_elapsed", lambda uid, now_ts=None: True)
    monkeypatch.setattr("core.scheduler.rhythm.triggered_on_logical_day", lambda name, now=None: False)
    monkeypatch.setattr("core.tools.diary_reader.yesterday_missing", lambda: True)

    proposal = diary.propose_diary_reminder({"now_dt": now, "now_ts": now.timestamp()})

    assert proposal.trigger_name == "diary_reminder"
    assert 0.50 <= proposal.urgency <= 0.69

    monkeypatch.setattr("core.tools.diary_reader.yesterday_missing", lambda: False)
    assert diary.propose_diary_reminder({"now_dt": now, "now_ts": now.timestamp()}) is None
