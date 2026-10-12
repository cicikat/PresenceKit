"""Bounded, private server mirror for dated Obsidian diary entries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import date
from typing import Any

from core.safe_write import safe_write_json, safe_write_text
from core.sandbox import get_paths

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_BATCH_ENTRIES = 100
MAX_ENTRY_BYTES = 256 * 1024
MAX_BATCH_BYTES = 2 * 1024 * 1024
_LOCK = asyncio.Lock()


class DiarySyncError(ValueError):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


def _owner_id() -> str:
    from core.config_loader import get_config

    return str(get_config().get("scheduler", {}).get("owner_id", "owner"))


def _default_manifest() -> dict[str, Any]:
    return {"schema_version": 1, "active_generation": None, "entries": {}}


def _default_status() -> dict[str, Any]:
    return {
        "last_success_at": None,
        "active_generation": None,
        "entry_count": 0,
        "changed_count": 0,
        "tombstone_count": 0,
        "error_count": 0,
        "last_error_code": None,
    }


def _read_json(path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return dict(default)
    return data if isinstance(data, dict) else dict(default)


def _validate_generation(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        raise DiarySyncError("generation_invalid")
    return value


def _validate_date(value: object) -> str:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise DiarySyncError("date_invalid")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise DiarySyncError("date_invalid") from exc
    return value


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _revision_value(entry: dict[str, Any]) -> tuple[int, float | None, str]:
    raw = entry.get("revision", entry.get("mtime"))
    if isinstance(raw, bool):
        raise DiarySyncError("revision_invalid")
    if isinstance(raw, (int, float)):
        return 2, float(raw), str(raw)
    if isinstance(raw, str) and raw:
        try:
            return 2, float(raw), raw
        except ValueError:
            return 1, None, raw
    raise DiarySyncError("revision_invalid")


def _compare_revision(incoming: dict[str, Any], existing: dict[str, Any]) -> int | None:
    in_kind, in_number, in_text = _revision_value(incoming)
    ex_kind, ex_number, ex_text = _revision_value(existing)
    if in_kind == ex_kind == 2 and in_number is not None and ex_number is not None:
        return (in_number > ex_number) - (in_number < ex_number)
    if in_kind == ex_kind == 1:
        return (in_text > ex_text) - (in_text < ex_text)
    return None


def _validate_entry(raw: object) -> tuple[dict[str, Any], int]:
    if not isinstance(raw, dict):
        raise DiarySyncError("entry_invalid")
    logical_date = raw.get("logical_date", raw.get("date"))
    logical_date = _validate_date(logical_date)
    deleted = raw.get("deleted", False)
    if not isinstance(deleted, bool):
        raise DiarySyncError("deleted_invalid")
    content = raw.get("content", "")
    if not deleted and not isinstance(content, str):
        raise DiarySyncError("content_invalid")
    if deleted:
        content = ""
    encoded_size = len(content.encode("utf-8"))
    if encoded_size > MAX_ENTRY_BYTES:
        raise DiarySyncError("entry_too_large")
    digest = raw.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise DiarySyncError("sha256_invalid")
    if not deleted and digest.lower() != _hash(content):
        raise DiarySyncError("sha256_mismatch")
    _kind, _number, revision = _revision_value(raw)
    return {
        "logical_date": logical_date,
        "sha256": digest.lower(),
        "revision": revision,
        "mtime": raw.get("mtime"),
        "deleted": deleted,
        "content": content,
    }, encoded_size


def _entry_projection(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: entry.get(key)
        for key in ("logical_date", "sha256", "revision", "mtime", "deleted")
    }


def _status_from_manifest(manifest: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    entries = manifest.get("entries", {})
    active = [row for row in entries.values() if isinstance(row, dict) and not row.get("deleted")]
    tombstones = [row for row in entries.values() if isinstance(row, dict) and row.get("deleted")]
    return {
        "last_success_at": status.get("last_success_at"),
        "active_generation": manifest.get("active_generation"),
        "entry_count": len(active),
        "changed_count": int(status.get("changed_count") or 0),
        "tombstone_count": len(tombstones),
        "error_count": int(status.get("error_count") or 0),
        "last_error_code": status.get("last_error_code"),
    }


async def apply_batch(*, generation: str, entries: list[dict[str, Any]], char_id: str | None = None) -> dict[str, Any]:
    from core.prompt_asset_selection import active_character
    from core.data_paths import safe_user_id
    char_id = safe_user_id(char_id) if char_id else active_character()
    generation = _validate_generation(generation)
    if not isinstance(entries, list) or not entries or len(entries) > MAX_BATCH_ENTRIES:
        raise DiarySyncError("batch_limit")
    normalized: list[dict[str, Any]] = []
    total_bytes = 0
    for raw in entries:
        item, size = _validate_entry(raw)
        normalized.append(item)
        total_bytes += size
    if total_bytes > MAX_BATCH_BYTES:
        raise DiarySyncError("batch_too_large")

    owner_id = _owner_id()
    async with _LOCK:
        paths = get_paths()
        root = paths.diary_mirror_root(owner_id=owner_id, char_id=char_id)
        manifest = _read_json(paths.diary_mirror_manifest(owner_id=owner_id, char_id=char_id), _default_manifest())
        manifest.setdefault("entries", {})
        status = _read_json(paths.diary_mirror_status(owner_id=owner_id, char_id=char_id), _default_status())
        changed = 0
        results: list[dict[str, Any]] = []
        try:
            for item in normalized:
                logical_date = item["logical_date"]
                existing = manifest["entries"].get(logical_date)
                if existing and item["sha256"] == existing.get("sha256") and item["deleted"] == bool(existing.get("deleted")):
                    results.append({"logical_date": logical_date, "status": "idempotent"})
                    continue
                if existing:
                    comparison = _compare_revision(item, existing)
                    if comparison is not None and comparison < 0:
                        results.append({"logical_date": logical_date, "status": "stale_revision"})
                        continue
                    if comparison == 0:
                        results.append({"logical_date": logical_date, "status": "conflict"})
                        continue
                if not item["deleted"]:
                    path = paths.diary_mirror_entry(owner_id=owner_id, logical_date=logical_date, char_id=char_id)
                    if not safe_write_text(path, item["content"]):
                        raise DiarySyncError("mirror_write_failed")
                manifest["entries"][logical_date] = _entry_projection(item)
                changed += 1
                results.append({"logical_date": logical_date, "status": "applied"})
            manifest["active_generation"] = generation
            if not safe_write_json(paths.diary_mirror_manifest(owner_id=owner_id, char_id=char_id), manifest):
                raise DiarySyncError("mirror_write_failed")
            status["last_success_at"] = time.time()
            status["active_generation"] = generation
            status["changed_count"] = int(status.get("changed_count") or 0) + changed
            status["last_error_code"] = None
            if not safe_write_json(paths.diary_mirror_status(owner_id=owner_id, char_id=char_id), status):
                raise DiarySyncError("mirror_write_failed")
        except DiarySyncError as exc:
            status["error_count"] = int(status.get("error_count") or 0) + 1
            status["last_error_code"] = exc.code
            safe_write_json(paths.diary_mirror_status(owner_id=owner_id, char_id=char_id), status)
            raise
        return {"generation": generation, "changed": changed, "entries": results, "status": _status_from_manifest(manifest, status)}


def status(*, char_id: str | None = None) -> dict[str, Any]:
    owner_id = _owner_id()
    paths = get_paths()
    manifest = _read_json(paths.diary_mirror_manifest(owner_id=owner_id, char_id=char_id), _default_manifest())
    saved = _read_json(paths.diary_mirror_status(owner_id=owner_id, char_id=char_id), _default_status())
    return _status_from_manifest(manifest, saved)


def sync_state(*, char_id: str | None = None) -> str:
    """Return a bounded read-side state for tool framing and reminders."""
    return "never_synced" if status(char_id=char_id).get("active_generation") is None else "synced"


def read_entry(target_date: date, *, char_id: str | None = None) -> str:
    owner_id = _owner_id()
    paths = get_paths()
    manifest = _read_json(paths.diary_mirror_manifest(owner_id=owner_id, char_id=char_id), _default_manifest())
    entry = (manifest.get("entries") or {}).get(target_date.isoformat())
    if not isinstance(entry, dict) or entry.get("deleted"):
        return ""
    try:
        return paths.diary_mirror_entry(owner_id=owner_id, logical_date=target_date.isoformat(), char_id=char_id).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError, UnicodeError):
        return ""


def has_any_entry(*, char_id: str | None = None) -> bool:
    owner_id = _owner_id()
    manifest = _read_json(get_paths().diary_mirror_manifest(owner_id=owner_id, char_id=char_id), _default_manifest())
    return any(isinstance(row, dict) and not row.get("deleted") for row in (manifest.get("entries") or {}).values())
