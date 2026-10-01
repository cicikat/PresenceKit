"""Rotated, redacted plain-text error ledger (``data/logs/error.log``).

This is the human-``tail``-able half of the two error ledgers (工单 E):

* ``error.log``                    — exceptions that went through ``core.error_handler``
                                     (``log_error()`` / ``with_retry``), with tracebacks.
* ``runtime_warnings-*.jsonl``     — every ``logging`` record at WARNING and above,
                                     including plain ``logging.error()`` calls that never
                                     reach this file (``GET /logs/runtime-warnings``).

``error.log`` is therefore **not** a full ERROR view; each file starts with a header
line saying so.  Rotation, retention and caps deliberately reuse the numbers owned by
``core.runtime_warning_log`` instead of introducing a second parameter set:

* the active file keeps the stable name ``error.log`` (so ``tail`` keeps working) and is
  renamed to ``error-YYYY-MM-DD.log`` the first time it is written to on a later UTC day;
* dated files older than the retention window are deleted, the oldest go first when the
  total exceeds the cap, and one UTC day never grows past the per-day cap (further
  records that day are counted as dropped, not written);
* every entry is passed through ``admin.log_filter.redact_log_text`` before it is written.

A pre-rotation ``error.log`` (no header, never redacted) is archived once: renamed away
immediately, then redacted line by line into ``error-YYYY-MM-DD.log.gz`` by a background
thread, so a 20 MB history never delays startup or the error path.  The archive then ages
out under the same retention rule; nothing is deleted before that.

Everything here is fail-open: a logging fault must never raise into the caller.
"""
from __future__ import annotations

import gzip
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HEADER_MARKER = "# [presence] error ledger v2"
HEADER = (
    HEADER_MARKER + "（纯文本，按 UTC 日轮转，已脱敏）。仅含经 core.error_handler 写入的异常；"
    "直接 logging.error()/warning() 的记录不在此文件，请查 runtime_warnings-*.jsonl"
    "（GET /logs/runtime-warnings）。\n"
)
_LEGACY_SUFFIX = ".legacy-migrating"
_DATED_RE = re.compile(r"^error-(\d{4}-\d{2}-\d{2})(?:\.\d+)?\.log(\.gz)?$")

_lock = threading.RLock()
_state_lock = threading.Lock()
_dropped_records = 0
_truncated_days: set[str] = set()
_faults: list[dict[str, str]] = []
_last_prune_day = ""
_migration_started_for: str | None = None
_migration_thread: threading.Thread | None = None

logger = logging.getLogger(__name__)


# ── shared numbers (single source of truth: core.runtime_warning_log) ──────────

def _limits() -> tuple[int, int, int]:
    from core import runtime_warning_log as rw
    return rw.retention_days(), rw.max_day_bytes(), rw.max_total_bytes()


def _utc_day(moment: datetime | None = None) -> str:
    from core.runtime_warning_log import utc_day
    return utc_day(moment)


def _base() -> Path:
    from core.sandbox import get_paths
    return get_paths().error_log()


def dated_path(day: str, *, gz: bool = False) -> Path:
    base = _base()
    return base.with_name(f"{base.stem}-{day}{base.suffix}{'.gz' if gz else ''}")


def _record_fault(kind: str, detail: str) -> None:
    global _dropped_records
    from admin.log_filter import redact_log_text
    with _state_lock:
        _faults.append({"kind": kind, "detail": redact_log_text(detail)[:240]})
        del _faults[:-20]


def reset_for_tests() -> None:
    global _dropped_records, _last_prune_day, _migration_started_for, _migration_thread
    with _state_lock:
        _dropped_records = 0
        _truncated_days.clear()
        _faults.clear()
    _last_prune_day = ""
    _migration_started_for = None
    thread, _migration_thread = _migration_thread, None
    if thread is not None:
        thread.join(timeout=10)


# ── write path ─────────────────────────────────────────────────────────────────

def _file_day(path: Path) -> str:
    return _utc_day(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))


def _rotate_if_stale(active: Path, today: str) -> None:
    """Move yesterday's (or older) active file to its dated name before writing today's."""
    if not active.exists():
        return
    day = _file_day(active)
    if day >= today:
        return
    target = dated_path(day)
    suffix = 0
    while target.exists():
        suffix += 1
        target = target.with_name(f"{target.stem.split('.')[0]}.{suffix}{target.suffix}")
    os.replace(active, target)


def append(entry: str, *, now: datetime | None = None) -> None:
    """Redact ``entry`` and append it to today's ``error.log``; never raises."""
    global _dropped_records
    try:
        from admin.log_filter import redact_log_text
        moment = now or datetime.now(timezone.utc)
        today = _utc_day(moment)
        text = redact_log_text(entry)
        encoded = text.encode("utf-8")
        _, max_day_bytes, _ = _limits()
        ensure_migration_started()
        with _lock:
            active = _base()
            active.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_stale(active, today)
            size = active.stat().st_size if active.exists() else 0
            if size + len(encoded) > max_day_bytes:
                with _state_lock:
                    _dropped_records += 1
                    _truncated_days.add(today)
                return
            with open(active, "a", encoding="utf-8", newline="\n") as handle:
                if size == 0:
                    handle.write(HEADER)
                handle.write(text)
        maybe_prune(now=moment)
    except Exception as exc:  # noqa: BLE001 - the ledger must never break its caller
        _record_fault("append", str(exc))
        logging.getLogger(__name__).debug("error ledger write failed", exc_info=True)


# ── retention ──────────────────────────────────────────────────────────────────

def _dated_files() -> list[tuple[str, Path]]:
    base = _base()
    rows: list[tuple[str, Path]] = []
    if not base.parent.exists():
        return rows
    for candidate in base.parent.iterdir():
        match = _DATED_RE.match(candidate.name)
        if match and candidate.is_file():
            rows.append((match.group(1), candidate))
    rows.sort(key=lambda item: (item[0], item[1].name))
    return rows


def prune(*, now: datetime | None = None) -> dict[str, Any]:
    """Age + capacity prune of dated files and archives; the active ``error.log`` is only
    counted toward the total, never deleted here."""
    keep_days, _, max_total = _limits()
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = moment.date() - timedelta(days=keep_days - 1)
    removed: list[str] = []
    kept: list[tuple[Path, int]] = []
    for day, path in _dated_files():
        try:
            size = path.stat().st_size
            if datetime.strptime(day, "%Y-%m-%d").date() < cutoff:
                path.unlink()
                removed.append(path.name)
                continue
        except OSError as exc:
            _record_fault("prune", f"{path.name}: {exc}")
            continue
        kept.append((path, size))
    active = _base()
    try:
        active_size = active.stat().st_size if active.exists() else 0
    except OSError:
        active_size = 0
    total = active_size + sum(size for _path, size in kept)
    while kept and total > max_total:
        path, size = kept.pop(0)
        try:
            path.unlink()
            removed.append(path.name)
            total -= size
        except OSError as exc:
            _record_fault("capacity", f"{path.name}: {exc}")
            break
    return {"removed": removed, "kept_files": len(kept), "kept_bytes": total}


def maybe_prune(*, now: datetime | None = None) -> None:
    global _last_prune_day
    day = _utc_day(now)
    with _state_lock:
        if _last_prune_day == day:
            return
        _last_prune_day = day
    try:
        prune(now=now)
    except Exception as exc:  # noqa: BLE001
        _record_fault("prune", str(exc))


# ── one-time legacy migration ──────────────────────────────────────────────────

def _is_legacy(active: Path) -> bool:
    """A pre-rotation ``error.log``: non-empty and lacking our header line."""
    try:
        if not active.is_file() or active.stat().st_size == 0:
            return False
        with open(active, "r", encoding="utf-8", errors="replace") as handle:
            return not handle.readline().startswith(HEADER_MARKER)
    except OSError:
        return False


def _archive(source: Path) -> Path | None:
    """Redact ``source`` line by line into ``error-<mtime day>.log.gz`` and remove it.

    The archive day is the source's last-write day, so a resumed run after a crash picks
    the same name; the gzip is written to a temp name and moved into place last.
    """
    from admin.log_filter import redact_log_text
    day = _file_day(source)
    final = dated_path(day, gz=True)
    tmp = final.with_name(final.name + ".tmp")
    with open(source, "r", encoding="utf-8", errors="replace") as src, \
            gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as out:
        out.write(f"# [presence] legacy error.log archived and redacted on {_utc_day()}（迁移前历史，未丢内容）\n")
        for line in src:
            out.write(redact_log_text(line))
    os.replace(tmp, final)
    source.unlink()
    return final


def _run_migration() -> None:
    try:
        base = _base()
        pending = base.with_name(base.name + _LEGACY_SUFFIX)
        if pending.exists():
            _archive(pending)
    except Exception as exc:  # noqa: BLE001
        _record_fault("migrate", str(exc))
        logger.warning("[error_log] 历史 error.log 归档失败，下次启动会重试: %s", type(exc).__name__)


def ensure_migration_started() -> None:
    """Idempotent, cheap: rename a legacy ``error.log`` away, archive it in the background."""
    global _migration_started_for, _migration_thread
    try:
        base = _base()
    except Exception as exc:  # noqa: BLE001
        _record_fault("migrate", str(exc))
        return
    key = str(base)
    if _migration_started_for == key:
        return
    with _lock:
        if _migration_started_for == key:
            return
        _migration_started_for = key
        try:
            pending = base.with_name(base.name + _LEGACY_SUFFIX)
            if _is_legacy(base) and not pending.exists():
                base.parent.mkdir(parents=True, exist_ok=True)
                os.replace(base, pending)
            if pending.exists():
                _migration_thread = threading.Thread(
                    target=_run_migration, name="error-log-archive", daemon=True,
                )
                _migration_thread.start()
        except Exception as exc:  # noqa: BLE001
            _record_fault("migrate", str(exc))


def wait_for_migration(timeout: float = 30.0) -> None:
    thread = _migration_thread
    if thread is not None:
        thread.join(timeout=timeout)


# ── read path (admin GET/DELETE /logs) ─────────────────────────────────────────

def read_tail(lines: int) -> tuple[str, int]:
    """Last ``lines`` lines across recent dated plain-text files and the active file.

    Compressed archives are history, not tail material, and are skipped.
    """
    from collections import deque
    lines = max(1, int(lines))
    files = [path for _day, path in _dated_files() if not path.name.endswith(".gz")]
    active = _base()
    if active.exists():
        files.append(active)
    tail: deque[str] = deque(maxlen=lines)
    total = 0
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    total += 1
                    tail.append(line)
        except OSError:
            continue
    return "".join(tail), total


def clear() -> int:
    """Admin 清空：删除 active 与所有按日明文/归档文件，返回删除个数。"""
    removed = 0
    base = _base()
    candidates = [path for _day, path in _dated_files()]
    pending = base.with_name(base.name + _LEGACY_SUFFIX)
    candidates += [p for p in (base, pending) if p.exists()]
    for path in candidates:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            _record_fault("clear", f"{path.name}: {exc}")
    return removed


def snapshot() -> dict[str, Any]:
    keep_days, max_day, max_total = _limits()
    base = _base()
    files = []
    for day, path in _dated_files():
        try:
            files.append({"name": path.name, "day": day, "bytes": path.stat().st_size,
                          "compressed": path.name.endswith(".gz")})
        except OSError:
            continue
    with _state_lock:
        return {
            "active_bytes": base.stat().st_size if base.exists() else 0,
            "files": files,
            "retention_days": keep_days, "max_day_bytes": max_day, "max_total_bytes": max_total,
            "truncated_days": sorted(_truncated_days),
            "dropped_records": _dropped_records,
            "recent_faults": list(_faults),
            "legacy_migration_pending": base.with_name(base.name + _LEGACY_SUFFIX).exists(),
            "timezone": "UTC",
        }
