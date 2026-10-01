"""Process-local, redacted counters for high-frequency runtime signals.

Console logging is intentionally not a ledger: routine signals may be sampled or
silenced there.  This module preserves the operational fact that they happened
without writing user content, prompt text, credentials, paths, or identifiers to
disk.  It is deliberately process-local, so a restart starts a fresh window.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal


SignalStatus = Literal["ok", "attention"]

_MAX_CONTEXTS_PER_SIGNAL = 64
_SAFE_CONTEXT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_PROCESS_STARTED_AT = time.time()
_LOCK = threading.Lock()


@dataclass
class _Signal:
    category: str
    code: str
    status: SignalStatus
    count: int
    first_seen: float
    last_seen: float
    latest_context: dict[str, Any] = field(default_factory=dict)
    contexts: set[str] = field(default_factory=set)
    context_counts: dict[str, int] = field(default_factory=dict)
    context_values: dict[str, dict[str, Any]] = field(default_factory=dict)


_signals: dict[tuple[str, str], _Signal] = {}


def _safe_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep this generic registry incapable of becoming a content/secret sink."""
    if not context:
        return {}

    cleaned: dict[str, Any] = {}
    for raw_key, raw_value in context.items():
        key = str(raw_key).strip().lower()
        if (
            not _SAFE_CONTEXT_KEY.fullmatch(key)
            or any(token in key for token in ("secret", "token", "content", "prompt", "path", "uid", "key", "credential", "auth", "url", "host"))
        ):
            continue
        if isinstance(raw_value, bool):
            cleaned[key] = raw_value
        elif isinstance(raw_value, int | float):
            cleaned[key] = raw_value
        elif isinstance(raw_value, str):
            value = raw_value.strip()
            if value and len(value) <= 80:
                cleaned[key] = value
    return cleaned


def _record(
    *,
    category: str,
    code: str,
    status: SignalStatus,
    context: Mapping[str, Any] | None = None,
) -> tuple[bool, int, int]:
    """Record one redacted runtime signal and return new/total/context counts.

    Callers use the boolean to emit one console line for a new condition while
    still counting every recurrence for the read-only admin observability view.
    """
    clean_category = str(category).strip().lower()[:48] or "uncategorized"
    clean_code = str(code).strip().lower()[:64] or "unknown"
    clean_context = _safe_context(context)
    context_key = json.dumps(clean_context, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    now = time.time()
    key = (clean_category, clean_code)

    with _LOCK:
        signal = _signals.get(key)
        if signal is None:
            signal = _Signal(
                category=clean_category,
                code=clean_code,
                status=status,
                count=0,
                first_seen=now,
                last_seen=now,
            )
            _signals[key] = signal
        signal.count += 1
        signal.last_seen = now
        signal.status = status
        signal.latest_context = clean_context
        if context_key in signal.context_counts:
            signal.context_counts[context_key] += 1
            return False, signal.count, signal.context_counts[context_key]
        if len(signal.contexts) >= _MAX_CONTEXTS_PER_SIGNAL:
            return False, signal.count, 0
        signal.contexts.add(context_key)
        signal.context_counts[context_key] = 1
        signal.context_values[context_key] = clean_context
        return True, signal.count, 1


def record(
    *,
    category: str,
    code: str,
    status: SignalStatus,
    context: Mapping[str, Any] | None = None,
) -> bool:
    """Record one redacted runtime signal and return whether its context is new."""
    is_new, _, _ = _record(category=category, code=code, status=status, context=context)
    return is_new


def record_counts(
    *,
    category: str,
    code: str,
    status: SignalStatus,
    context: Mapping[str, Any] | None = None,
) -> tuple[bool, int, int]:
    """Record a signal and return ``(is_new, total_count, context_count)``."""
    return _record(category=category, code=code, status=status, context=context)


def record_and_should_log(
    *,
    category: str,
    code: str,
    status: SignalStatus = "attention",
    context: Mapping[str, Any] | None = None,
    every: int = 20,
) -> bool:
    """Count every occurrence, but tell the caller to log only the first and every Nth.

    For fail-open background calls (lightweight judges, third-party lookups) that can
    fail in long streaks: the fact is always preserved in the counters, while the log
    gets one line per new context and then one per ``every`` repeats.
    """
    _, total, context_count = _record(category=category, code=code, status=status, context=context)
    count = context_count or total
    return count == 1 or (every > 0 and count % every == 0)


def snapshot() -> dict[str, Any]:
    """Return a copy suitable for the ``state.read`` admin endpoint."""
    with _LOCK:
        signals = [
            {
                "category": item.category,
                "code": item.code,
                "status": item.status,
                "count": item.count,
                "unique_contexts": len(item.contexts),
                "context_counts": [
                    {"context": dict(item.context_values[key]), "count": count}
                    for key, count in sorted(item.context_counts.items())
                ],
                "first_seen": item.first_seen,
                "last_seen": item.last_seen,
                "latest_context": dict(item.latest_context),
            }
            for item in _signals.values()
        ]

    signals.sort(key=lambda item: (item["status"] != "attention", -item["last_seen"], item["code"]))
    summary = {"ok": 0, "attention": 0}
    for item in signals:
        summary[item["status"]] += 1
    return {
        "scope": "process",
        "started_at": _PROCESS_STARTED_AT,
        "summary": summary,
        "signals": signals,
    }


def _reset_for_tests() -> None:
    """Test seam; production code must never clear its process observation."""
    with _LOCK:
        _signals.clear()
