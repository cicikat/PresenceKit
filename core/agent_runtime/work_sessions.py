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
_ARTIFACT_KINDS = frozenset({"authored_diary", "document_summary", "workspace_artifact"})
_TERMINAL = frozenset({"succeeded", "failed", "canceled", "outcome_unknown"})
MAX_CONTEXT_CHARS = 12000
MAX_ARTIFACT_ID_CHARS = 128


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
    return raw, rows


def _save(principal: TaskPrincipal, state: dict[str, Any], rows: list[WorkSessionRecord]) -> None:
    state["sessions"] = [row.to_dict() for row in rows[-100:]]
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
    if artifact_kind not in _ARTIFACT_KINDS:
        raise WorkSessionError("artifact_kind_forbidden")
    if not isinstance(context, str) or len(context) > MAX_CONTEXT_CHARS:
        raise WorkSessionError("context_limit_exceeded")
    if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 256:
        raise WorkSessionError("invalid_idempotency_key")
    digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
    session_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    timestamp = time.time() if now is None else float(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, rows = _load(principal)
        for row in rows:
            if row.idempotency_digest == session_digest:
                if row.task_id != task_id or row.capability != capability or row.context_digest != digest:
                    raise WorkSessionError("idempotency_conflict")
                return _project(row)
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
            artifact_id=f"ws-{session_digest[:24]}",
        )
        rows.append(row)
        _save(principal, state, rows)
        return _project(row)


def start_work_session(principal: TaskPrincipal, work_session_id: str, *, now: float | None = None) -> dict[str, Any]:
    principal = _validate_principal(principal)
    if not _SESSION_ID_RE.fullmatch(str(work_session_id or "")):
        raise WorkSessionError("invalid_work_session_id")
    timestamp = time.time() if now is None else float(now)
    with task_store.scope_lock(principal.uid, principal.char_id):
        state, rows = _load(principal)
        row = next((item for item in rows if item.session_id == work_session_id), None)
        if row is None:
            raise WorkSessionError("work_session_not_found")
        if row.status in _TERMINAL:
            raise WorkSessionError("work_session_terminal")
        row.status = "running"
        row.attempt_count += 1
        row.updated_at = timestamp
        _save(principal, state, rows)
        return _project(row)


def complete_work_session(principal: TaskPrincipal, work_session_id: str, *, artifact_id: str = "", artifact_version: int = 0, now: float | None = None) -> dict[str, Any]:
    return _finish(principal, work_session_id, "succeeded", artifact_id=artifact_id, artifact_version=artifact_version, now=now)


def fail_work_session(principal: TaskPrincipal, work_session_id: str, *, error_code: str = "work_session_failed", now: float | None = None) -> dict[str, Any]:
    error_code = _validate_name(error_code, "error_code")
    return _finish(principal, work_session_id, "failed", error_code=error_code, now=now)


def cancel_work_session(principal: TaskPrincipal, work_session_id: str, *, now: float | None = None) -> dict[str, Any]:
    return _finish(principal, work_session_id, "canceled", error_code="work_session_canceled", now=now)


def unknown_work_session(principal: TaskPrincipal, work_session_id: str, *, now: float | None = None) -> dict[str, Any]:
    return _finish(principal, work_session_id, "outcome_unknown", error_code="work_session_outcome_unknown", now=now)


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
    for path in get_paths().root_dir().joinpath("runtime", "agent_runtime", "reality", "work_sessions").glob("*/*/state.json"):
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
        return complete_work_session(
            principal,
            work_session_id,
            artifact_id=str(result.get("artifact_id") or ""),
            artifact_version=int(result.get("artifact_version") or 0),
        )
    except WorkSessionError:
        raise
    except Exception as exc:
        fail_work_session(principal, work_session_id, error_code="worker_failed")
        raise WorkSessionError("worker_failed") from exc
