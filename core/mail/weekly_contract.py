"""Durable weekly delivery contract for character letters."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from core.safe_write import safe_write_json
from core.sandbox import get_paths

_LOCK = threading.Lock()
_LEASE_SECONDS = 15 * 60


def week_key(uid: str, char_id: str, now: float | None = None) -> str:
    year, week, _ = datetime.fromtimestamp(now or time.time()).isocalendar()
    return f"{uid}:{char_id}:{year}-W{week:02d}"


def _load() -> dict:
    path = get_paths().letter_weekly_state()
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save(value: dict) -> None:
    safe_write_json(get_paths().letter_weekly_state(), value)


def snapshot(uid: str, char_id: str, *, now: float | None = None) -> dict:
    key = week_key(uid, char_id, now)
    with _LOCK:
        return dict(_load().get(key) or {"status": "unattempted", "retry_count": 0, "generation_count": 0})


def is_due(uid: str, char_id: str, *, now: float | None = None) -> bool:
    now = now or time.time()
    state = snapshot(uid, char_id, now=now)
    return state.get("status") not in {"sent", "outcome_unknown"} and float(state.get("next_attempt_ts") or 0) <= now and float(state.get("lease_until") or 0) <= now


def claim(uid: str, char_id: str, *, now: float | None = None) -> dict | None:
    now = now or time.time()
    key = week_key(uid, char_id, now)
    with _LOCK:
        rows = _load()
        state = dict(rows.get(key) or {"status": "unattempted", "retry_count": 0, "generation_count": 0})
        if state.get("status") in {"sent", "outcome_unknown"} or float(state.get("next_attempt_ts") or 0) > now or float(state.get("lease_until") or 0) > now:
            return None
        state["lease_until"] = now + _LEASE_SECONDS
        state["last_attempt_ts"] = now
        rows[key] = state
        _save(rows)
        return state


def finish(uid: str, char_id: str, *, sent: bool, failure_code: str = "", message_id: str = "", now: float | None = None, max_generation_attempts: int = 3, smtp_max_retries: int = 3, retry_base_seconds: int = 900) -> dict:
    now = now or time.time()
    key = week_key(uid, char_id, now)
    with _LOCK:
        rows = _load()
        state = dict(rows.get(key) or {})
        state.pop("lease_until", None)
        if sent:
            state.update(status="sent", sent_at=now, provider_message_id=str(message_id)[:255], failure_code="")
        else:
            state["status"] = "failed"
            state["failure_code"] = failure_code
            if failure_code == "outcome_unknown":
                state["status"] = "outcome_unknown"
            elif failure_code == "quality_rejected":
                state["generation_count"] = int(state.get("generation_count") or 0) + 1
                if state["generation_count"] >= max_generation_attempts:
                    state["next_attempt_ts"] = _next_week_ts(now)
                else:
                    state["next_attempt_ts"] = now + 6 * 3600
            elif failure_code.startswith(("smtp_", "gmail_")):
                state["retry_count"] = int(state.get("retry_count") or 0) + 1
                if state["retry_count"] >= smtp_max_retries:
                    state["next_attempt_ts"] = _next_week_ts(now)
                else:
                    state["next_attempt_ts"] = now + retry_base_seconds * (2 ** (state["retry_count"] - 1))
            else:
                state["next_attempt_ts"] = now + 6 * 3600
        rows[key] = state
        _save(rows)
        return state


def _next_week_ts(now: float) -> float:
    dt = datetime.fromtimestamp(now)
    return (dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + (7 - dt.weekday()) * 86400)
