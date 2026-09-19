"""Reality-only durable scheduling capability (Brief 235 / work order 256 E).

The schedule store is the reminder authority. Task Manager owns leases and
runnable lifecycle; a completed lease is never revived. Recurrence starts a
new task. Character tools see schedule_id, never delete by text match.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.agent_runtime import task_manager
from core.agent_runtime.models import CausationRef, TaskPrincipal, TERMINAL_STATUSES
from core.data_paths import safe_user_id
from core.safe_write import safe_write_json
from core.sandbox import get_paths
from core.sensitive_redaction import (
    REDACTION_VERSION,
    RedactionError,
    observability_snapshot as redaction_observability,
    redact_for_export,
)


SCHEMA = "agent-runtime-schedule-store.v1"
OBSERVABILITY_CAPABILITY = "character-reminders.v1"
MAX_HISTORY = 20
MAX_SCHEDULES = 200
MAX_CONTENT_CHARS = 2000
MIN_RECURRENCE_SECONDS = 60
DEFAULT_TTL_SECONDS = 365 * 86400
STATUS_SCHEDULED = "scheduled"
STATUS_IN_FLIGHT = "in_flight"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"
ACTIVE_STATUSES = frozenset({STATUS_SCHEDULED, STATUS_IN_FLIGHT})
RESTORABLE_STATUSES = frozenset({STATUS_CANCELLED})

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class ScheduleError(ValueError):
    def __init__(self, code: str, *, extra: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.extra = extra or {}


def _now(value: float | None) -> float:
    return time.time() if value is None else float(value)


def _lock_for(uid: str, char_id: str) -> threading.RLock:
    key = f"{uid}:{char_id}"
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _require_principal(principal: TaskPrincipal) -> TaskPrincipal:
    if not isinstance(principal, TaskPrincipal) or principal.realm != "reality":
        raise ScheduleError("realm_forbidden")
    try:
        safe_user_id(principal.uid)
        safe_user_id(principal.char_id)
    except ValueError as exc:
        raise ScheduleError("grant_principal_mismatch") from exc
    return principal


def _path(principal: TaskPrincipal) -> Path:
    _require_principal(principal)
    path = get_paths().agent_runtime_schedule_state(principal.uid, char_id=principal.char_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _empty_store() -> dict[str, Any]:
    return {"schema": SCHEMA, "schedules": []}


def _migrate_row(raw: dict[str, Any]) -> dict[str, Any]:
    canceled = bool(raw.get("canceled"))
    status = str(raw.get("status") or "")
    if status not in {STATUS_SCHEDULED, STATUS_IN_FLIGHT, STATUS_CANCELLED, STATUS_COMPLETED}:
        if canceled:
            status = STATUS_CANCELLED
        elif raw.get("completed"):
            status = STATUS_COMPLETED
        else:
            status = STATUS_SCHEDULED
    if canceled and status == STATUS_CANCELLED and raw.get("in_flight_occurrence"):
        status = STATUS_IN_FLIGHT
    recurrence = raw.get("recurrence_seconds")
    repeat = raw.get("repeat")
    if not isinstance(repeat, dict):
        seconds = int(recurrence) if isinstance(recurrence, int) and not isinstance(recurrence, bool) else None
        repeat = {"kind": "interval", "seconds": seconds} if seconds else {"kind": "none", "seconds": None}
    history = raw.get("history")
    if not isinstance(history, list):
        history = []
    return {
        "schedule_id": str(raw.get("schedule_id") or uuid.uuid4().hex),
        "task_id": str(raw.get("task_id") or ""),
        "content": str(raw.get("content") or ""),
        "due_at": float(raw.get("due_at") or 0),
        "repeat": {
            "kind": "interval" if repeat.get("kind") == "interval" and repeat.get("seconds") else "none",
            "seconds": int(repeat["seconds"]) if repeat.get("kind") == "interval" and repeat.get("seconds") else None,
        },
        # A cancel during send keeps in_flight until finish_delivery; do not
        # collapse that row to cancelled on reload or sent cannot be recorded.
        "status": (
            STATUS_CANCELLED
            if canceled and status not in {STATUS_COMPLETED, STATUS_IN_FLIGHT}
            else status
        ),
        "revision": int(raw.get("revision") or 1),
        "canceled": canceled or status == STATUS_CANCELLED,
        "in_flight_revision": raw.get("in_flight_revision"),
        "in_flight_due_at": raw.get("in_flight_due_at"),
        "in_flight_occurrence": raw.get("in_flight_occurrence"),
        "delivered_count": int(raw.get("delivered_count") or 0),
        "delivery_attempts": int(raw.get("delivery_attempts") or 0),
        "created_at": float(raw.get("created_at") or 0),
        "updated_at": float(raw.get("updated_at") or 0),
        "history": history[-MAX_HISTORY:],
        "legacy_id": str(raw["legacy_id"]) if raw.get("legacy_id") else "",
    }


def _load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return _empty_store()
    if isinstance(raw, list):
        return {"schema": SCHEMA, "schedules": [_migrate_row(item) for item in raw if isinstance(item, dict)]}
    if not isinstance(raw, dict):
        return _empty_store()
    rows = raw.get("schedules")
    if not isinstance(rows, list):
        return _empty_store()
    return {"schema": SCHEMA, "schedules": [_migrate_row(item) for item in rows if isinstance(item, dict)]}


def _save_store(path: Path, store: dict[str, Any]) -> None:
    if not safe_write_json(path, store, keep_bak=False):
        raise ScheduleError("quota_exhausted", extra={"reason": "persist_failed"})


def _redact_content(content: str) -> str:
    try:
        return redact_for_export(content)
    except RedactionError as exc:
        raise ScheduleError("sensitive_redaction_failed") from exc


def _due_text(due_at: float) -> str:
    return datetime.fromtimestamp(float(due_at)).strftime("%Y-%m-%d %H:%M")


def _normalize_repeat(recurrence_seconds: int | None) -> dict[str, Any]:
    if recurrence_seconds is None:
        return {"kind": "none", "seconds": None}
    if not isinstance(recurrence_seconds, int) or isinstance(recurrence_seconds, bool) or recurrence_seconds < MIN_RECURRENCE_SECONDS:
        raise ScheduleError("path_not_found", extra={"reason": "invalid_recurrence"})
    return {"kind": "interval", "seconds": recurrence_seconds}


def _validate_content(content: str) -> str:
    if not isinstance(content, str) or not content.strip():
        raise ScheduleError("path_not_found", extra={"reason": "invalid_content"})
    text = content.strip()
    if len(text) > MAX_CONTENT_CHARS:
        raise ScheduleError("quota_exhausted", extra={"limit": MAX_CONTENT_CHARS})
    _redact_content(text)
    return text


def _history_entry(row: dict[str, Any], *, action: str, now: float) -> dict[str, Any]:
    return {
        "revision": int(row.get("revision") or 0),
        "action": action,
        "at": now,
        "content": row.get("content"),
        "due_at": row.get("due_at"),
        "repeat": dict(row.get("repeat") or {}),
        "status": row.get("status"),
        "task_id": row.get("task_id") or "",
    }


def _push_history(row: dict[str, Any], *, action: str, now: float) -> None:
    history = list(row.get("history") or [])
    history.append(_history_entry(row, action=action, now=now))
    row["history"] = history[-MAX_HISTORY:]


def _character_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "schedule_id": row["schedule_id"],
        "revision": int(row["revision"]),
        "content": _redact_content(str(row.get("content") or "")),
        "due_at": float(row["due_at"]),
        "remind_at": _due_text(row["due_at"]),
        "repeat": dict(row.get("repeat") or {"kind": "none", "seconds": None}),
        "status": row["status"],
        "delivered_count": int(row.get("delivered_count") or 0),
        "created_at": float(row.get("created_at") or 0),
        "updated_at": float(row.get("updated_at") or 0),
    }


def _obs_item(row: dict[str, Any]) -> dict[str, Any]:
    repeat = row.get("repeat") or {}
    return {
        "schedule_id": row.get("schedule_id"),
        "revision": int(row.get("revision") or 0),
        "status": row.get("status"),
        "due_at": float(row.get("due_at") or 0),
        "repeat_kind": repeat.get("kind") or "none",
        "delivered_count": int(row.get("delivered_count") or 0),
        "updated_at": float(row.get("updated_at") or 0),
        "has_task": bool(row.get("task_id")),
    }


def _find(rows: list[dict[str, Any]], schedule_id: str) -> dict[str, Any]:
    row = next((item for item in rows if item.get("schedule_id") == schedule_id), None)
    if row is None:
        raise ScheduleError("path_not_found")
    return row


def _require_revision(row: dict[str, Any], expected_revision: int) -> None:
    if int(row.get("revision") or 0) != int(expected_revision):
        raise ScheduleError("revision_conflict", extra={"revision": int(row.get("revision") or 0)})


def _cancel_task_quiet(principal: TaskPrincipal, task_id: str) -> None:
    if not task_id:
        return
    try:
        task_manager.request_cancel(principal, task_id, reason_code="schedule_canceled")
    except task_manager.TaskManagerError:
        return


def _task_is_runnable(principal: TaskPrincipal, task_id: str) -> bool:
    if not task_id:
        return False
    try:
        receipt = task_manager.get_task(principal, task_id)
    except task_manager.TaskManagerError:
        return False
    return str(receipt.get("status") or "") not in TERMINAL_STATUSES


def _new_task(
    principal: TaskPrincipal,
    *,
    idempotency_key: str,
    ttl_seconds: int,
    causation_ref: CausationRef | None,
    now: float,
) -> tuple[str, bool]:
    receipt, created = task_manager.create_task(
        principal,
        capability="scheduler",
        source="user_schedule",
        idempotency_key=idempotency_key,
        ttl_seconds=ttl_seconds,
        causation_ref=causation_ref,
        now=now,
        request_summary={"kind": "reminder"},
    )
    return str(receipt["task_id"]), created


def _ensure_runnable_task(
    principal: TaskPrincipal,
    row: dict[str, Any],
    *,
    causation_ref: CausationRef | None,
    now: float,
) -> None:
    if row.get("status") not in ACTIVE_STATUSES:
        return
    if _task_is_runnable(principal, str(row.get("task_id") or "")):
        return
    task_id, _created = _new_task(
        principal,
        idempotency_key=f"reminder-life:{row['schedule_id']}:{int(row['revision'])}:{int(now * 1000)}",
        ttl_seconds=DEFAULT_TTL_SECONDS,
        causation_ref=causation_ref,
        now=now,
    )
    row["task_id"] = task_id


def _complete_current_task(principal: TaskPrincipal, row: dict[str, Any], *, now: float, outcome: str) -> None:
    task_id = str(row.get("task_id") or "")
    if not task_id:
        return
    try:
        lease = task_manager.claim_next(principal, task_id=task_id, now=now)
        if lease:
            task_manager.complete_task(
                principal, lease, result_metadata={"outcome_code": outcome}, now=now,
            )
            return
        receipt = task_manager.get_task(principal, task_id, now=now)
        if str(receipt.get("status") or "") not in TERMINAL_STATUSES:
            _cancel_task_quiet(principal, task_id)
    except task_manager.TaskManagerError:
        return


def create_schedule(
    principal: TaskPrincipal,
    *,
    content: str,
    due_at: float,
    recurrence_seconds: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    idempotency_key: str | None = None,
    causation_ref: CausationRef | None = None,
    legacy_id: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    principal = _require_principal(principal)
    text = _validate_content(content)
    repeat = _normalize_repeat(recurrence_seconds)
    timestamp = _now(now)
    due = float(due_at)
    if due <= 0:
        raise ScheduleError("path_not_found", extra={"reason": "invalid_due"})
    if ttl_seconds < 1:
        ttl_seconds = DEFAULT_TTL_SECONDS
    ttl_seconds = min(int(ttl_seconds), task_manager.MAX_TTL_SECONDS)
    idem = idempotency_key or uuid.uuid4().hex
    causation = causation_ref or CausationRef("tool_request", f"reminder:create:{principal.uid}:{principal.char_id}:{idem}")
    path = _path(principal)
    with _lock_for(principal.uid, principal.char_id):
        store = _load_store(path)
        rows = store["schedules"]
        if legacy_id:
            existing_legacy = next((item for item in rows if item.get("legacy_id") == legacy_id), None)
            if existing_legacy:
                return {**_character_projection(existing_legacy), "created": False}
        try:
            task_id, created = _new_task(
                principal, idempotency_key=idem, ttl_seconds=ttl_seconds,
                causation_ref=causation, now=timestamp,
            )
        except task_manager.TaskManagerError as exc:
            raise ScheduleError("quota_exhausted", extra={"reason": exc.code}) from exc
        existing = next((item for item in rows if item.get("task_id") == task_id), None)
        if existing:
            return {**_character_projection(existing), "created": False}
        live = [item for item in rows if item.get("status") in ACTIVE_STATUSES]
        if len(live) >= MAX_SCHEDULES:
            _cancel_task_quiet(principal, task_id)
            raise ScheduleError("quota_exhausted", extra={"limit": MAX_SCHEDULES})
        row = {
            "schedule_id": uuid.uuid4().hex,
            "task_id": task_id,
            "content": text,
            "due_at": due,
            "repeat": repeat,
            "status": STATUS_SCHEDULED,
            "revision": 1,
            "canceled": False,
            "in_flight_revision": None,
            "in_flight_due_at": None,
            "in_flight_occurrence": None,
            "delivered_count": 0,
            "delivery_attempts": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
            "history": [],
            "legacy_id": str(legacy_id or ""),
        }
        _push_history(row, action="create", now=timestamp)
        rows.append(row)
        try:
            _save_store(path, store)
        except ScheduleError:
            _cancel_task_quiet(principal, task_id)
            raise
        return {**_character_projection(row), "created": created}


def get_schedule(principal: TaskPrincipal, schedule_id: str) -> dict[str, Any]:
    principal = _require_principal(principal)
    with _lock_for(principal.uid, principal.char_id):
        row = _find(_load_store(_path(principal))["schedules"], schedule_id)
        return _character_projection(row)


def list_schedules(
    principal: TaskPrincipal,
    *,
    include_cancelled: bool = False,
    include_completed: bool = False,
) -> list[dict[str, Any]]:
    principal = _require_principal(principal)
    with _lock_for(principal.uid, principal.char_id):
        rows = _load_store(_path(principal))["schedules"]
    out: list[dict[str, Any]] = []
    for row in rows:
        status = row.get("status")
        if status in ACTIVE_STATUSES:
            out.append(_character_projection(row))
        elif include_cancelled and status == STATUS_CANCELLED:
            out.append(_character_projection(row))
        elif include_completed and status == STATUS_COMPLETED:
            out.append(_character_projection(row))
    out.sort(key=lambda item: (item.get("due_at") or 0, item.get("schedule_id") or ""))
    return out


def prompt_reminders(principal: TaskPrincipal) -> list[dict[str, Any]]:
    """Scoped projection for layer 5.2. Newly added scheduled items are visible."""
    items = []
    for row in list_schedules(principal):
        items.append({
            "content": row["content"],
            "remind_at": row["remind_at"],
            "schedule_id": row["schedule_id"],
            "status": row["status"],
            "revision": row["revision"],
        })
    return items


def update_schedule(
    principal: TaskPrincipal,
    schedule_id: str,
    *,
    expected_revision: int,
    content: str | None = None,
    due_at: float | None = None,
    recurrence_seconds: int | None | object = ...,
    causation_ref: CausationRef | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    principal = _require_principal(principal)
    timestamp = _now(now)
    causation = causation_ref or CausationRef(
        "tool_request", f"reminder:update:{principal.uid}:{principal.char_id}:{schedule_id}",
    )
    path = _path(principal)
    with _lock_for(principal.uid, principal.char_id):
        store = _load_store(path)
        row = _find(store["schedules"], schedule_id)
        if row.get("status") == STATUS_COMPLETED:
            raise ScheduleError("path_not_found", extra={"reason": "completed"})
        if row.get("status") == STATUS_CANCELLED:
            raise ScheduleError("path_not_found", extra={"reason": "cancelled"})
        _require_revision(row, expected_revision)
        if content is not None:
            row["content"] = _validate_content(content)
        if due_at is not None:
            due = float(due_at)
            if due <= 0:
                raise ScheduleError("path_not_found", extra={"reason": "invalid_due"})
            row["due_at"] = due
        if recurrence_seconds is not ...:
            row["repeat"] = _normalize_repeat(recurrence_seconds)  # type: ignore[arg-type]
        row["revision"] = int(row["revision"]) + 1
        row["updated_at"] = timestamp
        if row.get("status") != STATUS_IN_FLIGHT:
            row["status"] = STATUS_SCHEDULED
            _ensure_runnable_task(principal, row, causation_ref=causation, now=timestamp)
        _push_history(row, action="update", now=timestamp)
        _save_store(path, store)
        return _character_projection(row)


def cancel_schedule(
    principal: TaskPrincipal,
    schedule_id: str,
    *,
    expected_revision: int | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    principal = _require_principal(principal)
    timestamp = _now(now)
    path = _path(principal)
    with _lock_for(principal.uid, principal.char_id):
        store = _load_store(path)
        row = _find(store["schedules"], schedule_id)
        if expected_revision is not None:
            _require_revision(row, expected_revision)
        if row.get("status") == STATUS_COMPLETED:
            raise ScheduleError("path_not_found", extra={"reason": "completed"})
        if row.get("status") == STATUS_CANCELLED:
            return _character_projection(row)
        in_flight = row.get("status") == STATUS_IN_FLIGHT
        row["canceled"] = True
        row["status"] = STATUS_IN_FLIGHT if in_flight else STATUS_CANCELLED
        row["revision"] = int(row["revision"]) + 1
        row["updated_at"] = timestamp
        _push_history(row, action="cancel", now=timestamp)
        _save_store(path, store)
        if not in_flight:
            _cancel_task_quiet(principal, str(row.get("task_id") or ""))
        return _character_projection(row)


def restore_schedule(
    principal: TaskPrincipal,
    schedule_id: str,
    *,
    revision: int,
    causation_ref: CausationRef | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Restore a cancelled reminder onto a new runnable lifecycle. Completed leases stay dead."""
    principal = _require_principal(principal)
    timestamp = _now(now)
    causation = causation_ref or CausationRef(
        "tool_request", f"reminder:restore:{principal.uid}:{principal.char_id}:{schedule_id}:{revision}",
    )
    path = _path(principal)
    with _lock_for(principal.uid, principal.char_id):
        store = _load_store(path)
        row = _find(store["schedules"], schedule_id)
        if row.get("status") == STATUS_COMPLETED:
            raise ScheduleError("path_not_found", extra={"reason": "completed_lease"})
        if row.get("status") not in RESTORABLE_STATUSES:
            raise ScheduleError("path_not_found", extra={"reason": "not_restorable"})
        snapshot = None
        for item in reversed(list(row.get("history") or [])):
            if int(item.get("revision") or 0) == int(revision):
                snapshot = item
                break
        if snapshot is None and int(row.get("revision") or 0) == int(revision):
            snapshot = row
        if snapshot is None:
            raise ScheduleError("path_not_found", extra={"reason": "revision_missing"})
        live = [item for item in store["schedules"] if item.get("status") in ACTIVE_STATUSES]
        if len(live) >= MAX_SCHEDULES:
            raise ScheduleError("quota_exhausted", extra={"limit": MAX_SCHEDULES})
        old_task = str(row.get("task_id") or "")
        try:
            task_id, _created = _new_task(
                principal,
                idempotency_key=f"reminder-restore:{schedule_id}:{int(revision)}:{int(timestamp * 1000)}",
                ttl_seconds=DEFAULT_TTL_SECONDS,
                causation_ref=causation,
                now=timestamp,
            )
        except task_manager.TaskManagerError as exc:
            raise ScheduleError("quota_exhausted", extra={"reason": exc.code}) from exc
        row["content"] = _validate_content(str(snapshot.get("content") or row.get("content") or ""))
        row["due_at"] = float(snapshot.get("due_at") or row.get("due_at") or timestamp)
        repeat = snapshot.get("repeat") if isinstance(snapshot.get("repeat"), dict) else row.get("repeat")
        seconds = repeat.get("seconds") if isinstance(repeat, dict) else None
        row["repeat"] = _normalize_repeat(int(seconds) if isinstance(seconds, int) else None)
        row["task_id"] = task_id
        row["canceled"] = False
        row["status"] = STATUS_SCHEDULED
        row["in_flight_revision"] = None
        row["in_flight_due_at"] = None
        row["in_flight_occurrence"] = None
        row["delivery_attempts"] = 0
        row["revision"] = int(row["revision"]) + 1
        row["updated_at"] = timestamp
        _push_history(row, action="restore", now=timestamp)
        try:
            _save_store(path, store)
        except ScheduleError:
            _cancel_task_quiet(principal, task_id)
            raise
        _cancel_task_quiet(principal, old_task)
        return _character_projection(row)


def due_schedules(principal: TaskPrincipal, *, now: float | None = None) -> list[dict[str, Any]]:
    """Internal delivery candidates: scheduled-due plus in-flight crash recovery."""
    principal = _require_principal(principal)
    timestamp = _now(now)
    with _lock_for(principal.uid, principal.char_id):
        rows = _load_store(_path(principal))["schedules"]
    due: list[dict[str, Any]] = []
    for row in rows:
        status = row.get("status")
        if status == STATUS_IN_FLIGHT:
            due.append(dict(row))
            continue
        if status != STATUS_SCHEDULED or row.get("canceled"):
            continue
        if float(row.get("due_at") or 0) <= timestamp:
            due.append(dict(row))
    return due


def begin_delivery(
    principal: TaskPrincipal,
    schedule_id: str,
    *,
    expected_revision: int,
    occurrence: str,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Mark in-flight after rechecking revision/cancel. None means stale or cancelled."""
    principal = _require_principal(principal)
    timestamp = _now(now)
    causation = CausationRef("signal", occurrence)
    path = _path(principal)
    with _lock_for(principal.uid, principal.char_id):
        store = _load_store(path)
        try:
            row = _find(store["schedules"], schedule_id)
        except ScheduleError:
            return None
        if row.get("status") == STATUS_COMPLETED:
            return None
        if row.get("status") == STATUS_CANCELLED and row.get("in_flight_occurrence") != occurrence:
            return None
        if row.get("status") == STATUS_IN_FLIGHT:
            if str(row.get("in_flight_occurrence") or "") not in {"", occurrence}:
                return None
            if int(row.get("in_flight_revision") or 0) not in {0, int(expected_revision)}:
                return None
            _ensure_runnable_task(principal, row, causation_ref=causation, now=timestamp)
            _save_store(path, store)
            return dict(row)
        if row.get("status") != STATUS_SCHEDULED or row.get("canceled"):
            return None
        if int(row.get("revision") or 0) != int(expected_revision):
            return None
        if float(row.get("due_at") or 0) > timestamp:
            return None
        _ensure_runnable_task(principal, row, causation_ref=causation, now=timestamp)
        row["status"] = STATUS_IN_FLIGHT
        row["in_flight_revision"] = int(expected_revision)
        row["in_flight_due_at"] = float(row["due_at"])
        row["in_flight_occurrence"] = occurrence
        row["updated_at"] = timestamp
        _save_store(path, store)
        return dict(row)


def finish_delivery(
    principal: TaskPrincipal,
    schedule_id: str,
    *,
    sent: bool,
    occurrence: str,
    now: float | None = None,
) -> bool:
    """Complete the in-flight occurrence. Recurrence spawns a new task; unsent reverts."""
    principal = _require_principal(principal)
    timestamp = _now(now)
    path = _path(principal)
    with _lock_for(principal.uid, principal.char_id):
        store = _load_store(path)
        try:
            row = _find(store["schedules"], schedule_id)
        except ScheduleError:
            return False
        if row.get("status") != STATUS_IN_FLIGHT:
            return False
        if str(row.get("in_flight_occurrence") or "") not in {"", occurrence}:
            return False
        if not sent:
            cancelled = bool(row.get("canceled"))
            row["status"] = STATUS_CANCELLED if cancelled else STATUS_SCHEDULED
            row["in_flight_revision"] = None
            row["in_flight_due_at"] = None
            row["in_flight_occurrence"] = None
            row["delivery_attempts"] = int(row.get("delivery_attempts") or 0) + 1
            row["updated_at"] = timestamp
            _save_store(path, store)
            return True
        in_flight_due = float(row.get("in_flight_due_at") or row.get("due_at") or timestamp)
        in_flight_revision = int(row.get("in_flight_revision") or row.get("revision") or 0)
        updated_during_send = int(row.get("revision") or 0) != in_flight_revision
        cancelled = bool(row.get("canceled"))
        repeat = row.get("repeat") or {}
        interval = int(repeat["seconds"]) if repeat.get("kind") == "interval" and repeat.get("seconds") else None
        old_task = str(row.get("task_id") or "")
        row["delivered_count"] = int(row.get("delivered_count") or 0) + 1
        row["in_flight_revision"] = None
        row["in_flight_due_at"] = None
        row["in_flight_occurrence"] = None
        row["updated_at"] = timestamp
        if cancelled:
            row["status"] = STATUS_CANCELLED
            row["canceled"] = True
            _push_history(row, action="delivered_then_cancelled", now=timestamp)
            _save_store(path, store)
            _complete_current_task(principal, {**row, "task_id": old_task}, now=timestamp, outcome="delivered")
            return True
        if updated_during_send:
            row["status"] = STATUS_SCHEDULED
            _ensure_runnable_task(
                principal, row,
                causation_ref=CausationRef("signal", occurrence),
                now=timestamp,
            )
            _push_history(row, action="delivered_stale_revision", now=timestamp)
            _save_store(path, store)
            if old_task and old_task != row.get("task_id"):
                _complete_current_task(principal, {**row, "task_id": old_task}, now=timestamp, outcome="delivered")
            return True
        if interval:
            next_due = in_flight_due + interval
            if next_due <= timestamp:
                next_due = timestamp + interval
            try:
                new_task, _created = _new_task(
                    principal,
                    idempotency_key=f"reminder-next:{schedule_id}:{int(next_due)}",
                    ttl_seconds=DEFAULT_TTL_SECONDS,
                    causation_ref=CausationRef("signal", f"reminder-next:{schedule_id}:{int(next_due)}"),
                    now=timestamp,
                )
            except task_manager.TaskManagerError:
                row["status"] = STATUS_SCHEDULED
                row["due_at"] = next_due
                _save_store(path, store)
                return True
            row["due_at"] = next_due
            row["task_id"] = new_task
            row["status"] = STATUS_SCHEDULED
            row["delivery_attempts"] = 0
            row["revision"] = int(row["revision"]) + 1
            _push_history(row, action="recurrence", now=timestamp)
            try:
                _save_store(path, store)
            except ScheduleError:
                _cancel_task_quiet(principal, new_task)
                raise
            _complete_current_task(principal, {**row, "task_id": old_task}, now=timestamp, outcome="delivered")
            return True
        row["status"] = STATUS_COMPLETED
        row["canceled"] = False
        _push_history(row, action="completed", now=timestamp)
        _save_store(path, store)
        _complete_current_task(principal, {**row, "task_id": old_task}, now=timestamp, outcome="delivered")
        return True


def mark_delivered(principal: TaskPrincipal, schedule_id: str, *, now: float | None = None) -> bool:
    """Compatibility wrapper: treat as a successful in-flight finish when already claimed."""
    timestamp = _now(now)
    with _lock_for(principal.uid, principal.char_id):
        rows = _load_store(_path(principal))["schedules"]
        row = next((item for item in rows if item.get("schedule_id") == schedule_id), None)
        if row is None:
            return False
        occurrence = str(row.get("in_flight_occurrence") or f"schedule:{schedule_id}:{int(float(row.get('due_at') or 0))}")
        revision = int(row.get("in_flight_revision") or row.get("revision") or 0)
    if row.get("status") != STATUS_IN_FLIGHT:
        claimed = begin_delivery(
            principal, schedule_id, expected_revision=revision, occurrence=occurrence, now=timestamp,
        )
        if claimed is None:
            return False
    return finish_delivery(principal, schedule_id, sent=True, occurrence=occurrence, now=timestamp)


def iter_schedule_scopes(*, uid: str | None = None) -> list[tuple[str, str]]:
    """Enumerate Reality schedule files as (uid, char_id)."""
    root = get_paths().agent_runtime_schedules_root()
    if not root.exists():
        return []
    scopes: list[tuple[str, str]] = []
    try:
        paths = sorted(path for path in root.glob("*/*.json") if path.is_file())
    except OSError:
        return []
    for path in paths:
        owner = path.parent.name
        char_id = path.stem
        if uid is not None and owner != uid:
            continue
        try:
            safe_user_id(owner)
            safe_user_id(char_id)
        except ValueError:
            continue
        scopes.append((owner, char_id))
    return scopes


def import_index(principal: TaskPrincipal) -> tuple[set[str], set[tuple[str, float]]]:
    """Internal migration index: leftover ids and (content, due_at) keys."""
    principal = _require_principal(principal)
    with _lock_for(principal.uid, principal.char_id):
        rows = _load_store(_path(principal))["schedules"]
    legacy_ids = {str(row.get("legacy_id") or "") for row in rows if row.get("legacy_id")}
    keys = {(str(row.get("content") or ""), float(row.get("due_at") or 0)) for row in rows}
    return legacy_ids, keys


def due_across_owner(uid: str, *, now: float | None = None) -> list[dict[str, Any]]:
    """All due rows for one owner, every character that has a schedule file."""
    timestamp = _now(now)
    items: list[dict[str, Any]] = []
    for owner, char_id in iter_schedule_scopes(uid=uid):
        principal = TaskPrincipal.reality(owner, char_id)
        for row in due_schedules(principal, now=timestamp):
            items.append({**row, "uid": owner, "char_id": char_id})
    return items


def observability_snapshot(uid: str | None = None, char_id: str | None = None) -> dict[str, Any]:
    """Metadata-only projection. No reminder bodies, secrets, or absolute paths."""
    from core.reminder_migration import observability_projection

    legacy = observability_projection()
    if not uid or not char_id:
        return {
            "capability": OBSERVABILITY_CAPABILITY,
            "configured": True,
            "effective": True,
            "note": "pass uid and char_id for a scoped bucket; no reminder bodies",
            "legacy_reminder": legacy.get("legacy_reminder") or {},
            "redaction": {"version": REDACTION_VERSION, "counts": redaction_observability()["counts"]},
        }
    principal = _require_principal(TaskPrincipal.reality(uid, char_id))
    path = _path(principal)
    with _lock_for(principal.uid, principal.char_id):
        rows = _load_store(path)["schedules"]
    counts = {
        "scheduled": 0,
        "in_flight": 0,
        "cancelled": 0,
        "completed": 0,
        "total": len(rows),
    }
    items = []
    for row in rows:
        status = str(row.get("status") or "")
        if status in counts:
            counts[status] += 1
        items.append(_obs_item(row))
    payload = {
        "capability": OBSERVABILITY_CAPABILITY,
        "configured": True,
        "effective": True,
        "counts": counts,
        "items": items[:200],
        "legacy_reminder": legacy.get("legacy_reminder") or {},
        "redaction": {
            "version": REDACTION_VERSION,
            "counts": redaction_observability()["counts"],
        },
        "note": "metadata only; no reminder bodies, secrets, or absolute paths",
    }
    blob = json.dumps(payload)
    if str(path) in blob or str(path.parent) in blob:
        raise RuntimeError("character-reminders observability leaked an absolute path")
    return payload
