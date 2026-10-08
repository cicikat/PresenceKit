"""Scoped, non-memory storage for material a character may look up on demand."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import logging
import threading
from pathlib import Path
from typing import Literal

from core.safe_write import safe_write_bytes, safe_write_json
from core.sandbox import get_paths, safe_user_id

logger = logging.getLogger(__name__)

_SEARCH_TEXT_CAP = 500_000
_READ_CAP = 2_000  # compatibility default for direct callers
_RESULT_CAP = 8
_locks: dict[tuple[str, str], threading.RLock] = {}
_locks_guard = threading.Lock()


def scope_lock(uid: str, char_id: str):
    with _locks_guard:
        return _locks.setdefault((safe_user_id(uid), safe_user_id(char_id)), threading.RLock())


def _query_terms(query: str) -> list[str]:
    value = str(query or "").strip().casefold()
    if not value:
        return []
    terms = {part for part in value.split() if part}
    if len(terms) == 1 and " " not in value:
        for length in (2, 3, 4):
            terms.update(value[index:index + length] for index in range(len(value) - length + 1))
    return sorted(terms, key=len, reverse=True)


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    uid: str
    char_id: str
    source: Literal["upload_file", "upload_image", "character_note"]
    created_at: str
    filename: str
    media_type: str
    sha256: str
    summary: str
    searchable_text: str
    size_bytes: int
    raw_retained: bool = False
    deleted_at: str = ""
    text_revision: str = ""
    sections: list[dict] = field(default_factory=list)
    overview: dict = field(default_factory=dict)
    reading: dict = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config() -> dict:
    from core.config_loader import get_config
    value = get_config().get("character_document_library", {})
    return value if isinstance(value, dict) else {}


def _load(uid: str, char_id: str) -> list[dict]:
    with scope_lock(uid, char_id):
        return _load_locked(uid, char_id)


def _load_locked(uid: str, char_id: str) -> list[dict]:
    path = get_paths().character_document_index(uid, char_id=char_id)
    try:
        data = __import__("json").loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("[character_library] index read failed uid=%s char=%s: %s", uid, char_id, exc)
        _record_failure(uid, char_id, "index_read")
        return []


def _write(uid: str, char_id: str, rows: list[dict]) -> bool:
    path = get_paths().character_document_index(uid, char_id=char_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return safe_write_json(path, rows)


def _record_failure(uid: str, char_id: str, reason: str) -> None:
    path = get_paths().character_document_stats(uid, char_id=char_id)
    try:
        import json
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        failures = previous.get("failures", {}) if isinstance(previous.get("failures"), dict) else {}
        failures[reason] = int(failures.get(reason, 0)) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_write_json(path, {"failures": failures, "updated_at": _now()})
    except Exception:
        logger.debug("[character_library] failed to record failure", exc_info=True)


def record_failure(*, uid: str, char_id: str, reason: str) -> None:
    """Record import failure telemetry without making media ingestion fail closed."""
    try:
        _record_failure(safe_user_id(uid), safe_user_id(char_id), str(reason)[:64] or "unknown")
    except Exception:
        logger.debug("[character_library] failure telemetry unavailable", exc_info=True)


def _summary(text: str) -> str:
    clean = " ".join(str(text or "").split())
    return clean[:280] + ("..." if len(clean) > 280 else "")


def store_upload(
    *, uid: str, char_id: str, filename: str, media_type: str,
    sha256: str, searchable_text: str,
    source: Literal["upload_file", "upload_image", "character_note"],
    raw_bytes: bytes | None = None,
) -> str | None:
    """Store a bounded derived representation; raw bytes require explicit opt-in."""
    uid, char_id = safe_user_id(uid), safe_user_id(char_id)
    name = Path(filename or source).name or source
    text = str(searchable_text or "")
    if len(text) > _SEARCH_TEXT_CAP:
        _record_failure(uid, char_id, "derived_text_too_large")
        return None
    if not text:
        _record_failure(uid, char_id, "empty_derived_text")
        return None
    digest = sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest()
    document_id = f"doc_{digest[:24]}"
    retain_raw = bool(_config().get("retain_raw_uploads", False)) and raw_bytes is not None
    from core.document_reading import revision, sections
    record = DocumentRecord(
        document_id=document_id, uid=uid, char_id=char_id, source=source,
        created_at=_now(), filename=name, media_type=str(media_type or "application/octet-stream")[:128],
        sha256=digest, summary=_summary(text), searchable_text=text,
        size_bytes=len(raw_bytes) if raw_bytes is not None else len(text.encode("utf-8")),
        raw_retained=retain_raw,
        text_revision=revision(text), sections=sections(text),
        overview={"status": "pending", "covered_chars": 0, "text": "", "parts": []} if source == "upload_file" else {},
    )
    try:
        with scope_lock(uid, char_id):
            rows = _load(uid, char_id)
            previous = next((row for row in rows if row.get("document_id") == document_id), None)
            payload = asdict(record)
            if previous and not previous.get("deleted_at") and previous.get("searchable_text") == text:
                for key in ("created_at", "overview", "reading"):
                    if key in previous:
                        payload[key] = previous[key]
            rows = [row for row in rows if str(row.get("document_id")) != document_id]
            rows.append(payload)
            if not _write(uid, char_id, rows):
                _record_failure(uid, char_id, "index_write")
                return None
        if retain_raw:
            blob = get_paths().character_document_blob_dir(uid, char_id=char_id) / document_id
            blob.parent.mkdir(parents=True, exist_ok=True)
            if not safe_write_bytes(blob, raw_bytes or b""):
                _record_failure(uid, char_id, "raw_write")
            else:
                try:
                    from core.chat_media import invalidate_live_media_cache
                    invalidate_live_media_cache()
                except Exception:
                    pass
        return document_id
    except Exception as exc:
        logger.warning("[character_library] store failed uid=%s char=%s: %s", uid, char_id, exc)
        _record_failure(uid, char_id, "store")
        return None


def search(
    uid: str, char_id: str, query: str = "", *, media_type: str = "", source: str = "", sha256: str = "",
) -> list[dict]:
    uid, char_id = safe_user_id(uid), safe_user_id(char_id)
    terms = _query_terms(query)
    results = []
    for row in _load(uid, char_id):
        if row.get("deleted_at") or row.get("uid") != uid or row.get("char_id") != char_id:
            continue
        if media_type and str(row.get("media_type")) != media_type:
            continue
        if source and str(row.get("source")) != source:
            continue
        if sha256 and str(row.get("sha256")) != sha256:
            continue
        haystack = " ".join(str(row.get(key) or "") for key in ("filename", "summary", "searchable_text")).casefold()
        if terms and not all(term in haystack for term in terms):
            continue
        results.append({key: row.get(key) for key in (
            "document_id", "source", "created_at", "filename", "media_type", "sha256", "summary", "size_bytes", "raw_retained",
        )})
        results[-1].update(total_chars=len(str(row.get("searchable_text") or "")),
                           overview_status=(row.get("overview") or {}).get("status", "pending"))
    return sorted(results, key=lambda row: str(row.get("created_at") or ""), reverse=True)[:_RESULT_CAP]


def get_record(uid: str, char_id: str, document_id: str) -> dict | None:
    uid, char_id = safe_user_id(uid), safe_user_id(char_id)
    return next((row for row in _load(uid, char_id) if row.get("document_id") == document_id
                 and row.get("uid") == uid and row.get("char_id") == char_id and not row.get("deleted_at")), None)


def update_derived(uid: str, char_id: str, document_id: str, text_revision: str, **changes) -> bool:
    """Never revive a tombstone or overwrite derived state for a changed text."""
    from core.document_reading import revision
    with scope_lock(uid, char_id):
        rows = _load(uid, char_id)
        for row in rows:
            if (row.get("document_id") == document_id and row.get("uid") == uid
                    and row.get("char_id") == char_id and not row.get("deleted_at")
                    and revision(str(row.get("searchable_text") or "")) == text_revision):
                row.update({key: value for key, value in changes.items() if key in {"overview", "reading"}})
                return _write(uid, char_id, rows)
    return False


def record_provided(uid: str, char_id: str, document_id: str, start: int, end: int) -> bool:
    from core.document_reading import merged_ranges, revision
    with scope_lock(uid, char_id):
        row = get_record(uid, char_id, document_id)
        if not row:
            return False
        state = dict(row.get("reading") or {})
        state.update(provided_ranges=merged_ranges(state.get("provided_ranges", []), start, end), updated_at=_now())
        return update_derived(uid, char_id, document_id, revision(row["searchable_text"]), reading=state)


def pending_overviews(uid: str, char_id: str) -> list[dict]:
    rows = [row for row in _load(uid, char_id) if row.get("uid") == uid and row.get("char_id") == char_id
            and not row.get("deleted_at") and row.get("source") == "upload_file"
            and (row.get("overview") or {}).get("status") != "ready"][-20:]
    return sorted(rows, key=lambda row: (row.get("overview") or {}).get("last_attempt_at", 0))


def read(uid: str, char_id: str, document_id: str, *, offset: int = 0, mode: str = "context", query: str = "", limit: int = _READ_CAP) -> dict | None:
    uid, char_id = safe_user_id(uid), safe_user_id(char_id)
    if not document_id.startswith("doc_"):
        return None
    for row in _load(uid, char_id):
        if row.get("document_id") != document_id or row.get("deleted_at"):
            continue
        if row.get("uid") != uid or row.get("char_id") != char_id:
            return None
        start = max(0, int(offset or 0))
        text = str(row.get("searchable_text") or "")
        if mode not in {"summary", "context", "overview", "continue"}:
            raise ValueError("invalid_document_read_mode")
        from core.document_reading import PAGE_CHARS, progress, sections
        size = max(1, min(int(limit), PAGE_CHARS))
        state = progress(row)
        if mode == "continue":
            start = state["next_unread_offset"]
        start = min(start, len(text))
        headings = row.get("sections") or sections(text)
        overview = row.get("overview") or {}
        if query and mode == "context":
            match = text.casefold().find(query.casefold(), start)
            if match < 0:
                return {"document_id": document_id, "filename": row.get("filename"), "content": "未找到指定文字。", "next_offset": None}
            start = max(start, match - 400)
        content = (str(overview.get("text") or row.get("summary") or "") if mode == "summary" else
                   "" if mode == "overview" else text[start:start + size])
        current = next((heading["title"] for heading in reversed(headings) if heading["offset"] <= start), "正文起始")
        return {
            "document_id": document_id, "filename": row.get("filename"), "media_type": row.get("media_type"),
            "content": content, "created_at": row.get("created_at"), "mode": mode,
            "total_chars": len(text), "offset": start,
            "end_offset": start + len(content) if mode in {"context", "continue"} else start,
            "next_offset": start + len(content) if mode in {"context", "continue"} and len(text) > start + len(content) else None,
            "section": current, "line": text.count("\n", 0, start) + 1,
            "sections": headings[offset:offset + 10] if mode == "overview" else [],
            "next_section_offset": offset + 10 if mode == "overview" and len(headings) > offset + 10 else None,
            "section_count": len(headings), "overview_status": overview.get("status", "pending"),
            "overview_covered_chars": overview.get("covered_chars", 0),
            "progress": state,
        }
    return None


def candidates(uid: str, char_id: str) -> list[dict]:
    """Bounded current material metadata; tombstones and character notes stay out."""
    uid, char_id = safe_user_id(uid), safe_user_id(char_id)
    rows = [row for row in _load(uid, char_id) if row.get('uid') == uid and row.get('char_id') == char_id
            and not row.get('deleted_at') and row.get('source') in {'upload_file', 'upload_image'}]
    from core.document_reading import progress
    return [{**{key: row.get(key) for key in ('document_id', 'sha256', 'filename', 'source', 'created_at', 'summary')},
             "overview": {key: (row.get("overview") or {}).get(key) for key in ("status", "text", "covered_chars")},
             "total_chars": len(str(row.get("searchable_text") or "")), "progress": progress(row)}
            for row in sorted(rows, key=lambda row: str(row.get('created_at') or ''), reverse=True)[:100]]


def delete(uid: str, char_id: str, document_id: str) -> bool:
    with scope_lock(uid, char_id):
        return _delete_locked(uid, char_id, document_id)


def _delete_locked(uid: str, char_id: str, document_id: str) -> bool:
    uid, char_id = safe_user_id(uid), safe_user_id(char_id)
    rows = _load(uid, char_id)
    changed = False
    for row in rows:
        if row.get("document_id") == document_id and row.get("uid") == uid and row.get("char_id") == char_id and not row.get("deleted_at"):
            row["deleted_at"] = _now()
            changed = True
            blob = get_paths().character_document_blob_dir(uid, char_id=char_id) / document_id
            if blob.exists():
                try:
                    blob.unlink()
                except OSError:
                    _record_failure(uid, char_id, "raw_delete")
    if not changed:
        return False
    written = _write(uid, char_id, rows)
    if written:
        try:
            from core.chat_media import invalidate_live_media_cache
            invalidate_live_media_cache()
        except Exception:
            pass
    return written


def observability(uid: str, char_id: str) -> dict:
    uid, char_id = safe_user_id(uid), safe_user_id(char_id)
    rows = _load(uid, char_id)
    active = [row for row in rows if not row.get("deleted_at")]
    deleted = len(rows) - len(active)
    by_source: dict[str, int] = {}
    by_retention = {"raw_retained": 0, "derived_only": 0}
    for row in active:
        source = str(row.get("source") or "unknown")
        by_source[source] = by_source.get(source, 0) + 1
        by_retention["raw_retained" if row.get("raw_retained") else "derived_only"] += 1
    stats_path = get_paths().character_document_stats(uid, char_id=char_id)
    try:
        import json
        failures = (json.loads(stats_path.read_text(encoding="utf-8")) or {}).get("failures", {})
    except Exception:
        failures = {}
    from core.document_reading import progress
    documents = [{"document_id": row.get("document_id"), "total_chars": len(str(row.get("searchable_text") or "")),
                  "overview_status": (row.get("overview") or {}).get("status", "pending"),
                  "overview_covered_chars": (row.get("overview") or {}).get("covered_chars", 0),
                  **progress(row)} for row in active if row.get("source") == "upload_file"][-50:]
    return {"scope": {"uid": uid, "char_id": char_id, "realm": "reality"}, "count": len(active), "deleted": deleted,
            "documents": documents,
            "by_source": by_source, "retention": by_retention, "failures": failures if isinstance(failures, dict) else {}}
