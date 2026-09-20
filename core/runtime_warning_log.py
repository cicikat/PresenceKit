"""WARNING+ rotating JSONL persistence after sandbox paths are known.

Ownership:
- install after ``core.sandbox.init_paths`` / first production ``get_paths()``
- idempotent; a later sandbox re-init rebinds the same handler to the new root
- shutdown flushes and closes the file stream
- write/rotation faults never recurse into this handler and never block chat
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from admin.log_filter import RedactingFormatter, UrlRedactionFilter, redact_log_text

_HANDLER_NAME = "presence.runtime_warning_jsonl"
_KEEP_DAYS = 14
_MAX_DAY_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 48 * 1024 * 1024
_MAX_MESSAGE_CHARS = 2000
_MAX_EXC_CHARS = 4000
_LEVEL_NAMES = frozenset({"WARNING", "ERROR", "CRITICAL"})

_install_lock = threading.Lock()
_state_lock = threading.Lock()
_write_faults: list[dict[str, Any]] = []
_dropped_records = 0
_truncated_days: set[str] = set()
_last_prune_day = ""
_installed_base: str | None = None


def retention_days() -> int:
    return _KEEP_DAYS


def max_day_bytes() -> int:
    return _MAX_DAY_BYTES


def max_total_bytes() -> int:
    return _MAX_TOTAL_BYTES


def daily_path(base_path: Path, day: str) -> Path:
    return base_path.with_name(f"{base_path.stem}-{day}{base_path.suffix}")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_day(ts: datetime | None = None) -> str:
    moment = ts or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d")


def parse_iso_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _paths():
    from core.sandbox import get_paths
    return get_paths()


def _base_path() -> Path:
    return _paths().runtime_warning_log()


def _record_write_fault(kind: str, detail: str) -> None:
    global _dropped_records
    with _state_lock:
        _dropped_records += 1
        _write_faults.append({
            "ts": utc_now().isoformat().replace("+00:00", "Z"),
            "kind": kind,
            "detail": redact_log_text(detail)[:240],
        })
        del _write_faults[:-20]


def write_fault_snapshot() -> dict[str, Any]:
    with _state_lock:
        return {
            "dropped_records": _dropped_records,
            "truncated_days": sorted(_truncated_days),
            "recent_faults": list(_write_faults),
        }


def reset_write_faults_for_tests() -> None:
    global _dropped_records, _last_prune_day
    with _state_lock:
        _dropped_records = 0
        _truncated_days.clear()
        _write_faults.clear()
        _last_prune_day = ""


def _existing_day_files(base_path: Path) -> list[tuple[str, Path]]:
    pattern = f"{base_path.stem}-*{base_path.suffix}"
    rows: list[tuple[str, Path]] = []
    parent = base_path.parent
    if not parent.exists():
        return rows
    for candidate in parent.glob(pattern):
        day = candidate.stem.removeprefix(f"{base_path.stem}-")
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue
        rows.append((day, candidate))
    rows.sort(key=lambda item: item[0])
    return rows


def prune_runtime_warning_logs(
    *,
    now: datetime | None = None,
    keep_days: int = _KEEP_DAYS,
    max_total_bytes: int = _MAX_TOTAL_BYTES,
) -> dict[str, Any]:
    """Age + capacity prune; never touches error.log."""
    base_path = _base_path()
    moment = now or utc_now()
    cutoff = (moment.astimezone(timezone.utc).date() - timedelta(days=keep_days - 1))
    removed: list[str] = []
    remaining: list[tuple[str, Path, int]] = []
    for day, path in _existing_day_files(base_path):
        try:
            file_day = datetime.strptime(day, "%Y-%m-%d").date()
            size = path.stat().st_size if path.exists() else 0
        except OSError:
            continue
        if file_day < cutoff:
            try:
                path.unlink()
                removed.append(day)
            except OSError as exc:
                _record_write_fault("prune", f"{path.name}: {exc}")
            continue
        remaining.append((day, path, size))

    total = sum(size for _day, _path, size in remaining)
    while remaining and total > max_total_bytes:
        day, path, size = remaining[0]
        try:
            path.unlink()
            remaining.pop(0)
            total -= size
            removed.append(day)
        except OSError as exc:
            _record_write_fault("capacity", f"{path.name}: {exc}")
            break
    return {"removed_days": removed, "kept_bytes": total, "kept_files": len(remaining)}


def maybe_prune_runtime_warning_logs(now: datetime | None = None) -> None:
    global _last_prune_day
    day = utc_day(now)
    with _state_lock:
        if _last_prune_day == day:
            return
        _last_prune_day = day
    try:
        prune_runtime_warning_logs(now=now)
    except Exception as exc:
        _record_write_fault("prune", str(exc))


def _safe_exc_text(record: logging.LogRecord) -> str:
    if not record.exc_info:
        return ""
    try:
        formatted = logging.Formatter().formatException(record.exc_info)
    except Exception:
        formatted = type(record.exc_info[1]).__name__ if record.exc_info[1] else "exc"
    return redact_log_text(formatted)[:_MAX_EXC_CHARS]


def encode_runtime_warning_record(record: logging.LogRecord) -> dict[str, Any]:
    created = datetime.fromtimestamp(record.created, tz=timezone.utc)
    message = redact_log_text(record.getMessage())[:_MAX_MESSAGE_CHARS]
    payload: dict[str, Any] = {
        "ts": created.isoformat().replace("+00:00", "Z"),
        "level": record.levelname,
        "logger": record.name,
        "message": message,
    }
    request_id = getattr(record, "request_id", "") or getattr(record, "audit_id", "")
    if request_id:
        payload["request_id"] = str(request_id)[:128]
    exc_text = _safe_exc_text(record)
    if exc_text:
        payload["exc"] = exc_text
    return payload


class RuntimeWarningJsonlHandler(logging.Handler):
    """WARNING+ JSONL writer bound to the current sandbox root."""

    terminator = "\n"

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.name = _HANDLER_NAME
        self.addFilter(UrlRedactionFilter())
        self.setFormatter(RedactingFormatter())
        self._lock = threading.RLock()
        self._emitting = threading.local()
        self._stream = None
        self._open_day = ""
        self._open_path: Path | None = None
        self._bound_base: str | None = None

    def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        self._open_day = ""
        self._open_path = None
        if stream is None:
            return
        try:
            stream.flush()
        except OSError:
            pass
        try:
            stream.close()
        except OSError:
            pass

    def rebind_if_needed(self) -> None:
        try:
            base = str(_base_path())
        except Exception:
            return
        with self._lock:
            if self._bound_base != base:
                self._close_stream()
                self._bound_base = base

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        if getattr(self._emitting, "active", False):
            return
        self._emitting.active = True
        try:
            self.rebind_if_needed()
            payload = encode_runtime_warning_record(record)
            created = datetime.fromtimestamp(record.created, tz=timezone.utc)
            self._append(payload, created)
        except Exception:
            self.handleError(record)
        finally:
            self._emitting.active = False

    def handleError(self, record: logging.LogRecord) -> None:
        _record_write_fault("emit", getattr(record, "name", "unknown"))

    def _append(self, payload: dict[str, Any], created: datetime) -> None:
        day = utc_day(created)
        line = json.dumps(payload, ensure_ascii=False) + self.terminator
        encoded = line.encode("utf-8")
        maybe_prune_runtime_warning_logs(now=created)
        with self._lock:
            path = daily_path(_base_path(), day)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                current_size = path.stat().st_size if path.exists() else 0
            except OSError as exc:
                _record_write_fault("stat", str(exc))
                return
            if current_size >= _MAX_DAY_BYTES:
                with _state_lock:
                    _truncated_days.add(day)
                return
            if current_size + len(encoded) > _MAX_DAY_BYTES:
                with _state_lock:
                    _truncated_days.add(day)
                return
            try:
                if self._open_path != path or self._stream is None:
                    self._close_stream()
                    self._stream = open(path, "a", encoding="utf-8", newline="\n")
                    self._open_path = path
                    self._open_day = day
                    self._bound_base = str(_base_path())
                self._stream.write(line)
                self._stream.flush()
            except OSError as exc:
                self._close_stream()
                _record_write_fault("write", str(exc))

    def flush(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.flush()
                except OSError as exc:
                    _record_write_fault("flush", str(exc))

    def close(self) -> None:
        with self._lock:
            self._close_stream()
        super().close()


def _find_handler(root: logging.Logger | None = None) -> RuntimeWarningJsonlHandler | None:
    logger = root or logging.getLogger()
    for handler in logger.handlers:
        if isinstance(handler, RuntimeWarningJsonlHandler) or handler.name == _HANDLER_NAME:
            return handler  # type: ignore[return-value]
    return None


def install_runtime_warning_handler() -> RuntimeWarningJsonlHandler:
    """Attach or rebind the WARNING+ JSONL handler. Safe to call repeatedly."""
    with _install_lock:
        root = logging.getLogger()
        existing = _find_handler(root)
        if existing is None:
            existing = RuntimeWarningJsonlHandler()
            root.addHandler(existing)
        else:
            existing.rebind_if_needed()
        global _installed_base
        try:
            _installed_base = str(_base_path())
        except Exception:
            _installed_base = None
        maybe_prune_runtime_warning_logs()
        return existing


def shutdown_runtime_warning_handler() -> None:
    root = logging.getLogger()
    handler = _find_handler(root)
    if handler is None:
        return
    try:
        handler.flush()
    except Exception:
        pass
    try:
        handler.close()
    except Exception:
        pass
    try:
        root.removeHandler(handler)
    except Exception:
        pass


def ensure_runtime_warning_handler_after_paths() -> None:
    """Production ``get_paths()`` does not call ``init_paths``; install once paths exist."""
    install_runtime_warning_handler()


def query_runtime_warnings(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    level: str = "",
    logger_name: str = "",
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Read-only query over rotated JSONL. Never accepts a filesystem path."""
    base_path = _base_path()
    files = _existing_day_files(base_path)
    readable_error = ""
    unreadable: list[str] = []
    matched: list[dict[str, Any]] = []
    level_filter = str(level or "").strip().upper()
    logger_filter = str(logger_name or "").strip()
    if level_filter and level_filter not in _LEVEL_NAMES:
        raise ValueError("invalid_level")
    if start and end and start > end:
        raise ValueError("invalid_time_range")

    for day, path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            unreadable.append(day)
            readable_error = redact_log_text(str(exc))[:240]
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            ts = parse_iso_datetime(str(row.get("ts") or ""))
            if ts is None:
                continue
            if start and ts < start:
                continue
            if end and ts > end:
                continue
            row_level = str(row.get("level") or "").upper()
            if level_filter and row_level != level_filter:
                continue
            row_logger = str(row.get("logger") or "")
            if logger_filter and logger_filter not in row_logger:
                continue
            matched.append({
                "ts": ts.isoformat().replace("+00:00", "Z"),
                "level": row_level,
                "logger": row_logger,
                "message": redact_log_text(str(row.get("message") or ""))[:_MAX_MESSAGE_CHARS],
                "request_id": str(row.get("request_id") or "")[:128],
                "exc": redact_log_text(str(row.get("exc") or ""))[:_MAX_EXC_CHARS],
            })

    matched.sort(key=lambda row: row["ts"], reverse=True)
    total = len(matched)
    page = matched[offset:offset + limit]
    snapshot = write_fault_snapshot()
    now = utc_now()
    oldest = (now.date() - timedelta(days=_KEEP_DAYS - 1)).isoformat()
    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
        "retention_days": _KEEP_DAYS,
        "retention_from": oldest,
        "retention_to": utc_day(now),
        "max_day_bytes": _MAX_DAY_BYTES,
        "max_total_bytes": _MAX_TOTAL_BYTES,
        "truncated_days": snapshot["truncated_days"],
        "write_faults": snapshot["recent_faults"],
        "dropped_records": snapshot["dropped_records"],
        "unreadable_days": unreadable,
        "unreadable_error": readable_error,
        "installed": bool(_find_handler()),
        "timezone": "UTC",
    }
