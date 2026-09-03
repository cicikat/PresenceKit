"""Durable Reality task lifecycle and metadata-only receipts (Brief 230)."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import time
import uuid
from collections import Counter
from typing import Any, Iterable

from core.agent_runtime import task_store
from core.agent_runtime.models import (
    CausationRef,
    RetryPolicy,
    TaskLease,
    TaskPrincipal,
    TaskRecord,
    TaskStatus,
)
from core.data_paths import safe_user_id


DEFAULT_LEASE_SECONDS = 90
MIN_TTL_SECONDS = 1
MAX_TTL_SECONDS = 365 * 24 * 60 * 60
MAX_LEASE_SECONDS = 15 * 60
MAX_TASKS_PER_SCOPE = 1000
MAX_OBSERVABILITY_SCOPES = 1000
TERMINAL_RETENTION_SECONDS = 30 * 24 * 60 * 60
_NAME_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CAUSATION_KINDS = frozenset({"reality_turn", "signal", "admin_action", "parent_task"})
_RESULT_KEYS = frozenset({"outcome_code", "artifact_ids", "counters", "truncated"})


class TaskManagerError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now(value: float | None) -> float:
    return time.time() if value is None else float(value)


def _validate_principal(principal: TaskPrincipal) -> TaskPrincipal:
    if not isinstance(principal, TaskPrincipal):
        raise TaskManagerError("invalid_task_principal")
    if principal.realm != "reality":
        raise TaskManagerError("realm_forbidden")
    try:
        safe_user_id(principal.uid)
        safe_user_id(principal.char_id)
    except ValueError as exc:
        raise TaskManagerError("invalid_task_scope") from exc
    return principal


def _validate_name(value: object, field: str) -> str:
    text = str(value or "")
    if not _NAME_RE.fullmatch(text):
        raise TaskManagerError(f"invalid_{field}")
    return text


def _validate_code(value: object, field: str) -> str:
    text = str(value or "")
    if not _CODE_RE.fullmatch(text):
        raise TaskManagerError(f"invalid_{field}")
    return text


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_causation(value: CausationRef | None) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, CausationRef) or value.kind not in _CAUSATION_KINDS:
        raise TaskManagerError("invalid_causation_ref")
    reference = str(value.reference or "")
    if not reference or len(reference) > 256:
        raise TaskManagerError("invalid_causation_ref")
    return {"kind": value.kind, "ref_digest": _digest(reference)[:24]}


def _request_digest(
    *,
    capability: str,
    source: str,
    ttl_seconds: int,
    causation_ref: dict[str, str] | None,
    retry_policy: str,
    max_attempts: int,
) -> str:
    payload = {
        "capability": capability,
        "source": source,
        "ttl_seconds": ttl_seconds,
        "causation_ref": causation_ref,
        "retry_policy": retry_policy,
        "max_attempts": max_attempts,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _digest(encoded)


def _normalize_result_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - _RESULT_KEYS:
        raise TaskManagerError("invalid_result_metadata")
    result: dict[str, Any] = {}
    if "outcome_code" in value:
        result["outcome_code"] = _validate_code(value["outcome_code"], "outcome_code")
    if "artifact_ids" in value:
        items = value["artifact_ids"]
        if not isinstance(items, list) or len(items) > 10:
            raise TaskManagerError("invalid_result_metadata")
        normalized = []
        for item in items:
            text = str(item or "")
            if not _CODE_RE.fullmatch(text):
                raise TaskManagerError("invalid_result_metadata")
            normalized.append(text)
        result["artifact_ids"] = normalized
    if "counters" in value:
        counters = value["counters"]
        if not isinstance(counters, dict) or len(counters) > 16:
            raise TaskManagerError("invalid_result_metadata")
        clean_counters: dict[str, int | float | bool] = {}
        for key, item in counters.items():
            if (
                not _CODE_RE.fullmatch(str(key))
                or not isinstance(item, (int, float, bool))
                or (isinstance(item, float) and not math.isfinite(item))
            ):
                raise TaskManagerError("invalid_result_metadata")
            clean_counters[str(key)] = item
        result["counters"] = clean_counters
    if "truncated" in value:
        if not isinstance(value["truncated"], bool):
            raise TaskManagerError("invalid_result_metadata")
        result["truncated"] = value["truncated"]
    return result


def _receipt(task: TaskRecord) -> dict[str, Any]:
    return {
        "schema_version": task.schema_version,
        "task_id": task.task_id,
        "realm": task.realm,
        "capability": task.capability,
        "source": task.source,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "queued_at": task.queued_at or None,
        "started_at": task.started_at or None,
        "finished_at": task.finished_at or None,
        "expires_at": task.expires_at,
        "attempt_count": task.attempt_count,
        "max_attempts": task.max_attempts,
        "retry_policy": task.retry_policy,
        "lease_until": task.lease_until or None,
        "cancel_requested": bool(task.cancel_requested_at),
        "cancel_reason_code": task.cancel_reason_code or None,
        "error_code": task.error_code or None,
        "recovery_reason": task.recovery_reason or None,
        "causation_ref": dict(task.causation_ref) if task.causation_ref else None,
        "result_metadata": dict(task.result_metadata),
    }


def _clear_lease(task: TaskRecord) -> None:
    task.current_attempt_id = ""
    task.lease_owner_instance = ""
    task.lease_token = ""
    task.lease_until = 0.0


def _terminalize(
    task: TaskRecord,
    status: str,
    now: float,
    *,
    error_code: str = "",
    recovery_reason: str = "",
) -> None:
    task.status = status
    task.updated_at = now
    task.finished_at = now
    task.next_attempt_at = 0.0
    task.error_code = error_code
    task.recovery_reason = recovery_reason
    _clear_lease(task)


def _recover_records(tasks: list[TaskRecord], now: float) -> bool:
    changed = False
    instance = task_store.process_instance_id()
    for task in tasks:
        if task.terminal:
            continue
        if task.status in {TaskStatus.CREATED.value, TaskStatus.QUEUED.value} and task.expires_at <= now:
            _terminalize(task, TaskStatus.EXPIRED.value, now, error_code="task_ttl_expired")
            changed = True
            continue
        if task.status != TaskStatus.RUNNING.value:
            continue
        if not task.lease_owner_instance or task.lease_owner_instance != instance:
            _terminalize(
                task,
                TaskStatus.OUTCOME_UNKNOWN.value,
                now,
                error_code="worker_process_lost",
                recovery_reason="process_restart",
            )
            changed = True
        elif task.expires_at <= now:
            _terminalize(
                task,
                TaskStatus.OUTCOME_UNKNOWN.value,
                now,
                error_code="task_expired_while_running",
                recovery_reason="ttl_elapsed",
            )
            changed = True
        elif task.lease_until <= now:
            if (
                task.retry_policy == RetryPolicy.SAFE.value
                and task.attempt_count < task.max_attempts
                and not task.cancel_requested_at
            ):
                task.status = TaskStatus.QUEUED.value
                task.updated_at = now
                task.next_attempt_at = now
                task.error_code = "lease_expired_retry"
                task.recovery_reason = "lease_lost"
                _clear_lease(task)
            else:
                _terminalize(
                    task,
                    TaskStatus.OUTCOME_UNKNOWN.value,
                    now,
                    error_code="lease_lost",
                    recovery_reason="lease_lost",
                )
            changed = True
    return changed


def _load_records(
    principal: TaskPrincipal, now: float
) -> tuple[dict[str, Any], list[TaskRecord], bool]:
    try:
        state = task_store.load_unlocked(principal.uid, principal.char_id)
    except task_store.TaskStoreError as exc:
        raise TaskManagerError(exc.code) from exc
    try:
        records = [TaskRecord.from_dict(raw) for raw in state["tasks"]]
    except (TypeError, ValueError, KeyError) as exc:
        raise TaskManagerError("task_store_record_invalid") from exc
    for task in records:
        if (
            task.schema_version != "agent-runtime-task.v1"
            or task.realm != "reality"
            or task.uid != principal.uid
            or task.char_id != principal.char_id
            or not isinstance(task.task_id, str)
            or not _TASK_ID_RE.fullmatch(task.task_id)
            or task.status not in {item.value for item in TaskStatus}
            or not isinstance(task.capability, str)
            or not _NAME_RE.fullmatch(task.capability)
            or not isinstance(task.source, str)
            or not _NAME_RE.fullmatch(task.source)
            or not isinstance(task.idempotency_digest, str)
            or not _DIGEST_RE.fullmatch(task.idempotency_digest)
            or not isinstance(task.request_digest, str)
            or not _DIGEST_RE.fullmatch(task.request_digest)
        ):
            raise TaskManagerError("task_store_record_invalid")
    changed = _recover_records(records, now)
    return state, records, changed


def _save_records(
    principal: TaskPrincipal,
    state: dict[str, Any],
    records: list[TaskRecord],
    *,
    now: float | None = None,
) -> None:
    active = [task for task in records if not task.terminal]
    terminal = sorted(
        (task for task in records if task.terminal),
        key=lambda task: (task.finished_at, task.created_at, task.task_id),
        reverse=True,
    )
    timestamp = _now(now)
    recent = [
        task for task in terminal
        if not task.finished_at or timestamp - task.finished_at <= TERMINAL_RETENTION_SECONDS
    ]
    keep = active + recent[: max(0, MAX_TASKS_PER_SCOPE - len(active))]
    state["tasks"] = [task.to_dict() for task in keep]
    try:
        task_store.save_unlocked(principal.uid, principal.char_id, state)
    except task_store.TaskStoreError as exc:
        raise TaskManagerError(exc.code) from exc


def create_task(
    principal: TaskPrincipal,
    *,
    capability: str,
    source: str,
    idempotency_key: str,
    ttl_seconds: int,
    causation_ref: CausationRef | None = None,
    retry_policy: str = RetryPolicy.NEVER.value,
    max_attempts: int = 1,
    enqueue: bool = True,
    now: float | None = None,
) -> tuple[dict[str, Any], bool]:
    principal = _validate_principal(principal)
    capability = _validate_name(capability, "capability")
    source = _validate_name(source, "source")
    if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 256:
        raise TaskManagerError("invalid_idempotency_key")
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or not MIN_TTL_SECONDS <= ttl_seconds <= MAX_TTL_SECONDS
    ):
        raise TaskManagerError("invalid_task_ttl")
    if retry_policy not in {item.value for item in RetryPolicy}:
        raise TaskManagerError("invalid_retry_policy")
    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or not 1 <= max_attempts <= 10
    ):
        raise TaskManagerError("invalid_max_attempts")
    if retry_policy == RetryPolicy.NEVER.value and max_attempts != 1:
        raise TaskManagerError("unsafe_retry_configuration")
    timestamp = _now(now)
    causal = _normalize_causation(causation_ref)
    idem_digest = _digest(idempotency_key)
    request_digest = _request_digest(
        capability=capability,
        source=source,
        ttl_seconds=ttl_seconds,
        causation_ref=causal,
        retry_policy=retry_policy,
        max_attempts=max_attempts,
    )
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        for task in records:
            if task.idempotency_digest != idem_digest:
                continue
            if task.request_digest != request_digest:
                if changed:
                    _save_records(principal, state, records, now=timestamp)
                raise TaskManagerError("idempotency_conflict")
            if changed:
                _save_records(principal, state, records, now=timestamp)
            return _receipt(task), False
        task = TaskRecord(
            task_id=uuid.uuid4().hex,
            uid=principal.uid,
            char_id=principal.char_id,
            capability=capability,
            source=source,
            status=TaskStatus.QUEUED.value if enqueue else TaskStatus.CREATED.value,
            created_at=timestamp,
            updated_at=timestamp,
            ttl_seconds=ttl_seconds,
            expires_at=timestamp + ttl_seconds,
            idempotency_digest=idem_digest,
            request_digest=request_digest,
            causation_ref=causal,
            retry_policy=retry_policy,
            max_attempts=max_attempts,
            queued_at=timestamp if enqueue else 0.0,
        )
        records.append(task)
        _save_records(principal, state, records, now=timestamp)
        return _receipt(task), True


def _find(records: Iterable[TaskRecord], task_id: str) -> TaskRecord:
    if not _TASK_ID_RE.fullmatch(str(task_id or "")):
        raise TaskManagerError("invalid_task_id")
    task = next((item for item in records if item.task_id == task_id), None)
    if task is None:
        raise TaskManagerError("task_not_found")
    return task


def enqueue_task(
    principal: TaskPrincipal, task_id: str, *, now: float | None = None
) -> dict[str, Any]:
    principal = _validate_principal(principal)
    timestamp = _now(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        task = _find(records, task_id)
        if task.status == TaskStatus.CREATED.value:
            task.status = TaskStatus.QUEUED.value
            task.queued_at = timestamp
            task.updated_at = timestamp
            changed = True
        elif task.status not in {TaskStatus.QUEUED.value, TaskStatus.RUNNING.value}:
            if changed:
                _save_records(principal, state, records, now=timestamp)
            raise TaskManagerError("task_terminal")
        if changed:
            _save_records(principal, state, records, now=timestamp)
        return _receipt(task)


def claim_next(
    principal: TaskPrincipal,
    *,
    task_id: str | None = None,
    capabilities: set[str] | frozenset[str] | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: float | None = None,
) -> TaskLease | None:
    principal = _validate_principal(principal)
    if (
        not isinstance(lease_seconds, int)
        or isinstance(lease_seconds, bool)
        or not 1 <= lease_seconds <= MAX_LEASE_SECONDS
    ):
        raise TaskManagerError("invalid_lease_seconds")
    allowed = (
        None
        if capabilities is None
        else {_validate_name(item, "capability") for item in capabilities}
    )
    if task_id is not None and not _TASK_ID_RE.fullmatch(str(task_id)):
        raise TaskManagerError("invalid_task_id")
    timestamp = _now(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        candidates = [
            task
            for task in records
            if task.status == TaskStatus.QUEUED.value
            and task.next_attempt_at <= timestamp
            and (task_id is None or task.task_id == task_id)
            and (allowed is None or task.capability in allowed)
        ]
        if not candidates:
            if changed:
                _save_records(principal, state, records, now=timestamp)
            return None
        task = min(candidates, key=lambda item: (item.queued_at, item.created_at, item.task_id))
        task.status = TaskStatus.RUNNING.value
        task.attempt_count += 1
        task.current_attempt_id = uuid.uuid4().hex
        task.lease_token = secrets.token_urlsafe(32)
        task.lease_owner_instance = task_store.process_instance_id()
        task.lease_until = min(task.expires_at, timestamp + lease_seconds)
        task.started_at = task.started_at or timestamp
        task.updated_at = timestamp
        task.error_code = ""
        task.recovery_reason = ""
        _save_records(principal, state, records, now=timestamp)
        return TaskLease(
            task_id=task.task_id,
            attempt_id=task.current_attempt_id,
            lease_token=task.lease_token,
            lease_until=task.lease_until,
            receipt=_receipt(task),
        )


def _require_lease(task: TaskRecord, lease: TaskLease, now: float) -> None:
    if task.status != TaskStatus.RUNNING.value:
        if task.recovery_reason in {"lease_lost", "process_restart", "ttl_elapsed"}:
            raise TaskManagerError("lease_lost")
        raise TaskManagerError("task_not_running")
    if (
        lease.task_id != task.task_id
        or not secrets.compare_digest(lease.attempt_id, task.current_attempt_id)
        or not secrets.compare_digest(lease.lease_token, task.lease_token)
        or task.lease_owner_instance != task_store.process_instance_id()
        or task.lease_until <= now
    ):
        raise TaskManagerError("lease_lost")


def renew_lease(
    principal: TaskPrincipal,
    lease: TaskLease,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: float | None = None,
) -> TaskLease:
    principal = _validate_principal(principal)
    if (
        not isinstance(lease_seconds, int)
        or isinstance(lease_seconds, bool)
        or not 1 <= lease_seconds <= MAX_LEASE_SECONDS
    ):
        raise TaskManagerError("invalid_lease_seconds")
    timestamp = _now(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        task = _find(records, lease.task_id)
        if changed:
            _save_records(principal, state, records, now=timestamp)
        _require_lease(task, lease, timestamp)
        if task.cancel_requested_at:
            raise TaskManagerError("cancel_requested")
        task.lease_until = min(task.expires_at, timestamp + lease_seconds)
        task.updated_at = timestamp
        _save_records(principal, state, records, now=timestamp)
        return TaskLease(
            task.task_id,
            task.current_attempt_id,
            task.lease_token,
            task.lease_until,
            _receipt(task),
        )


def complete_task(
    principal: TaskPrincipal,
    lease: TaskLease,
    *,
    result_metadata: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    principal = _validate_principal(principal)
    metadata = _normalize_result_metadata(result_metadata)
    timestamp = _now(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        task = _find(records, lease.task_id)
        if changed:
            _save_records(principal, state, records, now=timestamp)
        _require_lease(task, lease, timestamp)
        if task.cancel_requested_at:
            raise TaskManagerError("cancel_requested")
        task.result_metadata = metadata
        _terminalize(task, TaskStatus.SUCCEEDED.value, timestamp)
        _save_records(principal, state, records, now=timestamp)
        return _receipt(task)


def fail_task(
    principal: TaskPrincipal,
    lease: TaskLease,
    *,
    error_code: str,
    retry: bool = False,
    retry_delay_seconds: int = 0,
    result_metadata: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    principal = _validate_principal(principal)
    code = _validate_code(error_code, "error_code")
    metadata = _normalize_result_metadata(result_metadata)
    if (
        not isinstance(retry_delay_seconds, int)
        or isinstance(retry_delay_seconds, bool)
        or not 0 <= retry_delay_seconds <= 24 * 60 * 60
    ):
        raise TaskManagerError("invalid_retry_delay")
    timestamp = _now(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        task = _find(records, lease.task_id)
        if changed:
            _save_records(principal, state, records, now=timestamp)
        _require_lease(task, lease, timestamp)
        task.result_metadata = metadata
        can_retry = (
            retry
            and task.retry_policy == RetryPolicy.SAFE.value
            and task.attempt_count < task.max_attempts
            and not task.cancel_requested_at
            and timestamp + retry_delay_seconds < task.expires_at
        )
        if can_retry:
            task.status = TaskStatus.QUEUED.value
            task.updated_at = timestamp
            task.next_attempt_at = timestamp + retry_delay_seconds
            task.error_code = code
            _clear_lease(task)
        else:
            terminal_code = (
                "retry_not_permitted"
                if retry and task.retry_policy != RetryPolicy.SAFE.value
                else code
            )
            _terminalize(task, TaskStatus.FAILED.value, timestamp, error_code=terminal_code)
        _save_records(principal, state, records, now=timestamp)
        return _receipt(task)


def request_cancel(
    principal: TaskPrincipal,
    task_id: str,
    *,
    reason_code: str = "user_requested",
    now: float | None = None,
) -> dict[str, Any]:
    principal = _validate_principal(principal)
    reason = _validate_code(reason_code, "cancel_reason")
    timestamp = _now(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        task = _find(records, task_id)
        if task.terminal:
            if changed:
                _save_records(principal, state, records, now=timestamp)
            return _receipt(task)
        task.cancel_requested_at = task.cancel_requested_at or timestamp
        task.cancel_reason_code = task.cancel_reason_code or reason
        task.updated_at = timestamp
        if task.status in {TaskStatus.CREATED.value, TaskStatus.QUEUED.value}:
            _terminalize(task, TaskStatus.CANCELED.value, timestamp, error_code="task_canceled")
        _save_records(principal, state, records, now=timestamp)
        return _receipt(task)


def acknowledge_cancel(
    principal: TaskPrincipal,
    lease: TaskLease,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    principal = _validate_principal(principal)
    timestamp = _now(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        task = _find(records, lease.task_id)
        if changed:
            _save_records(principal, state, records, now=timestamp)
        _require_lease(task, lease, timestamp)
        if not task.cancel_requested_at:
            raise TaskManagerError("cancel_not_requested")
        _terminalize(task, TaskStatus.CANCELED.value, timestamp, error_code="task_canceled")
        _save_records(principal, state, records, now=timestamp)
        return _receipt(task)


def get_task(
    principal: TaskPrincipal, task_id: str, *, now: float | None = None
) -> dict[str, Any]:
    principal = _validate_principal(principal)
    timestamp = _now(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        task = _find(records, task_id)
        if changed:
            _save_records(principal, state, records, now=timestamp)
        return _receipt(task)


def list_tasks(
    principal: TaskPrincipal,
    *,
    status: str | None = None,
    capability: str | None = None,
    limit: int = 100,
    now: float | None = None,
) -> list[dict[str, Any]]:
    principal = _validate_principal(principal)
    if status is not None and status not in {item.value for item in TaskStatus}:
        raise TaskManagerError("invalid_task_status")
    if capability is not None:
        capability = _validate_name(capability, "capability")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise TaskManagerError("invalid_limit")
    timestamp = _now(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        if changed:
            _save_records(principal, state, records, now=timestamp)
        selected = [
            task
            for task in records
            if (status is None or task.status == status)
            and (capability is None or task.capability == capability)
        ]
    selected.sort(key=lambda task: (task.created_at, task.task_id), reverse=True)
    return [_receipt(task) for task in selected[:limit]]


def recover_scope(
    principal: TaskPrincipal, *, now: float | None = None
) -> dict[str, int]:
    principal = _validate_principal(principal)
    timestamp = _now(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, records, changed = _load_records(principal, timestamp)
        if changed:
            _save_records(principal, state, records, now=timestamp)
    counts = Counter(task.status for task in records)
    return {"changed": int(changed), "total": len(records), **dict(counts)}


def recover_all_tasks(
    *, max_scopes: int = 1000, now: float | None = None
) -> dict[str, int]:
    timestamp = _now(now)
    result = {
        "scopes": 0,
        "changed_scopes": 0,
        "unreadable_scopes": 0,
        "truncated": 0,
    }
    paths = task_store.iter_scope_paths()
    if len(paths) > max_scopes:
        paths = paths[:max_scopes]
        result["truncated"] = 1
    for path in paths:
        scope = task_store.scope_from_path(path)
        if scope is None:
            continue
        uid, char_id = scope
        result["scopes"] += 1
        try:
            recovered = recover_scope(TaskPrincipal.reality(uid, char_id), now=timestamp)
            result["changed_scopes"] += int(bool(recovered["changed"]))
        except TaskManagerError:
            result["unreadable_scopes"] += 1
    return result


def observability_snapshot(
    *,
    uid: str | None = None,
    char_id: str | None = None,
    task_id: str | None = None,
    status: str | None = None,
    capability: str | None = None,
    limit: int = 50,
    now: float | None = None,
) -> dict[str, Any]:
    if uid is not None:
        try:
            uid = safe_user_id(uid)
        except ValueError as exc:
            raise TaskManagerError("invalid_task_scope") from exc
    if char_id is not None:
        try:
            char_id = safe_user_id(char_id)
        except ValueError as exc:
            raise TaskManagerError("invalid_task_scope") from exc
    if task_id is not None and not _TASK_ID_RE.fullmatch(task_id):
        raise TaskManagerError("invalid_task_id")
    if status is not None and status not in {item.value for item in TaskStatus}:
        raise TaskManagerError("invalid_task_status")
    if capability is not None:
        capability = _validate_name(capability, "capability")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise TaskManagerError("invalid_limit")
    timestamp = _now(now)
    entries: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    unreadable = 0
    scope_paths = task_store.iter_scope_paths()
    scopes_truncated = len(scope_paths) > MAX_OBSERVABILITY_SCOPES
    for path in scope_paths[:MAX_OBSERVABILITY_SCOPES]:
        scope = task_store.scope_from_path(path)
        if scope is None:
            continue
        scope_uid, scope_char = scope
        if uid is not None and scope_uid != uid:
            continue
        if char_id is not None and scope_char != char_id:
            continue
        principal = TaskPrincipal.reality(scope_uid, scope_char)
        try:
            with task_store.scope_lock(principal.uid, principal.char_id):
                state, records, changed = _load_records(principal, timestamp)
                if changed:
                    _save_records(principal, state, records, now=timestamp)
                rows = [_receipt(task) for task in records]
        except TaskManagerError:
            unreadable += 1
            continue
        for row in rows:
            counts[row["status"]] += 1
            if task_id is not None and row["task_id"] != task_id:
                continue
            if status is not None and row["status"] != status:
                continue
            if capability is not None and row["capability"] != capability:
                continue
            entries.append({
                **row,
                "scope": {
                    "uid_digest": _digest(scope_uid)[:16],
                    "char_id": scope_char,
                    "realm": "reality",
                },
            })
    entries.sort(key=lambda row: (row["created_at"], row["task_id"]), reverse=True)
    return {
        "schema_version": "agent-runtime-task-observability.v1",
        "entries": entries[:limit],
        "count": min(len(entries), limit),
        "total_matching": len(entries),
        "status_counts": dict(sorted(counts.items())),
        "unreadable_scope_count": unreadable,
        "scopes_truncated": scopes_truncated,
        "truncated": scopes_truncated or len(entries) > limit,
    }
