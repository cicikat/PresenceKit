"""Read-only shadow evaluation for the Memory Event recall path.

The shadow path is deliberately separate from prompt construction.  It may
search and expand a bounded reality event window, but it returns only
content-free metrics and identifiers to the caller.  Any failure is a metric,
not a reason to alter the normal recall path.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from typing import Any, Iterable

from core.config_loader import get_config
from core.memory import event_query
from core.memory import event_store
from core.memory.source_policy import is_isolated
from core.memory.scope import MemoryScope

_SHADOW_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="event-shadow",
)
_SCOPE_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}
_SCOPE_LOCKS_GUARD = threading.Lock()
logger = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "uids": [],
    "char_ids": [],
    "seed_limit": 3,
    "window_before": 2,
    "window_after": 2,
    "max_related_per_seed": 4,
    "timeout_ms": 120,
    "sqlite_timeout_ms": 40,
    "max_trace_ids": 24,
}


def config() -> dict[str, Any]:
    """Return bounded, hot-reloaded shadow settings."""
    try:
        raw = get_config().get("event_shadow_recall") or {}
    except Exception:
        raw = {}
    result = dict(DEFAULTS)
    if isinstance(raw, dict):
        result.update(raw)
    for key, low, high in (
        ("seed_limit", 1, 8),
        ("window_before", 0, 8),
        ("window_after", 0, 8),
        ("max_related_per_seed", 0, 8),
        ("timeout_ms", 20, 500),
        ("sqlite_timeout_ms", 5, 100),
        ("max_trace_ids", 1, 64),
    ):
        try:
            result[key] = min(high, max(low, int(result.get(key, DEFAULTS[key]))))
        except (TypeError, ValueError):
            result[key] = DEFAULTS[key]
    return result


def _scope_lock(scope: MemoryScope) -> threading.Lock:
    key = (scope.uid, str(scope.character_id or ""), scope.domain)
    with _SCOPE_LOCKS_GUARD:
        return _SCOPE_LOCKS.setdefault(key, threading.Lock())


def _values(raw: Any) -> set[str]:
    if isinstance(raw, str):
        return {raw.strip()} if raw.strip() else set()
    if isinstance(raw, (list, tuple, set, frozenset)):
        return {str(value).strip() for value in raw if str(value).strip()}
    return set()


def enabled_for(uid: str, char_id: str, cfg: dict[str, Any] | None = None) -> bool:
    """Resolve global plus uid/character rollout gates.

    ``uids``/``char_ids`` are allowlists and can enable a scope even when the
    global flag is false.  The two ``allow_*`` aliases are accepted for easy
    migration from deployment config drafts.
    """
    settings = cfg or config()
    if bool(settings.get("enabled", False)):
        return True
    return (
        str(uid) in (_values(settings.get("uids")) | _values(settings.get("allow_uids")))
        or str(char_id) in (_values(settings.get("char_ids")) | _values(settings.get("allow_char_ids")))
    )


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return round(len(left & right) / len(union), 4) if union else 0.0


def _text_size(event: dict[str, Any]) -> int:
    return len(str(event.get("memory_text") or event.get("visible_text") or ""))


def _in_scope(event: dict[str, Any], scope: MemoryScope) -> bool:
    return (
        str(event.get("uid") or "") == scope.uid
        and str(event.get("char_id") or "") == str(scope.character_id or "")
        and str(event.get("realm") or "") == scope.domain
    )


def _comparison_defaults() -> dict[str, Any]:
    return {
        "comparison_mode": "event_id_and_turn_id",
        "event_overlap_rate": 0.0,
        "turn_overlap_rate": 0.0,
        # Compatibility alias: this is event-level only, never a cross-namespace ID comparison.
        "overlap_rate": 0.0,
        "event_overlap_count": 0,
        "turn_overlap_count": 0,
        "old_result_count": 0,
        "old_mapped_count": 0,
        "old_unmapped_count": 0,
        "old_mapped_event_count": 0,
        "new_mapped_count": 0,
        "new_unmapped_count": 0,
        "new_event_count": 0,
        "new_turn_count": 0,
        "extra_event_count": 0,
        "omitted_event_count": 0,
        "event_coverage": 0.0,
        "comparison_scope_rejections": 0,
    }


def compare_legacy_results(
    result: dict[str, Any],
    old_results: Iterable[Any],
    *,
    scope: MemoryScope,
) -> dict[str, Any]:
    """Compare only shared event/turn identities, never episodic/vector IDs."""
    metrics = _comparison_defaults()
    new_ids = {str(value) for value in result.get("new_event_ids", []) if value}
    new_turns = {str(value) for value in result.get("new_turn_ids", []) if value}
    event_turns = {
        str(event_id): str(turn_id)
        for event_id, turn_id in (result.get("new_event_turns") or {}).items()
        if event_id and turn_id
    }
    mapped_old_events: set[str] = set()
    mapped_old_turns: set[str] = set()
    for item in old_results:
        metrics["old_result_count"] += 1
        explicit_event_ids: set[str] = set()
        turn_id = ""
        if isinstance(item, str):
            # Deprecated direct API input remains an explicit event ID, not an
            # inferred episodic/vector identifier.
            explicit_event_ids.add(item)
        elif isinstance(item, dict):
            item_scope = item.get("scope")
            if isinstance(item_scope, dict) and (
                str(item_scope.get("uid") or "") != scope.uid
                or str(item_scope.get("char_id") or "") != str(scope.character_id or "")
                or str(item_scope.get("realm") or "") != scope.domain
            ):
                metrics["old_unmapped_count"] += 1
                metrics["comparison_scope_rejections"] += 1
                continue
            if is_isolated(item.get("source")):
                metrics["old_unmapped_count"] += 1
                metrics["comparison_scope_rejections"] += 1
                continue
            source_ids = item.get("source_event_ids")
            if isinstance(source_ids, (list, tuple, set, frozenset)):
                explicit_event_ids.update(str(value) for value in source_ids if value)
            elif source_ids:
                explicit_event_ids.add(str(source_ids))
            if item.get("event_id"):
                explicit_event_ids.add(str(item["event_id"]))
            turn_id = str(item.get("turn_id") or item.get("source_turn_id") or "")
        else:
            metrics["old_unmapped_count"] += 1
            continue
        mapped = bool(explicit_event_ids)
        mapped_old_events.update(explicit_event_ids)
        if turn_id:
            turn_event_ids = set(event_store.event_ids_for_turn(scope, turn_id))
            if turn_event_ids:
                mapped_old_events.update(turn_event_ids)
                mapped_old_turns.add(turn_id)
                mapped = True
        if mapped:
            metrics["old_mapped_count"] += 1
        else:
            metrics["old_unmapped_count"] += 1

    overlap_events = new_ids & mapped_old_events
    comparable_turns = mapped_old_turns
    overlap_turns = new_turns & comparable_turns
    metrics.update({
        "event_overlap_count": len(overlap_events),
        "turn_overlap_count": len(overlap_turns),
        "old_mapped_event_count": len(mapped_old_events),
        "new_mapped_count": len(new_ids),
        "new_event_count": len(new_ids),
        "new_turn_count": len(new_turns),
        "extra_event_count": len(new_ids - mapped_old_events),
        "omitted_event_count": len(mapped_old_events - new_ids),
        # Coverage denominator is mapped old events, never old_result_count or new_event_count.
        "event_coverage": round(len(overlap_events) / len(mapped_old_events), 4) if mapped_old_events else 0.0,
        "event_overlap_rate": _jaccard(new_ids, mapped_old_events),
        "turn_overlap_rate": _jaccard(new_turns, comparable_turns),
    })
    metrics["overlap_rate"] = metrics["event_overlap_rate"]
    result.update(metrics)
    result.pop("new_event_turns", None)
    return result


def _run_sync(
    scope: MemoryScope,
    query: str,
    settings: dict[str, Any],
    cancelled: threading.Event,
    occurred_after: float | None,
    occurred_before: float | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    run_lock = _scope_lock(scope)
    if not run_lock.acquire(blocking=False):
        return {"status": "busy", "timeout_reason": "prior_run_active", "elapsed_ms": 0}
    seed_ids: list[str] = []
    new_ids: list[str] = []
    event_turns: dict[str, str] = {}
    expanded_count = 0
    related_count = 0
    rejected_count = 0
    chars = 0
    truncation_reason = ""
    try:
        seeds = event_query.search(
            scope, text=str(query or "")[:256], actor="", kind="", source="",
            occurred_after=occurred_after, occurred_before=occurred_before, cursor="",
            limit=int(settings["seed_limit"]),
            order="desc",
            busy_timeout_ms=int(settings["sqlite_timeout_ms"]),
        )
        candidates = seeds.get("items", [])
        seen: set[str] = set()

        def _add(item: dict[str, Any]) -> bool:
            nonlocal chars
            if not _in_scope(item, scope):
                return False
            event_id = str(item.get("event_id") or "")
            if not event_id or event_id in seen or len(new_ids) >= int(settings["max_trace_ids"]):
                return False
            seen.add(event_id)
            new_ids.append(event_id)
            event_turns[event_id] = str(item.get("turn_id") or "")
            chars += _text_size(item)
            return True

        for item in candidates:
            if cancelled.is_set():
                return {"status": "cancelled", "timeout_reason": "cancelled"}
            if not _in_scope(item, scope):
                rejected_count += 1
                continue
            if _add(item):
                seed_ids.append(str(item["event_id"]))
            if len(new_ids) >= int(settings["max_trace_ids"]):
                truncation_reason = "trace_id_limit"
                break

        for event_id in list(seed_ids):
            if cancelled.is_set():
                return {"status": "cancelled", "timeout_reason": "cancelled"}
            if len(new_ids) >= int(settings["max_trace_ids"]):
                truncation_reason = truncation_reason or "trace_id_limit"
                break
            window = event_query.window(
                scope, event_id, before=int(settings["window_before"]),
                after=int(settings["window_after"]),
                busy_timeout_ms=int(settings["sqlite_timeout_ms"]),
            )
            if window:
                for item in [*window.get("before", []), *window.get("after", [])]:
                    if cancelled.is_set():
                        return {"status": "cancelled", "timeout_reason": "cancelled"}
                    if not _in_scope(item, scope):
                        rejected_count += 1
                        continue
                    expanded_count += 1
                    _add(item)
            if int(settings["max_related_per_seed"]) <= 0 or cancelled.is_set():
                continue
            related = event_query.related(
                scope, event_id, cursor="", limit=int(settings["max_related_per_seed"]),
                relation_types=None, busy_timeout_ms=int(settings["sqlite_timeout_ms"]),
            )
            for item in related.get("items", []) if related else []:
                event = item.get("event")
                if not event:
                    continue
                if not _in_scope(event, scope):
                    rejected_count += 1
                    continue
                related_count += 1
                _add(event)

        if seeds.get("truncation_reason"):
            truncation_reason = truncation_reason or str(seeds["truncation_reason"])
        return {
            "status": "ok",
            "seed_event_ids": seed_ids,
            "new_event_ids": new_ids,
            "expand_count": expanded_count,
            "related_count": related_count,
            "candidate_count": len(new_ids),
            "chars": chars,
            "tokens": (chars + 3) // 4,
            "new_turn_ids": sorted({turn_id for turn_id in event_turns.values() if turn_id}),
            "new_event_turns": event_turns,
            "scope_rejections": rejected_count,
            "truncation_reason": truncation_reason,
            "timeout_reason": "",
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "seed_order": "temporal_desc",
        }
    finally:
        run_lock.release()


async def run_shadow_query(
    scope: MemoryScope,
    query: str,
    *,
    old_chars: int = 0,
    occurred_after: float | None = None,
    occurred_before: float | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded shadow query with a hard wall-clock budget."""
    cfg = config()
    if settings is not None:
        cfg.update(settings)
        for key, low, high in (
            ("seed_limit", 1, 8), ("window_before", 0, 8), ("window_after", 0, 8),
            ("max_related_per_seed", 0, 8), ("timeout_ms", 20, 500),
            ("sqlite_timeout_ms", 5, 100), ("max_trace_ids", 1, 64),
        ):
            try:
                cfg[key] = min(high, max(low, int(cfg[key])))
            except (TypeError, ValueError):
                cfg[key] = DEFAULTS[key]
    base = {
        "enabled": enabled_for(scope.uid, str(scope.character_id or ""), cfg),
        "status": "disabled",
        "seed_event_ids": [],
        "new_event_ids": [],
        "expand_count": 0,
        "related_count": 0,
        "candidate_count": 0,
        "chars": 0,
        "tokens": 0,
        "old_chars": max(0, int(old_chars)),
        "old_tokens": (max(0, int(old_chars)) + 3) // 4,
        "new_turn_ids": [],
        "seed_order": "",
        "scope_rejections": 0,
        "truncation_reason": "",
        "timeout_reason": "",
        "elapsed_ms": 0,
        "timeout_ms": int(cfg["timeout_ms"]),
        "sqlite_timeout_ms": int(cfg["sqlite_timeout_ms"]),
        **_comparison_defaults(),
    }
    if not base["enabled"]:
        return base
    cfg["sqlite_timeout_ms"] = min(
        int(cfg["sqlite_timeout_ms"]), max(5, int(cfg["timeout_ms"]) // 2),
    )
    base["sqlite_timeout_ms"] = int(cfg["sqlite_timeout_ms"])
    cancelled = threading.Event()
    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _SHADOW_EXECUTOR, _run_sync, scope, query, cfg, cancelled,
                occurred_after, occurred_before,
            ),
            timeout=float(cfg["timeout_ms"]) / 1000.0,
        )
        result = {**base, **result, "enabled": True}
        result["old_chars"] = max(0, int(old_chars))
        result["old_tokens"] = (max(0, int(old_chars)) + 3) // 4
        return result
    except asyncio.TimeoutError:
        cancelled.set()
        base.update({"status": "timeout", "timeout_reason": "budget_exceeded"})
        return base
    except Exception as exc:
        logger.warning("[event_shadow_recall] run failed: %s", type(exc).__name__, exc_info=True)
        base.update({"status": "error", "timeout_reason": type(exc).__name__[:64]})
        return base


def finalize_shadow_recall(
    result: dict[str, Any], *, scope: MemoryScope, old_results: Iterable[Any] = (),
    old_ids: Iterable[Any] = (), old_chars: int = 0,
) -> dict[str, Any]:
    finalized = dict(result)
    finalized["old_chars"] = max(0, int(old_chars))
    finalized["old_tokens"] = (finalized["old_chars"] + 3) // 4
    if finalized.get("status") == "ok":
        return compare_legacy_results(
            finalized, list(old_results) or list(old_ids), scope=scope,
        )
    finalized.pop("new_event_turns", None)
    return finalized


async def run_shadow_recall(
    scope: MemoryScope,
    query: str,
    *,
    old_ids: Iterable[Any] = (),
    old_results: Iterable[Any] = (),
    old_chars: int = 0,
    occurred_after: float | None = None,
    occurred_before: float | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for direct callers."""
    result = await run_shadow_query(
        scope, query, old_chars=old_chars, occurred_after=occurred_after,
        occurred_before=occurred_before, settings=settings,
    )
    return finalize_shadow_recall(
        result, scope=scope, old_results=old_results, old_ids=old_ids,
        old_chars=old_chars,
    )
