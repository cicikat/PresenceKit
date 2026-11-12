"""Metadata-only durable idempotency receipts for the owner turn API."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

from core.safe_write import safe_write_json
from core.sandbox import get_paths

_RETENTION_SECONDS = 30 * 24 * 60 * 60
DESKTOP_ADMISSION_SECONDS = 24 * 60 * 60
DESKTOP_FUTURE_SKEW_SECONDS = 300
_MAX_RECEIPTS_PER_CALLER = 1000
_OBSERVABILITY_DEFAULT_LIMIT = 25
_OBSERVABILITY_MAX_LIMIT = 100
_VALID_STATUSES = frozenset({
    "running", "completed", "failed", "interrupted_unknown",
})
_CALLER_LABEL_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_CURSOR_SECRET = secrets.token_bytes(32)
_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()


@dataclass(frozen=True)
class ReceiptResult:
    kind: str
    receipt: dict[str, Any] | None = None


def request_hash(*, message: str, reply_to: dict | None, upload_ids: list[str]) -> str:
    payload = {"message": message, "reply_to": reply_to, "upload_ids": list(upload_ids)}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def receipt_key(client_turn_id: str) -> str:
    return hashlib.sha256(client_turn_id.encode("utf-8")).hexdigest()


def validate_caller_label(value: object) -> str:
    if not isinstance(value, str) or not _CALLER_LABEL_RE.fullmatch(value):
        raise ValueError("caller must be a valid token label")
    return value


async def lock_for(caller_label: str, client_turn_id: str) -> asyncio.Lock:
    key = (caller_label, receipt_key(client_turn_id))
    async with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, asyncio.Lock())


def _path(caller_label: str, client_turn_id: str):
    return get_paths().owner_turn_receipt(
        caller_label=caller_label,
        receipt_key=receipt_key(client_turn_id),
    )


def load(caller_label: str, client_turn_id: str, *, strict: bool = False) -> dict[str, Any] | None:
    path = _path(caller_label, client_turn_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        if strict:
            raise
        return None
    if strict and not isinstance(raw, dict):
        raise ValueError("invalid turn receipt")
    return raw if isinstance(raw, dict) else None


def write(
    *,
    caller_label: str,
    client_turn_id: str,
    request_digest: str,
    status: str,
    canonical_turn_id: str | None = None,
    error_code: str | None = None,
    created_at: float | None = None,
    canonical_char_id: str | None = None,
    transport_msg_id: str | None = None,
) -> dict[str, Any]:
    now = time.time()
    row = {
        "caller": caller_label,
        "request_hash": request_digest,
        "status": status,
        "canonical_turn_id": canonical_turn_id,
        "created_at": created_at if created_at is not None else now,
        "updated_at": now,
        "error_code": error_code,
        "client_turn_id": client_turn_id,
    }
    if canonical_char_id is not None:
        row["canonical_char_id"] = canonical_char_id
    if transport_msg_id is not None:
        row["transport_msg_id"] = transport_msg_id
    if not safe_write_json(_path(caller_label, client_turn_id), row):
        raise OSError("owner turn receipt write failed")
    return row


def projection(row: dict[str, Any]) -> dict[str, Any]:
    """Return only safe receipt fields for the status endpoint."""
    return {
        "status": row.get("status"),
        "client_turn_id": row.get("client_turn_id"),
        "canonical_turn_id": row.get("canonical_turn_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "error_code": row.get("error_code"),
    }


def observability_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded metadata projection safe for admin state.read."""
    return {
        "caller": row.get("caller"),
        "client_turn_id": row.get("client_turn_id"),
        "canonical_turn_id": row.get("canonical_turn_id"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "error_code": row.get("error_code"),
    }


def _mark_interrupted(path, row: dict[str, Any]) -> dict[str, Any]:
    """Atomically terminalize a receipt whose process-local task is gone."""
    if row.get("status") != "running":
        return row
    updated = dict(row)
    updated["status"] = "interrupted_unknown"
    updated["error_code"] = "execution_outcome_unknown"
    updated["updated_at"] = time.time()
    if safe_write_json(path, updated):
        return updated
    raise OSError("owner turn interrupted receipt write failed")


def recover_if_interrupted(
    caller_label: str,
    client_turn_id: str,
    *,
    is_inflight: bool,
) -> dict[str, Any] | None:
    """Recover one persisted running receipt when no live task owns it."""
    row = load(caller_label, client_turn_id)
    if row is None or row.get("status") != "running" or is_inflight:
        return row
    return _mark_interrupted(_path(caller_label, client_turn_id), row)


def _read_path(path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def _cursor_encode(sort_key: tuple[float, str, str, str]) -> str:
    payload = json.dumps(
        {"created_at": sort_key[0], "caller": sort_key[1], "client_turn_id": sort_key[2], "key": sort_key[3]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(_CURSOR_SECRET, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + b"." + signature).decode("ascii").rstrip("=")


def _cursor_decode(value: str) -> tuple[float, str, str, str]:
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("invalid owner-turn cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(decoded) < hashlib.sha256().digest_size + 2:
            raise ValueError
        payload = decoded[: -(hashlib.sha256().digest_size + 1)]
        separator = decoded[-(hashlib.sha256().digest_size + 1)]
        signature = decoded[-hashlib.sha256().digest_size :]
        if separator != ord("."):
            raise ValueError
        expected = hmac.new(_CURSOR_SECRET, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        data = json.loads(payload.decode("utf-8"))
        result = (
            float(data["created_at"]),
            validate_caller_label(data["caller"]),
            str(data["client_turn_id"]),
            str(data["key"]),
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError):
        raise ValueError("invalid owner-turn cursor") from None
    if not result[2] or not result[3] or len(result[2]) > 128 or len(result[3]) > 128:
        raise ValueError("invalid owner-turn cursor")
    return result


def _sort_key(caller: str, row: dict[str, Any], storage_key: str) -> tuple[float, str, str, str]:
    try:
        created_at = float(row.get("created_at") or 0)
    except (TypeError, ValueError):
        created_at = 0.0
    client_turn_id = str(row.get("client_turn_id") or "")
    return (-created_at, caller, client_turn_id, storage_key)


def list_receipts(
    *,
    status: str | None = None,
    caller: str | None = None,
    created_after: float | None = None,
    created_before: float | None = None,
    limit: int = _OBSERVABILITY_DEFAULT_LIMIT,
    cursor: str | None = None,
    is_inflight=None,
) -> dict[str, Any]:
    """List stable, redacted receipt metadata without exposing storage paths."""
    if status and status not in _VALID_STATUSES:
        raise ValueError("invalid owner-turn status")
    if caller:
        caller = validate_caller_label(caller)
    if not isinstance(limit, int) or not 1 <= limit <= _OBSERVABILITY_MAX_LIMIT:
        raise ValueError("invalid owner-turn limit")
    cursor_key = _cursor_decode(cursor) if cursor else None
    root = get_paths().owner_turn_receipts_root()
    rows: list[tuple[tuple[float, str, str, str], dict[str, Any]]] = []
    if not root.exists():
        return {"entries": [], "count": 0, "total": 0, "limit": limit, "next_cursor": None}
    try:
        caller_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError:
        caller_dirs = []
    for caller_dir in caller_dirs:
        try:
            caller_name = validate_caller_label(caller_dir.name)
        except ValueError:
            continue
        if caller and caller_name != caller:
            continue
        try:
            files = sorted(caller_dir.glob("*.json"), key=lambda path: path.name)
        except OSError:
            continue
        for path in files:
            row = _read_path(path)
            if row is None:
                continue
            if row.get("status") == "running":
                client_id = str(row.get("client_turn_id") or "")
                live = bool(is_inflight(caller_name, client_id)) if is_inflight and client_id else False
                if not live:
                    try:
                        row = _mark_interrupted(path, row)
                    except OSError:
                        continue
            if status and row.get("status") != status:
                continue
            try:
                created_at = float(row.get("created_at") or 0)
            except (TypeError, ValueError):
                continue
            if created_after is not None and created_at < created_after:
                continue
            if created_before is not None and created_at > created_before:
                continue
            key = _sort_key(caller_name, row, path.name)
            if cursor_key is not None and key <= cursor_key:
                continue
            rows.append((key, row))
    rows.sort(key=lambda item: item[0])
    total = len(rows)
    page = rows[:limit]
    next_cursor = _cursor_encode(page[-1][0]) if len(page) == limit and total > limit else None
    return {
        "entries": [observability_projection(row) for _, row in page],
        "count": len(page),
        "total": total,
        "limit": limit,
        "next_cursor": next_cursor,
    }


def summary(*, is_inflight=None) -> dict[str, int]:
    result = {status: 0 for status in _VALID_STATUSES}
    result["total"] = 0
    data = list_receipts(limit=_OBSERVABILITY_MAX_LIMIT, is_inflight=is_inflight)
    # list_receipts is paginated; scan all rows for the bounded status summary.
    cursor = data.get("next_cursor")
    while True:
        for row in data["entries"]:
            status = row.get("status")
            if status in result:
                result[status] += 1
            result["total"] += 1
        if not cursor:
            break
        data = list_receipts(limit=_OBSERVABILITY_MAX_LIMIT, cursor=cursor, is_inflight=is_inflight)
        cursor = data.get("next_cursor")
    return result


def prune(*, is_inflight=None) -> None:
    """Bound receipt retention without touching any user-authored data."""
    root = get_paths().owner_turn_receipts_root()
    if not root.exists():
        return
    cutoff = time.time() - _RETENTION_SECONDS
    desktop_cutoff = time.time() - DESKTOP_ADMISSION_SECONDS - DESKTOP_FUTURE_SKEW_SECONDS
    for caller_dir in root.iterdir():
        if not caller_dir.is_dir():
            continue
        try:
            files = sorted(
                caller_dir.glob("*.json"),
                key=lambda path: path.stat().st_mtime if path.exists() else 0,
                reverse=True,
            )
        except OSError:
            continue
        retained = []
        for path in files:
            row = _read_path(path)
            if not row:
                retained.append(path)
                continue
            client_id = str(row.get("client_turn_id") or "")
            if client_id.startswith("~desktop:") and row.get("created_at", 0) >= desktop_cutoff:
                retained.append(path)
            if row.get("status") != "running":
                continue
            live = bool(is_inflight(caller_dir.name, client_id)) if is_inflight and client_id else False
            if live:
                retained.append(path)
                continue
            try:
                _mark_interrupted(path, row)
            except OSError:
                retained.append(path)
        candidates = [path for path in files if path not in retained]
        for path in candidates[_MAX_RECEIPTS_PER_CALLER:]:
            try:
                path.unlink()
            except OSError:
                pass
        for path in candidates[:_MAX_RECEIPTS_PER_CALLER]:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
