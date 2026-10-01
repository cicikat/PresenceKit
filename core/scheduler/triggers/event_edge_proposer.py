"""Bounded, model-proposed Memory Event relations (Brief 203).

This maintenance trigger never enters a prompt, recall, or fact-writing path.
It only records unreviewed proposals in the scoped evidence ledger.
"""
from __future__ import annotations

import hashlib
import json
import logging
import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Any

from core.memory.event_store import PROPOSAL_RELATION_TYPES

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "enabled": False,
    "cooldown_seconds": 6 * 3600,
    "event_window_size": 8,
    "max_candidates_per_run": 6,
    "max_daily_calls": 4,
    "max_daily_tokens": 1600,
    "max_tokens_per_call": 400,
    "scope_timeout_seconds": 30,
}
_DISCOVERY_LOCK = threading.Lock()
_DISCOVERY: dict[str, int | float] = {
    "runs": 0,
    "character_roots": 0,
    "candidate_directories": 0,
    "missing_ledgers": 0,
    "unhealthy_ledgers": 0,
    "version_mismatch": 0,
    "table_missing": 0,
    "column_missing": 0,
    "database_error": 0,
    "scope_mismatch": 0,
    "timeout": 0,
    "eligible_scopes": 0,
    "completed_scopes": 0,
    "timed_out_scopes": 0,
    "failed_scopes": 0,
    "updated_at": 0.0,
}
_SYSTEM_PROMPT = """You propose tentative relations between the supplied event records.
Return only a JSON array. Every item must have from_event_id, to_event_id,
relation_type, reason, and confidence. relation_type must be one of:
same_topic, follows_up, possible_cause, contradicts, supports.
Only relate supplied IDs. Do not assert certainty: possible_cause is always a
possibility. Keep reason under 240 characters. Return [] when no relation is useful."""


def _config() -> dict[str, int | bool]:
    from core.config_loader import get_config

    raw = get_config().get("event_edge_proposer", {})
    cfg: dict[str, int | bool] = dict(_DEFAULTS)
    if not isinstance(raw, dict):
        return cfg
    cfg["enabled"] = bool(raw.get("enabled", cfg["enabled"]))
    for key, lower, upper in (
        ("cooldown_seconds", 60, 7 * 24 * 3600),
        ("event_window_size", 2, 20),
        ("max_candidates_per_run", 1, 12),
        ("max_daily_calls", 1, 24),
        ("max_daily_tokens", 64, 20_000),
        ("max_tokens_per_call", 64, 2_000),
        ("scope_timeout_seconds", 1, 120),
    ):
        try:
            cfg[key] = min(upper, max(lower, int(raw.get(key, cfg[key]))))
        except (TypeError, ValueError):
            pass
    return cfg


def _scope_timeout_seconds(cfg: dict[str, int | bool]) -> float:
    """Return the effective per-scope timeout; tests may inject a short clock."""
    return float(cfg["scope_timeout_seconds"])


def _record_discovery(**counts: int) -> None:
    with _DISCOVERY_LOCK:
        _DISCOVERY["runs"] = int(_DISCOVERY["runs"]) + 1
        for key, value in counts.items():
            _DISCOVERY[key] = int(_DISCOVERY.get(key, 0)) + int(value)
        _DISCOVERY["updated_at"] = time.time()


def discovery_observability_snapshot() -> dict[str, int | float]:
    """Process-local, content-free scheduler discovery counters."""
    with _DISCOVERY_LOCK:
        return dict(_DISCOVERY)


def _day_key(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time(), timezone.utc).date().isoformat()


def _prompt(events: list[dict[str, Any]]) -> str:
    return _SYSTEM_PROMPT + "\n\nEvents:\n" + json.dumps(
        events, ensure_ascii=False, separators=(",", ":")
    )


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _parse_candidates(raw: str, event_ids: set[str], maximum: int) -> list[dict[str, Any]]:
    """Validate the complete model response before permitting any proposal write."""
    value = json.loads(raw)
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("invalid_json")
    candidates: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "from_event_id", "to_event_id", "relation_type", "reason", "confidence",
        }:
            raise ValueError("invalid_json")
        from_id = str(item["from_event_id"]).strip()
        to_id = str(item["to_event_id"]).strip()
        relation = str(item["relation_type"]).strip()
        reason = str(item["reason"]).strip()
        try:
            confidence = float(item["confidence"])
        except (TypeError, ValueError):
            raise ValueError("invalid_json") from None
        if (
            from_id not in event_ids or to_id not in event_ids or from_id == to_id
            or relation not in PROPOSAL_RELATION_TYPES or not reason or len(reason) > 240
            or not 0.0 <= confidence <= 1.0
        ):
            raise ValueError("invalid_json")
        candidates.append({
            "from_event_id": from_id, "to_event_id": to_id,
            "relation_type": relation, "reason": reason, "confidence": confidence,
        })
    return candidates


async def _propose_scope(uid: str, char_id: str, cfg: dict[str, int | bool]) -> None:
    from core.memory import event_store
    from core.memory.scope import MemoryScope
    from core.model_registry import resolve_category_info

    scope = MemoryScope.reality_scope(uid, char_id)
    now = time.time()
    if now - event_store.latest_proposer_run_at(scope) < int(cfg["cooldown_seconds"]):
        return
    day_key = _day_key(now)
    budget = event_store.proposal_budget_snapshot(scope, day_key)
    call_limit = int(cfg["max_daily_calls"])
    token_limit = int(cfg["max_daily_tokens"])
    token_budget = min(int(cfg["max_tokens_per_call"]), token_limit - budget["tokens"])
    if budget["calls"] >= call_limit or token_budget < 64:
        return

    events = event_store.recent_events_for_proposal(scope, limit=int(cfg["event_window_size"]))
    if len(events) < 2:
        return
    prompt = _prompt(events)
    prompt_hash = _prompt_hash(prompt)
    model_info = resolve_category_info("event_edge_proposer", char_id=char_id)
    model = str(model_info.get("model") or "")
    preset = str(model_info.get("effective_preset") or "")
    model_version = str(model_info.get("model_version") or model)

    try:
        from core import llm_client

        raw = await llm_client.chat(
            [{"role": "system", "content": prompt}],
            max_tokens_override=token_budget,
            call_category="event_edge_proposer",
            char_id=char_id,
        )
        candidates = _parse_candidates(
            raw or "", {event["event_id"] for event in events}, int(cfg["max_candidates_per_run"])
        )
    except Exception as exc:
        event_store.record_proposer_run(
            scope, day_key=day_key, input_count=len(events), candidate_count=0,
            token_budget=token_budget, model=model, preset=preset, model_version=model_version,
            prompt_hash=prompt_hash, status="failed", error_code="invalid_output" if isinstance(exc, ValueError) else "model_error",
        )
        logger.warning("[event_edge_proposer] proposal failed uid=%s char_id=%s code=%s", uid, char_id,
                       "invalid_output" if isinstance(exc, ValueError) else "model_error")
        return

    inserted = 0
    try:
        for candidate in candidates:
            inserted += int(event_store.append_edge_proposal(scope, {
                **candidate, "model": model, "preset": preset, "model_version": model_version,
                "prompt_hash": prompt_hash, "created_at": now,
            }))
        event_store.record_proposer_run(
            scope, day_key=day_key, input_count=len(events), candidate_count=len(candidates),
            token_budget=token_budget, model=model, preset=preset, model_version=model_version,
            prompt_hash=prompt_hash, status="ok",
        )
    except Exception:
        event_store.record_proposer_run(
            scope, day_key=day_key, input_count=len(events), candidate_count=0,
            token_budget=token_budget, model=model, preset=preset, model_version=model_version,
            prompt_hash=prompt_hash, status="failed", error_code="write_error",
        )
        logger.exception("[event_edge_proposer] write failed uid=%s char_id=%s", uid, char_id)
        return
    logger.info("[event_edge_proposer] uid=%s char_id=%s proposed=%d inserted=%d", uid, char_id, len(candidates), inserted)


async def _check_event_edge_proposer() -> None:
    """Scheduler-only candidate generation; it never emits or changes memory facts."""
    cfg = _config()
    if not cfg["enabled"]:
        return
    from core.scheduler.loop import _is_ready, _mark
    from core.asset_registry import get_registry
    from core.memory import event_store
    from core.memory.locks import uid_lock
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    from core.sandbox import get_paths

    if not _is_ready("event_edge_proposer"):
        return
    _mark("event_edge_proposer")
    counters = {
        "character_roots": 0, "candidate_directories": 0, "missing_ledgers": 0,
        "unhealthy_ledgers": 0, "eligible_scopes": 0, "completed_scopes": 0,
        "timed_out_scopes": 0, "failed_scopes": 0,
        "version_mismatch": 0, "table_missing": 0, "column_missing": 0,
        "database_error": 0, "scope_mismatch": 0,
        "timeout": 0,
    }
    for character in get_registry().list_all("character"):
        char_id = character.id
        char_root = get_paths().memory_char_root(char_id=char_id)
        if not char_root.exists():
            continue
        counters["character_roots"] += 1
        for directory in char_root.iterdir():
            if not directory.is_dir():
                continue
            counters["candidate_directories"] += 1
            scope = MemoryScope.reality_scope(directory.name, char_id)
            ledger_path = resolve_path(scope, "event_store")
            if not ledger_path.is_file():
                counters["missing_ledgers"] += 1
                continue
            health_code = event_store.existing_ledger_health_code(scope)
            if health_code != "ok":
                counters["unhealthy_ledgers"] += 1
                if health_code in counters:
                    counters[health_code] += 1
                continue
            counters["eligible_scopes"] += 1

            async def _run_scope() -> None:
                async with uid_lock(scope.uid):
                    await _propose_scope(scope.uid, char_id, cfg)

            try:
                await asyncio.wait_for(_run_scope(), timeout=_scope_timeout_seconds(cfg))
                counters["completed_scopes"] += 1
            except asyncio.TimeoutError:
                counters["timed_out_scopes"] += 1
                # 超时是持续性的（上游模型本身过慢/不可用，见 docs/known-issues.md），不靠加大
                # 阈值掩盖：每次都计数，日志只留首条与每 20 条一条。
                from core.runtime_signal_observability import record_and_should_log
                if record_and_should_log(category="model_quality", code="edge_proposer_scope_timeout",
                                         status="attention", context={"timeout_s": int(_scope_timeout_seconds(cfg))}):
                    logger.warning("[event_edge_proposer] scope timed out char_id=%s", char_id)
            except Exception:
                counters["failed_scopes"] += 1
                logger.exception("[event_edge_proposer] scope failed char_id=%s", char_id)
    _record_discovery(**counters)
