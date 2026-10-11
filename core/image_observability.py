"""Bounded, fail-open recognition results; no images, requests or credentials."""
from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

from core.safe_write import safe_append_jsonl, safe_write_text
from core.sandbox import get_paths

MAX_RESULT_CHARS = 12000
MAX_LOG_BYTES = 16 * 1024 * 1024
RETENTION_DAYS = 30
_lock = threading.RLock()
_suppressed = ContextVar('image_trace_suppressed', default=False)
_source = ContextVar('image_trace_source', default='')


@contextmanager
def attributed(source: str):
    token = _source.set(source)
    try:
        yield
    finally:
        _source.reset(token)


@contextmanager
def suppress_nested_trace():
    """The shadow ingress owns its existing sensitive-aware trace projection."""
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)


def _append_row(row: dict) -> bool:
    with _lock:
        path = get_paths().visual_trace_log()
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            lines = path.read_text(encoding='utf-8').splitlines()
            kept, size = [], 0
            for line in reversed(lines):
                size += len(line.encode('utf-8')) + 1
                if size > MAX_LOG_BYTES // 2:
                    break
                kept.append(line)
            if not safe_write_text(path, '\n'.join(reversed(kept)) + '\n'):
                return False
        return safe_append_jsonl(path, row)


def append_shadow(*, source: str, observation=None, dropped: str | None = None) -> bool:
    """Preserve the original shadow row contract while sharing the writer lock."""
    try:
        row = {'ts': time.time(), 'source': source, 'dropped': dropped}
        if observation is not None:
            row.update(scene=observation.scene, activity=observation.activity,
                       confidence=observation.confidence, caption=observation.caption)
        return _append_row(row)
    except Exception:
        return False


def append(*, source: str, result: str = '', model: str = '', provider: str = '',
           error: str = '', sensitive: bool = False, cached: bool = False,
           image_count: int = 1, **projection) -> bool:
    if _suppressed.get():
        return True
    try:
        source = _source.get() or source
        if result:
            try:
                parsed = json.loads(result)
                sensitive = sensitive or (isinstance(parsed, dict) and parsed.get('sensitive') is True)
            except (ValueError, TypeError):
                pass
            result = re.sub(r'data:image/[^\s"\x27<>]+', '[image omitted]', result)
        row = {'ts': time.time(), 'source': source[:80], 'model': model[:160],
               'provider': provider[:80], 'status': 'sensitive' if sensitive else ('error' if error else 'ok'),
               'cached': cached, 'image_count': image_count}
        if sensitive or error:
            row['dropped'] = 'sensitive' if sensitive else error[:80]
        else:
            row['caption'] = result[:MAX_RESULT_CHARS]
            row['truncated'] = len(result) > MAX_RESULT_CHARS
            row.update({k: projection[k] for k in ('scene', 'activity', 'confidence') if k in projection})
        return _append_row(row)
    except Exception:
        return False


def prune() -> int:
    try:
        with _lock:
            path = get_paths().visual_trace_log()
            if not path.exists():
                return 0
            lines = path.read_text(encoding='utf-8').splitlines()
            cutoff = time.time() - RETENTION_DAYS * 86400
            kept = []
            for line in lines:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and row.get('ts', 0) >= cutoff:
                        kept.append(line)
                except (ValueError, TypeError):
                    continue
            removed = len(lines) - len(kept)
            if removed and not safe_write_text(path, '\n'.join(kept) + ('\n' if kept else '')):
                return 0
            return removed
    except Exception:
        return 0
