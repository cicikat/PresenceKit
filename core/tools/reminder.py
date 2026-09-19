"""Reminder tools backed by the Runtime scheduler capability.

Timed/repeating reminders live in Reality schedules. Free notes without a
due time stay in self; writing "明天提醒" into a self file does not create a
schedule. Frozen uid/char come from the dispatcher, never from model JSON.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from core.agent_runtime.models import CausationRef, TaskPrincipal
from core.agent_runtime.scheduler_capability import (
    ScheduleError,
    cancel_schedule,
    create_schedule,
    get_schedule,
    list_schedules,
    prompt_reminders,
    restore_schedule,
    update_schedule,
)
from core.data_paths import safe_user_id
from core.error_handler import log_error
from core.sensitive_redaction import RedactionError


logger = logging.getLogger(__name__)


_TIME_FMTS = [
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%m-%d %H:%M",
    "%m/%d %H:%M",
    "%H:%M",
]


def dumps(payload: dict[str, Any] | list[dict[str, Any]]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _principal(user_id: str | None, char_id: str | None) -> TaskPrincipal:
    uid = str(user_id or "").strip()
    cid = str(char_id or "").strip()
    if not uid or not cid:
        raise ScheduleError("grant_principal_mismatch")
    try:
        return TaskPrincipal.reality(safe_user_id(uid), safe_user_id(cid))
    except ValueError as exc:
        raise ScheduleError("grant_principal_mismatch") from exc


def _causation(action: str, principal: TaskPrincipal, extra: str = "") -> CausationRef:
    ref = f"reminder:{action}:{principal.uid}:{principal.char_id}"
    if extra:
        ref = f"{ref}:{extra}"
    return CausationRef("tool_request", ref[:256])


def _fail(code: str, **extra: Any) -> dict[str, Any]:
    payload = {"ok": False, "code": code}
    payload.update(extra)
    return payload


def _wrap(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ScheduleError):
        return _fail(exc.code, **exc.extra)
    if isinstance(exc, RedactionError):
        return _fail("sensitive_redaction_failed")
    log_error("reminder", exc)
    return _fail("quota_exhausted", reason="unavailable")


def _parse_time(time_str: str, *, now: datetime | None = None) -> datetime | None:
    """Parse local reminder time. Past HH:MM rolls to tomorrow; month-day without year wraps to next year."""
    clock = now or datetime.now()
    time_str = str(time_str or "").strip()
    for fmt in _TIME_FMTS:
        try:
            dt = datetime.strptime(time_str, fmt)
            if fmt == "%H:%M":
                dt = dt.replace(year=clock.year, month=clock.month, day=clock.day)
                if dt <= clock:
                    dt += timedelta(days=1)
            elif fmt in ("%m-%d %H:%M", "%m/%d %H:%M"):
                dt = dt.replace(year=clock.year)
                if dt <= clock:
                    dt = dt.replace(year=clock.year + 1)
            return dt
        except ValueError:
            continue
    return None


def _parse_repeat(repeat: str | int | None) -> int | None:
    if repeat in (None, "", "none", "once"):
        return None
    if isinstance(repeat, bool):
        raise ScheduleError("path_not_found", extra={"reason": "invalid_repeat"})
    if isinstance(repeat, int):
        seconds = repeat
    else:
        text = str(repeat).strip().lower()
        mapping = {
            "daily": 86400,
            "day": 86400,
            "weekly": 7 * 86400,
            "week": 7 * 86400,
            "hourly": 3600,
            "hour": 3600,
        }
        if text in mapping:
            seconds = mapping[text]
        else:
            try:
                seconds = int(text)
            except ValueError as exc:
                raise ScheduleError("path_not_found", extra={"reason": "invalid_repeat"}) from exc
    if seconds < 60:
        raise ScheduleError("path_not_found", extra={"reason": "invalid_repeat"})
    return seconds


def add_reminder(
    user_id: str,
    content: str,
    remind_at_str: str,
    *,
    char_id: str | None = None,
    repeat: str | int | None = None,
) -> str:
    """Create a timed reminder. Returns a short description or a JSON error payload."""
    dt = _parse_time(remind_at_str)
    if dt is None:
        return dumps(_fail(
            "path_not_found",
            reason="invalid_time",
            message=(
                f"无法解析时间格式：{remind_at_str}，"
                "请使用 HH:MM 或 MM-DD HH:MM 或 YYYY-MM-DD HH:MM"
            ),
        ))
    try:
        principal = _principal(user_id, char_id)
        recurrence = _parse_repeat(repeat)
        receipt = create_schedule(
            principal,
            content=content,
            due_at=dt.timestamp(),
            recurrence_seconds=recurrence,
            idempotency_key=f"reminder:{principal.uid}:{principal.char_id}:{content}:{dt.isoformat()}:{recurrence or 0}",
            causation_ref=_causation("add", principal, dt.isoformat()),
        )
        when = dt.strftime("%Y-%m-%d %H:%M")
        return dumps({
            "ok": True,
            "schedule_id": receipt.get("schedule_id"),
            "revision": receipt.get("revision"),
            "content": receipt.get("content"),
            "due_at": receipt.get("due_at"),
            "remind_at": receipt.get("remind_at"),
            "repeat": receipt.get("repeat"),
            "status": receipt.get("status"),
            "created": receipt.get("created"),
            "message": f"已记住：{receipt.get('content')!r}，将在 {when} 提醒你",
        })
    except Exception as exc:
        payload = _wrap(exc)
        if payload.get("code") in {"grant_principal_mismatch", "revision_conflict"}:
            return dumps(payload)
        logger.error("runtime scheduler unavailable; reminder was not created: %s", exc)
        return dumps({**payload, "message": "提醒暂时无法创建，请稍后再试"})


def get_reminders(user_id: str, *, char_id: str | None = None) -> list:
    """Prompt-layer projection of scheduled reminders for this frozen character."""
    try:
        principal = _principal(user_id, char_id)
    except ScheduleError:
        return []
    try:
        return prompt_reminders(principal)
    except Exception as exc:
        log_error("reminder.get_reminders", exc)
        return []


def list_reminders(
    user_id: str,
    *,
    char_id: str | None = None,
    include_cancelled: bool = False,
) -> dict[str, Any]:
    try:
        principal = _principal(user_id, char_id)
        items = list_schedules(principal, include_cancelled=include_cancelled)
        return {"ok": True, "items": items, "count": len(items)}
    except Exception as exc:
        return _wrap(exc)


def get_reminder(user_id: str, schedule_id: str, *, char_id: str | None = None) -> dict[str, Any]:
    try:
        principal = _principal(user_id, char_id)
        return get_schedule(principal, schedule_id)
    except Exception as exc:
        return _wrap(exc)


def update_reminder(
    user_id: str,
    schedule_id: str,
    expected_revision: int,
    *,
    char_id: str | None = None,
    content: str | None = None,
    remind_at: str | None = None,
    repeat: str | int | None | object = ...,
) -> dict[str, Any]:
    try:
        principal = _principal(user_id, char_id)
        due_at = None
        if remind_at is not None:
            dt = _parse_time(remind_at)
            if dt is None:
                return _fail("path_not_found", reason="invalid_time")
            due_at = dt.timestamp()
        recurrence = ...
        if repeat is not ...:
            recurrence = _parse_repeat(repeat)  # type: ignore[arg-type]
        return update_schedule(
            principal,
            schedule_id,
            expected_revision=int(expected_revision),
            content=content,
            due_at=due_at,
            recurrence_seconds=recurrence,
            causation_ref=_causation("update", principal, schedule_id),
        )
    except Exception as exc:
        return _wrap(exc)


def cancel_reminder(
    user_id: str,
    schedule_id: str,
    expected_revision: int,
    *,
    char_id: str | None = None,
) -> dict[str, Any]:
    try:
        principal = _principal(user_id, char_id)
        return cancel_schedule(
            principal, schedule_id, expected_revision=int(expected_revision),
        )
    except Exception as exc:
        return _wrap(exc)


def restore_reminder(
    user_id: str,
    schedule_id: str,
    revision: int,
    *,
    char_id: str | None = None,
) -> dict[str, Any]:
    try:
        principal = _principal(user_id, char_id)
        return restore_schedule(
            principal,
            schedule_id,
            revision=int(revision),
            causation_ref=_causation("restore", principal, f"{schedule_id}:{revision}"),
        )
    except Exception as exc:
        return _wrap(exc)


def get_due_reminders(user_id: str, *, char_id: str | None = None) -> list:
    """Shadow proposer projection. Live delivery uses scheduler_capability.due_across_owner."""
    try:
        from core.agent_runtime.scheduler_capability import due_across_owner
        uid = safe_user_id(user_id)
        due = due_across_owner(uid)
        if char_id:
            due = [item for item in due if item.get("char_id") == char_id]
        items = []
        for item in due:
            items.append({
                "id": item.get("schedule_id"),
                "schedule_id": item.get("schedule_id"),
                "content": item.get("content"),
                "remind_at": datetime.fromtimestamp(float(item.get("due_at") or 0)).strftime("%Y-%m-%d %H:%M"),
                "revision": item.get("revision"),
                "status": item.get("status"),
                "char_id": item.get("char_id"),
            })
        return items
    except Exception as exc:
        log_error("reminder.get_due_reminders", exc)
        return []
