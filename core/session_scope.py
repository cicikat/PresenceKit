"""Server-issued Reality session grants and bounded request receipts.

The client requests a character, but this module is the authority that validates
the character and binds it to the authenticated token label and configured owner.
No message body can override the resulting scope.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.memory.scope import MemoryScope

SESSION_SCOPE_VERSION = "v1"
SESSION_TTL_SECONDS = 24 * 60 * 60
REQUEST_RETENTION_SECONDS = 30 * 60
MAX_SESSIONS = 256
MAX_REQUESTS = 512
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class SessionScopeError(ValueError):
    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class SessionGrant:
    session_id: str
    token_label: str
    owner_id: str
    char_id: str
    domain: str
    created_at: float
    expires_at: float

    @property
    def memory_scope(self) -> MemoryScope:
        return MemoryScope.reality_scope(self.owner_id, self.char_id)

    def projection(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "owner_id": self.owner_id,
            "char_id": self.char_id,
            "domain": self.domain,
            "expires_at": self.expires_at,
        }


_SESSIONS: dict[str, SessionGrant] = {}
_REQUESTS: dict[tuple[str, str, str], dict[str, Any]] = {}
_REQUEST_LOCKS: dict[tuple[str, str, str], asyncio.Lock] = {}
_RUNNING: dict[tuple[str, str, str], asyncio.Task] = {}
_TRACE: deque[dict[str, Any]] = deque(maxlen=500)
_GUARD = asyncio.Lock()


def _owner_id() -> str:
    from core.config_loader import get_config

    owner = str(get_config().get("scheduler", {}).get("owner_id", "")).strip()
    if not owner:
        raise SessionScopeError(503, "owner_scope_unavailable")
    return owner


def _validate_character(char_id: object) -> str:
    if not isinstance(char_id, str) or not _OPAQUE_RE.fullmatch(char_id):
        raise SessionScopeError(422, "character_unavailable")
    try:
        from core.asset_registry import get_registry
        from core.character_loader import load

        entry = get_registry().resolve(char_id, "character")
        if entry.hidden:
            raise ValueError("hidden character")
        load(char_id)
    except Exception:
        raise SessionScopeError(404, "character_unavailable") from None
    return char_id


def _record(event: str, *, grant: SessionGrant | None = None, request_id: str = "", reason: str = "") -> None:
    _TRACE.append({
        "event": event,
        "timestamp": time.time(),
        "token_label": grant.token_label if grant else "",
        "session_id": grant.session_id if grant else "",
        "owner_id": grant.owner_id if grant else "",
        "char_id": grant.char_id if grant else "",
        "domain": grant.domain if grant else "",
        "request_id": request_id,
        "reason": reason,
    })


def _prune(now: float | None = None) -> None:
    current = time.time() if now is None else now
    expired = [key for key, grant in _SESSIONS.items() if grant.expires_at <= current]
    for key in expired:
        _SESSIONS.pop(key, None)
    stale = [
        key for key, row in _REQUESTS.items()
        if float(row.get("updated_at") or 0) + REQUEST_RETENTION_SECONDS <= current
        and key not in _RUNNING
    ]
    for key in stale:
        _REQUESTS.pop(key, None)
        _REQUEST_LOCKS.pop(key, None)
    if len(_SESSIONS) > MAX_SESSIONS:
        for grant in sorted(_SESSIONS.values(), key=lambda item: item.created_at)[: len(_SESSIONS) - MAX_SESSIONS]:
            _SESSIONS.pop(grant.session_id, None)
    if len(_REQUESTS) > MAX_REQUESTS:
        removable = sorted(
            ((key, row) for key, row in _REQUESTS.items() if key not in _RUNNING),
            key=lambda item: float(item[1].get("updated_at") or 0),
        )
        for key, _row in removable[: max(0, len(_REQUESTS) - MAX_REQUESTS)]:
            _REQUESTS.pop(key, None)
            _REQUEST_LOCKS.pop(key, None)


def create_session(*, token_label: str, char_id: object, domain: str = "reality") -> SessionGrant:
    if domain != "reality":
        raise SessionScopeError(422, "session_domain_unsupported")
    character = _validate_character(char_id)
    owner = _owner_id()
    now = time.time()
    _prune(now)
    grant = SessionGrant(
        session_id="pss_" + secrets.token_urlsafe(24),
        token_label=token_label,
        owner_id=owner,
        char_id=character,
        domain=domain,
        created_at=now,
        expires_at=now + SESSION_TTL_SECONDS,
    )
    _SESSIONS[grant.session_id] = grant
    _record("session_created", grant=grant)
    return grant


def resolve_session(session_id: object, *, token_label: str) -> SessionGrant:
    _prune()
    if not isinstance(session_id, str) or not _OPAQUE_RE.fullmatch(session_id):
        _record("session_rejected", reason="session_not_found")
        raise SessionScopeError(404, "session_not_found")
    grant = _SESSIONS.get(session_id)
    if grant is None or grant.expires_at <= time.time():
        _record("session_rejected", reason="session_not_found")
        raise SessionScopeError(404, "session_not_found")
    if not secrets.compare_digest(grant.token_label, token_label):
        _record("session_rejected", grant=grant, reason="character_not_authorized")
        raise SessionScopeError(403, "character_not_authorized")
    if grant.owner_id != _owner_id():
        _record("session_rejected", grant=grant, reason="character_revoked")
        raise SessionScopeError(403, "character_revoked")
    try:
        _validate_character(grant.char_id)
    except SessionScopeError:
        _record("session_rejected", grant=grant, reason="character_unavailable")
        raise
    return grant


def validate_request_id(value: object) -> str:
    if value in (None, ""):
        return "req_" + secrets.token_urlsafe(18)
    if not isinstance(value, str) or not _OPAQUE_RE.fullmatch(value):
        raise SessionScopeError(422, "invalid_request_id")
    return value


def request_digest(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def execute_request(
    *, grant: SessionGrant, request_id: object, payload: object,
    executor: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[str, dict[str, Any]]:
    rid = validate_request_id(request_id)
    key = (grant.token_label, grant.session_id, rid)
    digest = request_digest(payload)
    async with _GUARD:
        _prune()
        lock = _REQUEST_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        row = _REQUESTS.get(key)
        if row is not None:
            if row.get("digest") != digest:
                _record("request_rejected", grant=grant, request_id=rid, reason="request_payload_conflict")
                raise SessionScopeError(409, "request_payload_conflict")
            if row.get("status") == "completed":
                replay = dict(row["result"])
                replay["request_id"] = rid
                replay["session_id"] = grant.session_id
                replay["char_id"] = grant.char_id
                replay["domain"] = grant.domain
                return rid, replay
            if key in _RUNNING:
                raise SessionScopeError(202, "in_flight")
            raise SessionScopeError(503, "execution_outcome_unknown")
        now = time.time()
        _REQUESTS[key] = {"digest": digest, "status": "running", "created_at": now, "updated_at": now}
        async def _execute_and_store():
            try:
                result = await executor()
            except Exception as exc:
                async with lock:
                    row = _REQUESTS.get(key, {})
                    row.update(status="failed", updated_at=time.time(), error_code=type(exc).__name__)
                    _REQUESTS[key] = row
                    _RUNNING.pop(key, None)
                    _record("request_failed", grant=grant, request_id=rid, reason=type(exc).__name__)
                raise
            async with lock:
                _REQUESTS[key] = {
                    "digest": digest, "status": "completed",
                    "created_at": _REQUESTS[key]["created_at"],
                    "updated_at": time.time(), "result": dict(result),
                }
                _RUNNING.pop(key, None)
                _record("request_completed", grant=grant, request_id=rid)
            return result

        task = asyncio.create_task(_execute_and_store())
        _RUNNING[key] = task
        _record("request_started", grant=grant, request_id=rid)
    result = await asyncio.shield(task)
    result = dict(result)
    result["request_id"] = rid
    result["session_id"] = grant.session_id
    result["char_id"] = grant.char_id
    result["domain"] = grant.domain
    return rid, result


def observability_snapshot(*, limit: int = 100) -> dict[str, Any]:
    _prune()
    entries = list(_TRACE)[-max(1, min(limit, 500)):]
    entries.reverse()
    return {
        "capability": SESSION_SCOPE_VERSION,
        "effective": True,
        "session_ttl_seconds": SESSION_TTL_SECONDS,
        "request_retention_seconds": REQUEST_RETENTION_SECONDS,
        "active_sessions": len(_SESSIONS),
        "retained_requests": len(_REQUESTS),
        "entries": entries,
    }


def reset_for_tests() -> None:
    _SESSIONS.clear()
    _REQUESTS.clear()
    _REQUEST_LOCKS.clear()
    _RUNNING.clear()
    _TRACE.clear()
