"""Canonical chat-media identity, live-ref GC, and authenticated byte lookup.

Media identity is sha256. HTTP payloads never expose disk paths. GC may only
delete inbox / image_cache files that are not still referenced by event
media_refs or a retained character-library blob.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from core.data_paths import safe_user_id
from core.sandbox import get_paths

logger = logging.getLogger(__name__)

_SHA256_RE = __import__("re").compile(r"^[0-9a-f]{64}$")
_LIVE_REF_CACHE: dict[str, set[str]] | None = None
_LIVE_REF_LOCK = threading.Lock()

IRRECOVERABLE_DETAIL = "媒体文件已不可恢复"


class ChatMediaError(ValueError):
    """Stable, content-free media lookup failure."""

    def __init__(self, code: str, status_code: int = 404, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.detail = detail or code


@dataclass(frozen=True)
class ChatMediaRecord:
    sha256: str
    filename: str
    mime: str
    path: Path
    source: str
    size: int
    uid: str
    char_id: str


def validate_sha256(value: str) -> str:
    digest = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ChatMediaError("invalid_sha256", 422, "媒体指纹不合法")
    return digest


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mime_for(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
        ".bmp": "image/bmp",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(suffix, "application/octet-stream")


def _safe_filename(raw: str, digest: str) -> str:
    name = Path(str(raw or "").strip()).name
    if not name or name in {".", ".."}:
        return f"{digest[:12]}.bin"
    return name[:180]


def _assert_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _inbox_file_for(digest: str) -> Path | None:
    inbox = get_paths().inbox_dir()
    if not inbox.exists():
        return None
    for path in inbox.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        try:
            if _hash_bytes(path.read_bytes()) == digest:
                return path
        except OSError:
            continue
    return None


def _cache_payload(digest: str) -> dict | None:
    path = get_paths().image_cache_dir() / f"{digest}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _inbox_from_cache(digest: str) -> Path | None:
    payload = _cache_payload(digest)
    if not payload:
        return None
    raw = Path(str(payload.get("image_path") or ""))
    if not raw:
        return None
    inbox = get_paths().inbox_dir()
    if not _assert_within(inbox, raw) or not raw.is_file() or raw.is_symlink():
        return None
    try:
        if _hash_bytes(raw.read_bytes()) != digest:
            return None
    except OSError:
        return None
    return raw


def _library_blob_for(digest: str, *, uid: str, char_id: str) -> tuple[Path, dict] | None:
    from core.character_document_library import search

    for row in search(uid, char_id, sha256=digest):
        if not row.get("raw_retained"):
            continue
        document_id = str(row.get("document_id") or "")
        if not document_id.startswith("doc_"):
            continue
        blob = get_paths().character_document_blob_dir(uid, char_id=char_id) / document_id
        if not blob.is_file() or blob.is_symlink():
            continue
        try:
            if _hash_bytes(blob.read_bytes()) != digest:
                continue
        except OSError:
            continue
        return blob, row
    return None


def resolve_chat_media(sha256: str, *, uid: str, char_id: str) -> ChatMediaRecord:
    """Locate original bytes for one owner+character without exposing a URL."""
    digest = validate_sha256(sha256)
    try:
        uid = safe_user_id(uid)
        char_id = safe_user_id(char_id)
    except ValueError as exc:
        raise ChatMediaError("invalid_scope", 422, "用户或角色标识不合法") from exc

    blob = _library_blob_for(digest, uid=uid, char_id=char_id)
    if blob is not None:
        path, row = blob
        filename = _safe_filename(str(row.get("filename") or ""), digest)
        return ChatMediaRecord(
            sha256=digest,
            filename=filename,
            mime=str(row.get("media_type") or _mime_for(filename)),
            path=path,
            source="character_library",
            size=path.stat().st_size,
            uid=uid,
            char_id=char_id,
        )

    path = _inbox_from_cache(digest) or _inbox_file_for(digest)
    if path is None:
        raise ChatMediaError("gone", 410, IRRECOVERABLE_DETAIL)
    payload = _cache_payload(digest) or {}
    filename = _safe_filename(str(payload.get("source_filename") or path.name), digest)
    return ChatMediaRecord(
        sha256=digest,
        filename=filename,
        mime=_mime_for(filename),
        path=path,
        source="inbox",
        size=path.stat().st_size,
        uid=uid,
        char_id=char_id,
    )


def _scan_ledger_digests(path: Path) -> set[str]:
    digests: set[str] = set()
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=0.25,
            check_same_thread=False,
        )
        connection.execute("PRAGMA query_only=ON")
        try:
            rows = connection.execute(
                "SELECT media_refs_json FROM events WHERE redaction_state != 'tombstoned'"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return digests
    for (raw,) in rows:
        if isinstance(raw, list):
            value = raw
        else:
            try:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", "replace")
                value = json.loads(str(raw or "[]"))
            except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                digests.add(validate_sha256(str(item.get("sha256") or "")))
            except ChatMediaError:
                continue
    return digests


def _scan_library_digests() -> set[str]:
    digests: set[str] = set()
    root = get_paths()._p("runtime", "character_library")
    if not root.exists():
        return digests
    try:
        character_dirs = sorted(root.iterdir())
    except OSError:
        return digests
    for char_dir in character_dirs:
        if not char_dir.is_dir() or char_dir.is_symlink():
            continue
        try:
            user_dirs = sorted(char_dir.iterdir())
        except OSError:
            continue
        for user_dir in user_dirs:
            if not user_dir.is_dir() or user_dir.is_symlink():
                continue
            index = user_dir / "index.json"
            if not index.is_file():
                continue
            try:
                rows = json.loads(index.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or row.get("deleted_at"):
                    continue
                if not row.get("raw_retained"):
                    continue
                try:
                    digests.add(validate_sha256(str(row.get("sha256") or "")))
                except ChatMediaError:
                    continue
    return digests


def live_media_digests(*, refresh: bool = False) -> set[str]:
    """sha256 values still referenced by events or retained library blobs."""
    global _LIVE_REF_CACHE
    with _LIVE_REF_LOCK:
        if _LIVE_REF_CACHE is not None and not refresh:
            return set(_LIVE_REF_CACHE.get("digests") or ())
        digests = set(_scan_library_digests())
        root = get_paths().memory_char_root().parent
        try:
            character_dirs = sorted(root.iterdir()) if root.exists() else []
        except OSError:
            character_dirs = []
        for char_dir in character_dirs:
            if not char_dir.is_dir() or char_dir.is_symlink():
                continue
            try:
                user_dirs = sorted(char_dir.iterdir())
            except OSError:
                continue
            for user_dir in user_dirs:
                if not user_dir.is_dir() or user_dir.is_symlink():
                    continue
                ledger = user_dir / "event_store.sqlite3"
                if ledger.is_file() and not ledger.is_symlink():
                    digests.update(_scan_ledger_digests(ledger))
        _LIVE_REF_CACHE = {"digests": set(digests)}
        return set(digests)


def invalidate_live_media_cache() -> None:
    global _LIVE_REF_CACHE
    with _LIVE_REF_LOCK:
        _LIVE_REF_CACHE = None


def is_live_media_digest(digest: str) -> bool:
    try:
        return validate_sha256(digest) in live_media_digests()
    except ChatMediaError:
        return False


def inbox_file_digest(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return _hash_bytes(path.read_bytes())
    except OSError:
        return None


def cache_entry_digest(path: Path) -> str | None:
    stem = path.stem.lower()
    if _SHA256_RE.fullmatch(stem):
        return stem
    return None


def observability_snapshot(*, uid: str = "", char_id: str = "") -> dict:
    """Counts and retention only. No bodies, paths, or filenames."""
    from core.config_loader import get_config

    retention = get_config().get("retention", {}) if isinstance(get_config().get("retention"), dict) else {}
    inbox_cfg = retention.get("inbox", {}) if isinstance(retention.get("inbox"), dict) else {}
    cache_cfg = retention.get("image_cache", {}) if isinstance(retention.get("image_cache"), dict) else {}
    live = live_media_digests()
    inbox_dir = get_paths().inbox_dir()
    cache_dir = get_paths().image_cache_dir()
    inbox_files = [p for p in inbox_dir.iterdir() if p.is_file()] if inbox_dir.exists() else []
    cache_files = list(cache_dir.glob("*.json")) if cache_dir.exists() else []
    live_inbox = 0
    for path in inbox_files:
        digest = inbox_file_digest(path)
        if digest and digest in live:
            live_inbox += 1
    live_cache = 0
    for path in cache_files:
        digest = cache_entry_digest(path)
        if digest and digest in live:
            live_cache += 1
    recoverable = False
    if uid and char_id:
        try:
            uid = safe_user_id(uid)
            char_id = safe_user_id(char_id)
            from core.character_document_library import search
            recoverable = any(row.get("raw_retained") for row in search(uid, char_id))
        except Exception:
            recoverable = False
    return {
        "identity": "sha256",
        "download_path": "/chat/media/{sha256}",
        "scope": "chat",
        "retention": {
            "inbox_max_age_days": int(inbox_cfg.get("max_age_days", 7)),
            "image_cache_max_age_days": int(cache_cfg.get("max_age_days", 30)),
            "image_cache_max_files": int(cache_cfg.get("max_files", 500)),
            "live_ref_guard": True,
            "retain_raw_uploads": bool(
                (get_config().get("character_document_library") or {}).get("retain_raw_uploads", False)
            ),
        },
        "counts": {
            "live_refs": len(live),
            "inbox_files": len(inbox_files),
            "inbox_live": live_inbox,
            "image_cache_files": len(cache_files),
            "image_cache_live": live_cache,
        },
        "recoverable_raw_in_scope": recoverable,
        "note": "只统计引用与保留策略，不展示正文、路径或文件名。墓碑事件与已删资料库条目不再保护缓存。",
    }
