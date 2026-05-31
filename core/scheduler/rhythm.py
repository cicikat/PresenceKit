"""Shared helpers for scheduler rhythm proposals."""

from __future__ import annotations

import time
from datetime import date, datetime, time as dt_time, timedelta

# TODO(policy.yaml): move logical day cutoff to scheduler policy.
LOGICAL_DAY_CUTOFF_HOUR = 5

# TODO(policy.yaml): move presence thresholds to scheduler policy.
PRESENCE_FRESHNESS_SECONDS = 90
PRESENCE_IDLE_THRESHOLD_SECONDS = 300

# TODO(policy.yaml): move nightly rhythm window end to scheduler policy.
NIGHT_WINDOW_END_HOUR = 2

# TODO(policy.yaml): move diary quiet floor to scheduler policy.
DIARY_MIN_QUIET_MINUTES = 12

# TODO(policy.yaml): move filler silence threshold to scheduler policy.
FILLER_SILENCE_THRESHOLD_SECONDS = 30 * 60


def logical_day(now: datetime | None = None, cutoff_hour: int = LOGICAL_DAY_CUTOFF_HOUR) -> date:
    """Return the scheduler's logical day; pre-cutoff early morning belongs to yesterday."""
    current = now or datetime.now()
    day = current.date()
    if current.hour < cutoff_hour:
        return day - timedelta(days=1)
    return day


def is_present(
    now: float | None = None,
    freshness_sec: int = PRESENCE_FRESHNESS_SECONDS,
    idle_threshold_sec: int = PRESENCE_IDLE_THRESHOLD_SECONDS,
) -> bool:
    """Return whether the latest realtime sensor snapshot says she is currently present."""
    from core.memory import realtime_state

    snap = realtime_state.get()
    if snap is None:
        return False
    current = time.time() if now is None else float(now)
    if current - float(snap.get("received_at") or 0) > freshness_sec:
        return False
    idle = snap.get("input", {}).get("idle_seconds", idle_threshold_sec + 1)
    return int(idle) < idle_threshold_sec


def triggered_on_logical_day(trigger_name: str, now: datetime | None = None) -> bool:
    """Read scheduler cooldown marks and compare them by logical day without writing state."""
    from core.scheduler.loop import _last_trigger

    last = float(_last_trigger.get(trigger_name, 0) or 0)
    if last <= 0:
        return False
    current = now or datetime.now()
    last_dt = datetime.fromtimestamp(last)
    return logical_day(last_dt) == logical_day(current)


def quiet_floor_elapsed(
    uid: str,
    now_ts: float | None = None,
    min_minutes: int = DIARY_MIN_QUIET_MINUTES,
) -> bool:
    """Return whether enough time has passed since her last owner turn."""
    from core.scheduler.state_machine import snapshot

    state = snapshot(uid)
    last_owner_turn = float(state.get("last_owner_turn_ts") or 0)
    if last_owner_turn <= 0:
        return True
    current = time.time() if now_ts is None else float(now_ts)
    return current - last_owner_turn >= min_minutes * 60


def silence_ratio(
    uid: str,
    now_ts: float | None = None,
    threshold_seconds: int = FILLER_SILENCE_THRESHOLD_SECONDS,
) -> float:
    from core.scheduler.state_machine import snapshot

    state = snapshot(uid)
    last_owner_turn = float(state.get("last_owner_turn_ts") or 0)
    if last_owner_turn <= 0:
        return 1.0
    current = time.time() if now_ts is None else float(now_ts)
    return max(0.0, min(1.0, (current - last_owner_turn) / threshold_seconds))


def daytime_window_ratio(now: datetime, start_hour: int, end_hour: int) -> float:
    start = datetime.combine(now.date(), dt_time(hour=start_hour))
    end = datetime.combine(now.date(), dt_time(hour=end_hour))
    return _ratio_between(now, start, end)


def night_window_ratio(now: datetime, start_hour: int = 23, end_hour: int = NIGHT_WINDOW_END_HOUR) -> float:
    start = datetime.combine(logical_day(now), dt_time(hour=start_hour))
    end = datetime.combine(logical_day(now) + timedelta(days=1), dt_time(hour=end_hour))
    return _ratio_between(now, start, end)


def in_night_window(now: datetime, start_hour: int = 23, end_hour: int = NIGHT_WINDOW_END_HOUR) -> bool:
    start = datetime.combine(logical_day(now), dt_time(hour=start_hour))
    end = datetime.combine(logical_day(now) + timedelta(days=1), dt_time(hour=end_hour))
    return start <= now < end


def is_quiet_sleep_time(now: datetime | None = None) -> bool:
    """Return True when local time is in the likely-sleep window (23:00–08:00).

    Used as a hard gate: when True + idle≥300s, presence events are suppressed.
    Intentionally simple — no LLM, no state, just clock hour.
    """
    current = now or datetime.now()
    h = current.hour
    return h >= 23 or h < 8


def _ratio_between(now: datetime, start: datetime, end: datetime) -> float:
    total = (end - start).total_seconds()
    if total <= 0:
        return 1.0
    elapsed = (now - start).total_seconds()
    return max(0.0, min(1.0, elapsed / total))
