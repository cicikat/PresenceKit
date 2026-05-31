"""
P0 sleep-guard tests.

Verifies:
1. Deep night + idle>=300 → sensor_events.tick() returns no presence events
2. Deep night → get_last_seen_text() returns "" (never "N小时前")
3. Daytime: active/idle/away behaviour unchanged
4. User sending a message is not gated by sleep guard
"""
import time
from datetime import datetime
from unittest.mock import patch


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_snap(idle: int, received_delta: float = 0.0) -> dict:
    """Build a minimal realtime_state snapshot."""
    return {
        "input":          {"idle_seconds": idle, "keystrokes": 0},
        "focus":          {"app": "explorer.exe", "title_hint": "", "switch_count": 0},
        "screen":         {},
        "window_seconds": 30,
        "received_at":    time.time() + received_delta,
    }


def _reset_sensor_events():
    """Reset all module-level state in sensor_events between tests."""
    import core.scheduler.sensor_events as se
    se._cooldowns.clear()
    se._last_presence = None
    se._last_presence_changed_at = None
    se._last_app = None
    se._last_app_category = None
    se._last_chat_at = None
    se._last_proactive_at = None
    se._focus_window_in_app_started_at = None
    se._focus_window_in_app_name = None
    se._recent_switch_events = []
    se._last_presence_was_sleep_guarded = False


# ─── 1. Sleep guard suppresses PRESENCE_LEFT / PRESENCE_RETURNED ─────────────

def test_sleep_guard_no_presence_left(monkeypatch):
    """Deep night + idle>=300: PRESENCE_LEFT must not be generated."""
    import core.scheduler.sensor_events as se
    import core.memory.realtime_state as rs

    _reset_sensor_events()
    snap = _make_snap(idle=600)
    monkeypatch.setattr(rs, "get", lambda: snap)
    monkeypatch.setattr(rs, "get_presence", lambda: "away")
    monkeypatch.setattr(rs, "get_continuous_at_desk_seconds", lambda: 0)

    # Seed state: was active before sleep
    se._last_presence = "active"
    se._last_presence_changed_at = time.time() - 20 * 60  # active for 20 min

    sleep_hour = datetime(2026, 1, 15, 2, 30)  # 02:30 — inside sleep window
    with patch("core.scheduler.sensor_events.is_quiet_sleep_time", return_value=True):
        events = se.tick()

    types = [e["type"] for e in events]
    assert se.PRESENCE_LEFT not in types
    assert se.PRESENCE_RETURNED not in types
    assert events == []


def test_sleep_guard_no_presence_returned_on_wakeup(monkeypatch):
    """After sleep guard, first active tick must NOT generate PRESENCE_RETURNED."""
    import core.scheduler.sensor_events as se
    import core.memory.realtime_state as rs

    _reset_sensor_events()

    # Simulate: guard was active, state frozen at "away"
    se._last_presence = "away"
    se._last_presence_changed_at = time.time() - 7 * 3600  # 7h ago
    se._last_presence_was_sleep_guarded = True

    # Now it's 08:30 — outside sleep window
    snap_awake = _make_snap(idle=5)
    monkeypatch.setattr(rs, "get", lambda: snap_awake)
    monkeypatch.setattr(rs, "get_presence", lambda: "active")
    monkeypatch.setattr(rs, "get_continuous_at_desk_seconds", lambda: 0)

    with patch("core.scheduler.sensor_events.is_quiet_sleep_time", return_value=False):
        events = se.tick()

    types = [e["type"] for e in events]
    assert se.PRESENCE_RETURNED not in types
    assert not se._last_presence_was_sleep_guarded  # flag cleared after transition


# ─── 2. get_last_seen_text() returns "" during sleep window ──────────────────

def test_get_last_seen_text_silent_during_sleep(monkeypatch):
    """During 23:00–08:00, get_last_seen_text must return '' regardless of age."""
    from core import presence

    monkeypatch.setattr("core.presence.is_quiet_sleep_time", lambda: True)
    # Patch file read to return something that would normally produce "8小时前"
    monkeypatch.setattr(
        "core.presence.get_paths",
        lambda: type("P", (), {"presence": lambda self: type("F", (), {
            "exists": lambda self: True,
            "read_text": lambda self, **kw: '{"u1": {"last_message_at": ' + str(time.time() - 8 * 3600) + '}}',
        })()})(),
    )

    result = presence.get_last_seen_text("u1")
    assert result == ""


def test_get_last_seen_text_returns_text_during_daytime(monkeypatch):
    """During daytime, get_last_seen_text returns a non-empty string for old messages."""
    from core import presence

    monkeypatch.setattr("core.presence.is_quiet_sleep_time", lambda: False)
    last_ts = time.time() - 9 * 3600  # 9 hours ago — should yield "9小时前"
    monkeypatch.setattr(
        "core.presence.get_paths",
        lambda: type("P", (), {"presence": lambda self: type("F", (), {
            "exists": lambda self: True,
            "read_text": lambda self, **kw: f'{{"u1": {{"last_message_at": {last_ts}}}}}',
        })()})(),
    )

    result = presence.get_last_seen_text("u1")
    assert result != ""
    assert "小时前" in result


# ─── 3. Daytime: active / idle / away behaviour unchanged ────────────────────

def test_daytime_presence_returned_fires_normally(monkeypatch):
    """Outside sleep window, a normal away→active transition fires PRESENCE_RETURNED."""
    import core.scheduler.sensor_events as se
    import core.memory.realtime_state as rs

    _reset_sensor_events()
    se._last_presence = "away"
    se._last_presence_changed_at = time.time() - 10 * 60  # away for 10 min
    se._last_presence_was_sleep_guarded = False

    snap = _make_snap(idle=5)
    monkeypatch.setattr(rs, "get", lambda: snap)
    monkeypatch.setattr(rs, "get_presence", lambda: "active")
    monkeypatch.setattr(rs, "get_continuous_at_desk_seconds", lambda: 0)

    with patch("core.scheduler.sensor_events.is_quiet_sleep_time", return_value=False):
        events = se.tick()

    types = [e["type"] for e in events]
    assert se.PRESENCE_RETURNED in types


def test_daytime_presence_left_fires_normally(monkeypatch):
    """Outside sleep window, active→away after ≥10 min fires PRESENCE_LEFT."""
    import core.scheduler.sensor_events as se
    import core.memory.realtime_state as rs

    _reset_sensor_events()
    se._last_presence = "active"
    se._last_presence_changed_at = time.time() - 15 * 60  # active for 15 min

    snap = _make_snap(idle=600)
    monkeypatch.setattr(rs, "get", lambda: snap)
    monkeypatch.setattr(rs, "get_presence", lambda: "away")
    monkeypatch.setattr(rs, "get_continuous_at_desk_seconds", lambda: 0)

    with patch("core.scheduler.sensor_events.is_quiet_sleep_time", return_value=False):
        events = se.tick()

    types = [e["type"] for e in events]
    assert se.PRESENCE_LEFT in types


# ─── 4. User sending a message is not gated ──────────────────────────────────

def test_notify_chat_happened_always_works():
    """notify_chat_happened() must succeed regardless of time of day."""
    import core.scheduler.sensor_events as se
    _reset_sensor_events()
    before = se._last_chat_at
    se.notify_chat_happened()
    assert se._last_chat_at is not None
    assert se._last_chat_at > (before or 0)


# ─── 5. is_quiet_sleep_time boundary checks ──────────────────────────────────

def test_is_quiet_sleep_time_boundaries():
    from core.scheduler.rhythm import is_quiet_sleep_time

    assert is_quiet_sleep_time(datetime(2026, 1, 15, 0, 0))   # midnight
    assert is_quiet_sleep_time(datetime(2026, 1, 15, 3, 30))  # 03:30
    assert is_quiet_sleep_time(datetime(2026, 1, 15, 7, 59))  # 07:59
    assert not is_quiet_sleep_time(datetime(2026, 1, 15, 8, 0))   # 08:00 — boundary out
    assert not is_quiet_sleep_time(datetime(2026, 1, 15, 12, 0))  # noon
    assert not is_quiet_sleep_time(datetime(2026, 1, 15, 22, 59)) # 22:59
    assert is_quiet_sleep_time(datetime(2026, 1, 15, 23, 0))  # 23:00 — in
    assert is_quiet_sleep_time(datetime(2026, 1, 15, 23, 59)) # 23:59
