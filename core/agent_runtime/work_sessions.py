"""Bounded Reality Agent Work Session lifecycle (Brief 232).

Work sessions are non-chat LLM jobs attached to a Task Manager task.  They
carry only digested context and artifact metadata; they never create turns or
write memory evidence.  Artifact writers remain capability-owned callables.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable

from core.agent_runtime import task_store
from core.agent_runtime.models import TaskPrincipal
from core.safe_write import safe_write_json
from core.sandbox import get_paths

WORK_SESSION_SCHEMA_VERSION = "agent-runtime-work-session.v1"
_SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
CAPABILITY_MANIFESTS: dict[str, frozenset[str]] = {
    "authored_diary": frozenset({"authored_diary"}),
    "document_summary": frozenset({"document_summary"}),
    "workspace_artifact": frozenset({"workspace_artifact"}),
}
_ARTIFACT_KINDS = frozenset().union(*CAPABILITY_MANIFESTS.values())
_TERMINAL = frozenset({"succeeded", "failed", "canceled", "outcome_unknown"})
_STATUSES = _TERMINAL | {"created", "running"}
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_CONTEXT_CHARS = 12000
MAX_ARTIFACT_ID_CHARS = 128
MAX_SESSIONS_PER_SCOPE = 100


class WorkSessionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class WorkSessionRecord:
    session_id: str
    task_id: str
    uid: str
    char_id: str
    realm: str
    capability: str
    artifact_kind: str
    status: str
    created_at: float
    updated_at: float
    context_digest: str
    context_chars: int
    idempotency_digest: str
    artifact_id: str = ""
    artifact_version: int = 0
    attempt_count: int = 0
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkSessionRecord":
        if not isinstance(raw, dict):
            raise TypeError("work session record must be an object")
        values = {key: raw[key] for key in cls.__dataclass_fields__ if key in raw}
        return cls(**values)


def _validate_principal(principal: TaskPrincipal) -> TaskPrincipal:
    if not isinstance(principal, TaskPrincipal) or principal.realm != "reality":
        raise WorkSessionError("realm_forbidden")
    try:
        from core.data_paths import safe_user_id
        safe_user_id(principal.uid)
        safe_user_id(principal.char_id)
    except ValueError as exc:
        raise WorkSessionError("invalid_session_scope") from exc
    return principal


def _validate_name(value: object, field: str) -> str:
    text = str(value or "")
    if not _NAME_RE.fullmatch(text):
        raise WorkSessionError(f"invalid_{field}")
    return text


def _session_path(principal: TaskPrincipal):
    return get_paths().agent_runtime_work_session_state(principal.uid, char_id=principal.char_id)


def _record_valid(row: WorkSessionRecord) -> bool:
    try:
        return bool(
            _SESSION_ID_RE.fullmatch(str(row.session_id or ""))
            and _SESSION_ID_RE.fullmatch(str(row.task_id or ""))
            and isinstance(row.capability, str)
            and row.capability in CAPABILITY_MANIFESTS
            and isinstance(row.artifact_kind, str)
            and row.artifact_kind in CAPABILITY_MANIFESTS[row.capability]
            and row.status in _STATUSES
            and _DIGEST_RE.fullmatch(str(row.context_digest or ""))
            and _DIGEST_RE.fullmatch(str(row.idempotency_digest or ""))
            and isinstance(row.context_chars, int)
            and not isinstance(row.context_chars, bool)
            and 0 <= row.context_chars <= MAX_CONTEXT_CHARS
            and isinstance(row.attempt_count, int)
            and not isinstance(row.attempt_count, bool)
            and row.attempt_count >= 0
            and isinstance(row.artifact_id, str)
            and (not row.artifact_id or _NAME_RE.fullmatch(row.artifact_id))
            and isinstance(row.artifact_version, int)
            and not isinstance(row.artifact_version, bool)
            and row.artifact_version >= 0
            and isinstance(row.error_code, str)
            and (not row.error_code or _NAME_RE.fullmatch(row.error_code))
            and isinstance(row.created_at, (int, float))
            and not isinstance(row.created_at, bool)
            and isinstance(row.updated_at, (int, float))
            and not isinstance(row.updated_at, bool)
            and (row.status != "succeeded" or (row.artifact_id and row.artifact_version > 0))
        )
    except (KeyError, TypeError, ValueError):
        return False


def _load(principal: TaskPrincipal) -> tuple[dict[str, Any], list[WorkSessionRecord]]:
    path = _session_path(principal)
    if not path.exists():
        return {"schema_version": WORK_SESSION_SCHEMA_VERSION, "sessions": []}, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema_version") != WORK_SESSION_SCHEMA_VERSION:
            raise WorkSessionError("work_session_store_invalid")
        rows = [WorkSessionRecord.from_dict(item) for item in raw.get("sessions", [])]
    except WorkSessionError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise WorkSessionError("work_session_store_invalid") from exc
    for row in rows:
        if row.realm != "reality" or row.uid != principal.uid or row.char_id != principal.char_id:
            raise WorkSessionError("work_session_scope_invalid")
        if not _record_valid(row):
            raise WorkSessionError("work_session_store_invalid")
    return raw, rows


def _save(principal: TaskPrincipal, state: dict[str, Any], rows: list[WorkSessionRecord]) -> None:
    active = [row for row in rows if row.status not in _TERMINAL]
    terminal = sorted(
        (row for row in rows if row.status in _TERMINAL),
        key=lambda row: (row.updated_at, row.created_at, row.session_id),
        reverse=True,
    )
    keep = active + terminal[:max(0, MAX_SESSIONS_PER_SCOPE - len(active))]
    state["sessions"] = [row.to_dict() for row in keep]
    if not safe_write_json(_session_path(principal), state):
        raise WorkSessionError("work_session_store_write_failed")


def _project(row: WorkSessionRecord) -> dict[str, Any]:
    return {
        "schema_version": WORK_SESSION_SCHEMA_VERSION,
        "work_session_id": row.session_id,
        "task_id": row.task_id,
        "realm": row.realm,
        "capability": row.capability,
        "artifact_kind": row.artifact_kind,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "context_digest": row.context_digest,
        "context_chars": row.context_chars,
        "artifact_id": row.artifact_id or None,
        "artifact_version": row.artifact_version or None,
        "attempt_count": row.attempt_count,
        "error_code": row.error_code or None,
    }


def create_work_session(
    principal: TaskPrincipal,
    *,
    task_id: str,
    capability: str,
    artifact_kind: str,
    context: str,
    idempotency_key: str,
    now: float | None = None,
) -> dict[str, Any]:
    principal = _validate_principal(principal)
    if not isinstance(task_id, str) or not _SESSION_ID_RE.fullmatch(task_id):
        raise WorkSessionError("invalid_task_id")
    capability = _validate_name(capability, "capability")
    if artifact_kind not in _ARTIFACT_KINDS or artifact_kind not in CAPABILITY_MANIFESTS.get(capability, frozenset()):
        raise WorkSessionError("artifact_kind_forbidden")
    if not isinstance(context, str) or len(context) > MAX_CONTEXT_CHARS:
        raise WorkSessionError("context_limit_exceeded")
    if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 256:
        raise WorkSessionError("invalid_idempotency_key")
    digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
    session_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    timestamp = time.time() if now is None else float(now)
    from core.agent_runtime.task_manager import TaskManagerError, get_task
    try:
        task = get_task(principal, task_id, now=timestamp)
    except TaskManagerError as exc:
        raise WorkSessionError("task_not_found") from exc
    if task["realm"] != "reality" or task["capability"] != capability:
        raise WorkSessionError("task_capability_mismatch")
    if task["status"] not in {"created", "queued", "running"}:
        raise WorkSessionError("task_terminal")
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, rows = _load(principal)
        for row in rows:
            if row.idempotency_digest == session_digest:
                if row.task_id != task_id or row.capability != capability or row.artifact_kind != artifact_kind:
                    raise WorkSessionError("idempotency_conflict")
                if row.context_digest != digest:
                    if row.status != "failed" or task["status"] != "queued":
                        raise WorkSessionError("idempotency_conflict")
                    # A safe explicit retry may rebuild fresher bounded input.
                    # Only its digest/size are retained; the worker still receives
                    # the exact in-memory context for this attempt.
                    row.context_digest = digest
                    row.context_chars = len(context)
                    row.updated_at = timestamp
                    _save(principal, state, rows)
                return _project(row)
        if sum(1 for row in rows if row.status not in _TERMINAL) >= MAX_SESSIONS_PER_SCOPE:
            raise WorkSessionError("work_session_capacity_exceeded")
        row = WorkSessionRecord(
            session_id=uuid.uuid4().hex,
            task_id=task_id,
            uid=principal.uid,
            char_id=principal.char_id,
            realm="reality",
            capability=capability,
            artifact_kind=artifact_kind,
            status="created",
            created_at=timestamp,
            updated_at=timestamp,
            context_digest=digest,
            context_chars=len(context),
            idempotency_digest=session_digest,
        )
        rows.append(row)
        _save(principal, state, rows)
        return _project(row)


def start_work_session(principal: TaskPrincipal, work_session_id: str, *, now: float | None = None) -> dict[str, Any]:
    principal = _validate_principal(principal)
    if not _SESSION_ID_RE.fullmatch(str(work_session_id or "")):
        raise WorkSessionError("invalid_work_session_id")
    timestamp = time.time() if now is None else float(now)
    from core.agent_runtime.task_manager import TaskManagerError, get_task
    try:
        task = get_task(principal, get_work_session(principal, work_session_id)["task_id"], now=timestamp)
    except TaskManagerError as exc:
        raise WorkSessionError("task_not_found") from exc
    if task["status"] != "running":
        raise WorkSessionError("task_not_running")
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, rows = _load(principal)
        row = next((item for item in rows if item.session_id == work_session_id), None)
        if row is None:
            raise WorkSessionError("work_session_not_found")
        if row.status != "created":
            raise WorkSessionError("work_session_terminal")
        row.status = "running"
        row.attempt_count += 1
        row.updated_at = timestamp
        _save(principal, state, rows)
        return _project(row)


def complete_work_session(principal: TaskPrincipal, work_session_id: str, *, artifact_id: str = "", artifact_version: int = 0, now: float | None = None) -> dict[str, Any]:
    if not artifact_id or not isinstance(artifact_version, int) or isinstance(artifact_version, bool) or artifact_version <= 0:
        raise WorkSessionError("artifact_not_created")
    return _finish(principal, work_session_id, "succeeded", artifact_id=artifact_id, artifact_version=artifact_version, now=now)


def fail_work_session(principal: TaskPrincipal, work_session_id: str, *, error_code: str = "work_session_failed", now: float | None = None) -> dict[str, Any]:
    error_code = _validate_name(error_code, "error_code")
    return _finish(principal, work_session_id, "failed", error_code=error_code, now=now)


def cancel_work_session(principal: TaskPrincipal, work_session_id: str, *, now: float | None = None) -> dict[str, Any]:
    return _finish(principal, work_session_id, "canceled", error_code="work_session_canceled", now=now)


def unknown_work_session(principal: TaskPrincipal, work_session_id: str, *, now: float | None = None) -> dict[str, Any]:
    return _finish(principal, work_session_id, "outcome_unknown", error_code="work_session_outcome_unknown", now=now)


def retry_work_session(principal: TaskPrincipal, work_session_id: str, *, now: float | None = None) -> dict[str, Any]:
    """Explicitly requeue a failed session; unknown/canceled sessions never replay."""
    principal = _validate_principal(principal)
    timestamp = time.time() if now is None else float(now)
    current = get_work_session(principal, work_session_id)
    from core.agent_runtime.task_manager import TaskManagerError, get_task
    try:
        task = get_task(principal, current["task_id"], now=timestamp)
    except TaskManagerError as exc:
        raise WorkSessionError("task_not_found") from exc
    if task["status"] != "queued":
        raise WorkSessionError("task_not_queued")
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, rows = _load(principal)
        row = next((item for item in rows if item.session_id == work_session_id), None)
        if row is None:
            raise WorkSessionError("work_session_not_found")
        if row.status != "failed":
            raise WorkSessionError("work_session_retry_forbidden")
        row.status = "created"
        row.updated_at = timestamp
        row.error_code = ""
        _save(principal, state, rows)
        return _project(row)


def _finish(principal: TaskPrincipal, work_session_id: str, status: str, *, artifact_id: str = "", artifact_version: int = 0, error_code: str = "", now: float | None = None) -> dict[str, Any]:
    principal = _validate_principal(principal)
    if not _SESSION_ID_RE.fullmatch(str(work_session_id or "")):
        raise WorkSessionError("invalid_work_session_id")
    if artifact_id and (len(artifact_id) > MAX_ARTIFACT_ID_CHARS or not _NAME_RE.fullmatch(artifact_id)):
        raise WorkSessionError("invalid_artifact_id")
    timestamp = time.time() if now is None else float(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, rows = _load(principal)
        row = next((item for item in rows if item.session_id == work_session_id), None)
        if row is None:
            raise WorkSessionError("work_session_not_found")
        if row.status in _TERMINAL:
            return _project(row)
        if status == "succeeded" and row.status != "running":
            raise WorkSessionError("work_session_not_running")
        row.status = status
        row.updated_at = timestamp
        row.error_code = error_code
        if artifact_id:
            row.artifact_id = artifact_id
        if artifact_version:
            row.artifact_version = int(artifact_version)
        _save(principal, state, rows)
        return _project(row)


def get_work_session(principal: TaskPrincipal, work_session_id: str) -> dict[str, Any]:
    principal = _validate_principal(principal)
    with task_store.scope_lock(principal.uid, principal.char_id):
        _, rows = _load(principal)
    row = next((item for item in rows if item.session_id == work_session_id), None)
    if row is None:
        raise WorkSessionError("work_session_not_found")
    return _project(row)


def list_work_sessions(principal: TaskPrincipal, *, limit: int = 50) -> list[dict[str, Any]]:
    principal = _validate_principal(principal)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise WorkSessionError("invalid_limit")
    with task_store.scope_lock(principal.uid, principal.char_id):
        _, rows = _load(principal)
    rows.sort(key=lambda item: (item.created_at, item.session_id), reverse=True)
    return [_project(row) for row in rows[:limit]]


def observability_snapshot(*, uid: str | None = None, char_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    from core.data_paths import safe_user_id
    if uid is not None:
        uid = safe_user_id(uid)
    if char_id is not None:
        char_id = safe_user_id(char_id)
    if not isinstance(limit, int) or not 1 <= limit <= 200:
        raise WorkSessionError("invalid_limit")
    entries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for path in get_paths().agent_runtime_work_sessions_root().glob("*/*/state.json"):
        try:
            scope = path.parts[-3:-1]
            if len(scope) != 2:
                continue
            scope_char, scope_uid = scope
            if uid is not None and scope_uid != uid:
                continue
            if char_id is not None and scope_char != char_id:
                continue
            principal = TaskPrincipal.reality(scope_uid, scope_char)
            for row in list_work_sessions(principal, limit=100):
                counts[row["status"]] = counts.get(row["status"], 0) + 1
                entries.append({**row, "scope": {"uid_digest": hashlib.sha256(scope_uid.encode()).hexdigest()[:16], "char_id": scope_char, "realm": "reality"}})
        except Exception:
            continue
    entries.sort(key=lambda item: (item["created_at"], item["work_session_id"]), reverse=True)
    return {"schema_version": "agent-runtime-work-session-observability.v1", "entries": entries[:limit], "count": min(len(entries), limit), "total_matching": len(entries), "status_counts": dict(sorted(counts.items())), "truncated": len(entries) > limit}


def recover_all_work_sessions(*, now: float | None = None) -> dict[str, int]:
    """Mark orphan running sessions unknown before scheduler workers start."""
    timestamp = time.time() if now is None else float(now)
    result = {"scopes": 0, "recovered": 0, "unreadable": 0}
    root = get_paths().agent_runtime_work_sessions_root()
    for path in root.glob("*/*/state.json"):
        parts = path.parts[-3:-1]
        if len(parts) != 2:
            continue
        char_id, uid = parts
        principal = TaskPrincipal.reality(uid, char_id)
        result["scopes"] += 1
        try:
            with task_store.scope_lock(uid, char_id):
                state, rows = _load(principal)
                changed = 0
                for row in rows:
                    if row.status == "running":
                        row.status = "outcome_unknown"
                        row.error_code = "worker_process_lost"
                        row.updated_at = timestamp
                        changed += 1
                if changed:
                    _save(principal, state, rows)
                    result["recovered"] += changed
        except Exception:
            result["unreadable"] += 1
    return result


async def run_work_session(
    principal: TaskPrincipal,
    work_session_id: str,
    worker: Callable[[], Awaitable[dict[str, Any] | None]],
) -> dict[str, Any]:
    """Run a bounded non-chat worker and terminalize its session only.

    The worker is responsible for using an approved artifact capability.  No
    turn sink, EventContext, or memory writer is reachable from this helper.
    """
    start_work_session(principal, work_session_id)
    try:
        result = await worker()
        result = result or {}
        if not isinstance(result, dict):
            raise WorkSessionError("invalid_worker_result")
        artifact_id = str(result.get("artifact_id") or "")
        artifact_version = result.get("artifact_version")
        if not artifact_id or not isinstance(artifact_version, int) or isinstance(artifact_version, bool) or artifact_version <= 0:
            raise WorkSessionError("artifact_not_created")
        return complete_work_session(
            principal,
            work_session_id,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
        )
    except Exception as exc:
        code = exc.code if isinstance(exc, WorkSessionError) else "worker_failed"
        fail_work_session(principal, work_session_id, error_code=code)
        if isinstance(exc, WorkSessionError):
            raise
        raise WorkSessionError(code) from exc
