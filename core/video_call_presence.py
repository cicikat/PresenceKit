"""In-call proactivity scope (video-call work order F).

While a camera session is active, the camera-sourced autonomy job may use a
call-scoped set of limits instead of the global ones.  Everything here is
**off by default** and never touches the global defaults:

    video_call_presence:
      enabled: false
      max_talks_per_call: 3          # the anti-harassment cap; cannot be disabled
      min_gap_seconds: 180           # minimum gap between proactive talks in one call
      silence_seconds: 30            # owner must be quiet this long (global rule: 120)
      camera_signal_interval_seconds: 60

What this relaxes (only for a camera job, only while the call is live):
  * the ``camera_silent`` admission window and the in-loop "user became active" cancel
  * the autonomy min-interval for the camera source
  * the global proactive gap / daily cap, replaced by the per-call cap + gap below
What it does not relax: DND, dream guard, unanswered-talk hard stop, circuit breaker,
and ledger *accounting* (``record_send`` still counts every in-call talk).
"""
from __future__ import annotations

import time
from typing import Any

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "max_talks_per_call": 3,
    "min_gap_seconds": 180,
    "silence_seconds": 30,
    "camera_signal_interval_seconds": 60,
}
GLOBAL_SILENCE_SECONDS = 120

_blocks: dict[str, int] = {}
_talks_total = 0


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    block = payload if isinstance(payload, dict) else {}
    out = dict(DEFAULTS)
    if "enabled" in block:
        if not isinstance(block["enabled"], bool):
            raise ValueError("enabled 必须是布尔值")
        out["enabled"] = block["enabled"]
    ranges = {"max_talks_per_call": (1, 10), "min_gap_seconds": (30, 3600),
              "silence_seconds": (5, 120), "camera_signal_interval_seconds": (15, 600)}
    for key, (low, high) in ranges.items():
        if key in block:
            value = block[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{key} 必须是 {low}–{high} 的整数")
            out[key] = value
    return out


def settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if config is None:
        from core.config_loader import get_config
        config = get_config() or {}
    try:
        return validate(config.get("video_call_presence") or {})
    except ValueError:
        return dict(DEFAULTS)


def active_session(uid: str, char_id: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The live camera session when call-scoped limits apply, else None."""
    if not settings(config)["enabled"]:
        return None
    from core.video_call import camera_session
    return camera_session(uid, char_id)


def job_is_call_scoped(job: Any) -> bool:
    signals = [s for s in (getattr(job, "opportunity", None) or {}).get("signals") or [] if isinstance(s, dict)]
    if not any(s.get("source") == "video_call_camera" for s in signals):
        return False
    return active_session(job.uid, job.char_id) is not None


def camera_signal_interval(session: dict[str, Any] | None) -> float:
    from core.video_call import CAMERA_SIGNAL_INTERVAL_SECONDS
    if session is None or not settings()["enabled"]:
        return CAMERA_SIGNAL_INTERVAL_SECONDS
    return float(settings()["camera_signal_interval_seconds"])


def record_block(reason: str) -> None:
    """Count which gate stopped an in-call camera attempt (observability only)."""
    _blocks[reason] = _blocks.get(reason, 0) + 1


def talk_check(uid: str, char_id: str) -> tuple[str, str]:
    """Per-call replacement for the global gap/daily check: returns (mode, reason)."""
    session = active_session(uid, char_id)
    if session is None:
        return "allow", "ok"
    cfg = settings()
    if int(session.get("presence_talks") or 0) >= cfg["max_talks_per_call"]:
        record_block("call_talk_cap")
        return "hard", "suppressed_daily_budget"
    last = float(session.get("presence_last_talk_at") or 0)
    if last and time.time() - last < cfg["min_gap_seconds"]:
        record_block("call_gap_not_elapsed")
        return "soft", "call_gap_not_elapsed"
    return "allow", "ok"


def record_talk(uid: str, char_id: str) -> None:
    global _talks_total
    from core.video_call import camera_session
    session = camera_session(uid, char_id)
    if session is None:
        return
    session["presence_talks"] = int(session.get("presence_talks") or 0) + 1
    session["presence_last_talk_at"] = time.time()
    _talks_total += 1


def silence_window(uid: str, char_id: str) -> int:
    return settings()["silence_seconds"] if active_session(uid, char_id) else GLOBAL_SILENCE_SECONDS


def snapshot(config: dict[str, Any] | None = None) -> dict[str, Any]:
    from core.video_call import _camera_sessions, camera_status
    cfg = settings(config)
    now = time.time()
    return {
        "settings": cfg,
        "scoped_now": camera_status()["active_sessions"] if cfg["enabled"] else 0,
        "sessions": [{"talks": int(row.get("presence_talks") or 0),
                      "last_talk_seconds_ago": (round(now - row["presence_last_talk_at"])
                                               if row.get("presence_last_talk_at") else None)}
                     for row in _camera_sessions.values()],
        "talks_total": _talks_total,
        "gate_blocks": dict(_blocks),
    }


def reset_for_tests() -> None:
    global _talks_total
    _blocks.clear()
    _talks_total = 0
