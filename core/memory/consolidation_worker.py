"""Silent same-character dossier consolidation worker (Brief 258 D).

The scheduler is the only automatic entrypoint. Explicit operator first-night
uses ``run_operator_pass`` and never flips ``memory_consolidation.enabled``.
Both paths claim Task Manager work, create a bounded Work Session, call the
configured model without holding a memory lock, then submit a validated patch
to the shared dossier capability. They never send/capture a turn or touch the
conversation slow queue.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from core.agent_runtime.models import TaskPrincipal
from core.memory.scope import MemoryScope
from core.safe_write import safe_write_json
from core.sandbox import get_paths

logger = logging.getLogger(__name__)
CAPABILITY = "memory.consolidation"
ARTIFACT_KIND = "memory_consolidation_receipt"
PROMPT_REVISION = "memory-consolidation-prompt.v2"
OPERATOR_SOURCE = "operator_first_night"
_GLOBAL_SEMAPHORE = asyncio.Semaphore(1)
_scope_locks: dict[tuple[str, str], asyncio.Lock] = {}
_scope_locks_guard = threading.Lock()
_state_lock = threading.RLock()

_DEFAULTS: dict[str, Any] = {
    "enabled": False, "paused": False, "pause_reason": "", "grant_revision": 1,
    "night_start_hour": 23, "night_end_hour": 7, "idle_seconds": 600,
    "max_global_workers": 1, "batch_size": 100, "max_input_chars": 24000,
    "max_tokens_per_call": 1200, "daily_call_budget": 8,
    "daily_token_budget": 9600, "daily_wall_seconds": 600,
    "per_scope_daily_calls": 4, "per_scope_daily_tokens": 4800,
    "call_timeout_seconds": 90, "retry_backoff_seconds": 900,
    "background_preset": "",
}


def config() -> dict[str, Any]:
    from core.config_loader import get_config
    raw = get_config().get("memory_consolidation", {})
    value = dict(_DEFAULTS)
    if isinstance(raw, dict): value.update(raw)
    for key, low, high in (
        ("grant_revision", 1, 1_000_000), ("night_start_hour", 0, 23),
        ("night_end_hour", 0, 23), ("idle_seconds", 60, 24 * 3600),
        ("max_global_workers", 1, 1), ("batch_size", 1, 100),
        ("max_input_chars", 1000, 48000), ("max_tokens_per_call", 64, 4000),
        ("daily_call_budget", 1, 100), ("daily_token_budget", 64, 100000),
        ("daily_wall_seconds", 1, 86400), ("per_scope_daily_calls", 1, 50),
        ("per_scope_daily_tokens", 64, 50000), ("call_timeout_seconds", 1, 600),
        ("retry_backoff_seconds", 1, 86400),
    ):
        try: value[key] = min(high, max(low, int(value[key])))
        except (TypeError, ValueError): value[key] = _DEFAULTS[key]
    value["enabled"] = bool(value.get("enabled"))
    value["paused"] = bool(value.get("paused"))
    value["pause_reason"] = str(value.get("pause_reason") or "")[:128]
    value["background_preset"] = str(value.get("background_preset") or "")[:128]
    return value


def _night_window(cfg: dict[str, Any], now: datetime | None = None) -> bool:
    hour = (now or datetime.now()).hour
    start, end = int(cfg["night_start_hour"]), int(cfg["night_end_hour"])
    return start <= hour < end if start < end else hour >= start or hour < end


def _last_message_at(uid: str) -> float:
    path = get_paths().presence()
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return float((raw.get(str(uid)) or {}).get("last_message_at") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0.0


def _foreground_active(uid: str, cfg: dict[str, Any], *, now: float | None = None) -> bool:
    timestamp = time.time() if now is None else float(now)
    if timestamp - _last_message_at(uid) < int(cfg["idle_seconds"]): return True
    try:
        from core.scheduler.loop import _user_active_recently
        if _user_active_recently(int(cfg["idle_seconds"])): return True
        from core.conversation_gate import conversation_lock
        if conversation_lock(uid).locked(): return True
        from core.message_queue import active_sessions, queue_size
        if str(uid) in active_sessions() or queue_size(str(uid)) > 0: return True
    except Exception:
        return True
    return False


def _state() -> dict[str, Any]:
    path = get_paths().memory_consolidation_runtime_state()
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError): value = {}
    if value.get("schema_version") != "memory-consolidation-runtime.v1":
        value = {"schema_version": "memory-consolidation-runtime.v1", "days": {},
                 "round_robin_cursor": 0, "consecutive_failures": 0,
                 "backoff_until": 0.0, "last_error": "", "updated_at": 0.0}
    return value


def _save_state(value: dict[str, Any]) -> None:
    value["updated_at"] = time.time()
    if not safe_write_json(get_paths().memory_consolidation_runtime_state(), value, keep_bak=False):
        raise RuntimeError("memory_consolidation_state_write_failed")


def _day_bounds(now: float | None = None) -> tuple[str, float]:
    current = datetime.fromtimestamp(now or time.time())
    start = current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return current.date().isoformat(), start


def _global_budget_allows(cfg: dict[str, Any]) -> bool:
    day, _ = _day_bounds()
    with _state_lock:
        usage = (_state().get("days") or {}).get(day, {})
    return (int(usage.get("calls") or 0) < int(cfg["daily_call_budget"])
            and int(usage.get("tokens") or 0) + int(cfg["max_tokens_per_call"]) <= int(cfg["daily_token_budget"])
            and float(usage.get("wall_seconds") or 0) < float(cfg["daily_wall_seconds"]))


def _reserve_global_budget(cfg: dict[str, Any]) -> None:
    day, _ = _day_bounds()
    with _state_lock:
        state = _state(); days = state.setdefault("days", {})
        usage = days.setdefault(day, {"calls": 0, "tokens": 0, "wall_seconds": 0.0})
        usage["calls"] = int(usage.get("calls") or 0) + 1
        usage["tokens"] = int(usage.get("tokens") or 0) + int(cfg["max_tokens_per_call"])
        for old in sorted(days)[:-7]: days.pop(old, None)
        _save_state(state)


def _record_wall(seconds: float, *, error: str = "", backoff_seconds: int = 900) -> None:
    day, _ = _day_bounds()
    with _state_lock:
        state = _state(); usage = state.setdefault("days", {}).setdefault(
            day, {"calls": 0, "tokens": 0, "wall_seconds": 0.0})
        usage["wall_seconds"] = float(usage.get("wall_seconds") or 0) + max(0.0, seconds)
        if error:
            failures = int(state.get("consecutive_failures") or 0) + 1
            state["consecutive_failures"] = failures; state["last_error"] = error
            state["backoff_until"] = time.time() + min(
                86400, max(1, int(backoff_seconds)) * (2 ** min(6, failures - 1)),
            )
        else:
            state["consecutive_failures"] = 0; state["last_error"] = ""; state["backoff_until"] = 0.0
        _save_state(state)


def _scope_lock(uid: str, char_id: str) -> asyncio.Lock:
    with _scope_locks_guard: return _scope_locks.setdefault((uid, char_id), asyncio.Lock())


def _identity_context(char_id: str) -> tuple[str, str]:
    from core.character_loader import load
    character = load(char_id)
    value = {"name": character.name, "description": str(character.description or "")[:1600],
             "personality": str(character.personality or "")[:800]}
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _request_messages(prompt: str) -> list[dict[str, str]]:
    """Keep a user turn so providers that reject system-only chats stay usable."""
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Return the JSON array now."},
    ]


def _prompt(identity: str, current: list[dict[str, Any]], events: list[dict[str, Any]],
            related: list[dict[str, Any]] | None = None) -> str:
    public_events = [{key: item[key] for key in ("source_id", "ingest_sequence", "occurred_at", "actor", "kind", "text")} for item in events]
    related = related or []
    return (
        "You are the same character maintaining your derived topic-memory dossiers. "
        "Return only a JSON array of legal operations accepted by update_memory_dossier. "
        "Use only supplied event IDs. Keep plans, cancellations, reports, hypotheticals, "
        "assistant suggestions, observations, user statements and inference distinct. "
        "Do not turn feelings into user facts. Duplicate mentions of one experience must "
        "share one occurrence; if uncertain, leave the material evidence_only by returning []. "
        "Every edit of an existing dossier needs its current expected_revision. Causes are tentative. "
        "Related dossiers already cite this batch's evidence: reuse those dossier_id values. "
        "Titles, aliases and member lists are presentation only and are not idempotency keys. "
        "Do not invent extra dossiers for one experience. If a related understanding is stale, "
        "revise or retire it instead of leaving the old conclusion in recall.\n"
        f"Character context (frozen): {identity}\n"
        "Related dossiers for this batch's evidence: " +
        json.dumps(related, ensure_ascii=False, separators=(",", ":")) + "\n"
        "Current dossiers (bounded; IDs and revisions are authoritative): " +
        json.dumps(current, ensure_ascii=False, separators=(",", ":")) + "\nEvents: " +
        json.dumps(public_events, ensure_ascii=False, separators=(",", ":"))
    )


def _parse(raw: str) -> list[dict[str, Any]]:
    cleaned = str(raw or "").strip().removeprefix("```json").removesuffix("```").strip()
    value = json.loads(cleaned)
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(item, dict) for item in value):
        raise ValueError("invalid_model_patch")
    return value


def _processing_items(events: list[dict[str, Any]], operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outcome = "attach" if operations else "evidence_only"
    return [{key: item[key] for key in ("store_kind", "source_id", "source_revision", "ingest_sequence", "input_digest")} |
            {"semantic_outcomes": [outcome]} for item in events]


async def _run_claimed(principal: TaskPrincipal, lease, cfg: dict[str, Any],
                       *, operator_pass: bool = False) -> dict[str, Any]:
    from core.agent_runtime import task_manager, work_sessions
    from core.memory import dossiers
    from core.model_registry import resolve_category_info

    scope = MemoryScope.reality_scope(principal.uid, principal.char_id)
    events = dossiers.maintenance_candidates(scope, limit=int(cfg["batch_size"]), max_chars=int(cfg["max_input_chars"]))
    context = json.dumps({"source_count": len(events), "from": events[0]["ingest_sequence"] if events else None,
                          "to": events[-1]["ingest_sequence"] if events else None}, separators=(",", ":"))
    session = work_sessions.create_work_session(principal, task_id=lease.task_id,
        capability=CAPABILITY, artifact_kind=ARTIFACT_KIND, context=context,
        idempotency_key=f"memory:{lease.task_id}:{events[-1]['ingest_sequence'] if events else 'empty'}")
    if session.get("status") == "failed":
        session = work_sessions.retry_work_session(
            principal, session["work_session_id"], lease=lease,
        )
    session = work_sessions.start_work_session(principal, session["work_session_id"])
    if not events:
        work_sessions.complete_work_session(principal, session["work_session_id"],
                                             artifact_id=f"memory:{lease.task_id}", artifact_version=1)
        return task_manager.complete_task(
            principal,
            lease,
            result_metadata={"outcome_code": "no_new_evidence", "counters": {"processed": 0, "model_calls": 0}},
        )
    _, day_start = _day_bounds()
    scope_budget = dossiers.maintenance_budget(scope, since=day_start)
    if (int(scope_budget["calls"]) >= int(cfg["per_scope_daily_calls"])
            or int(scope_budget["tokens"]) + int(cfg["max_tokens_per_call"]) > int(cfg["per_scope_daily_tokens"])):
        work_sessions.fail_work_session(principal, session["work_session_id"], error_code="scope_budget_exhausted")
        return task_manager.fail_task(principal, lease, error_code="scope_budget_exhausted", retry=True,
                                      retry_delay_seconds=int(cfg["retry_backoff_seconds"]))
    if _foreground_active(principal.uid, cfg):
        work_sessions.fail_work_session(principal, session["work_session_id"], error_code="foreground_active")
        return task_manager.fail_task(principal, lease, error_code="foreground_active", retry=True,
                                      retry_delay_seconds=int(cfg["retry_backoff_seconds"]))
    identity, identity_revision = _identity_context(principal.char_id)
    current = [{**item, "summary": str(item.get("summary") or "")[:320]}
               for item in dossiers.search(scope, "", limit=20)]
    related = dossiers.related_dossiers_for_sources(scope, events)
    prompt = _prompt(identity, current, events, related)
    model_info = resolve_category_info("consolidation", char_id=principal.char_id)
    preset = cfg["background_preset"] or str(model_info.get("effective_preset") or "")
    model = str(model_info.get("model") or "")
    if cfg["background_preset"]:
        from core.config_loader import get_config
        preset_cfg = ((get_config().get("model_presets") or {}).get("presets") or {}).get(preset)
        if not isinstance(preset_cfg, dict):
            work_sessions.fail_work_session(
                principal, session["work_session_id"], error_code="background_preset_invalid",
            )
            return task_manager.fail_task(
                principal, lease, error_code="background_preset_invalid", retry=False,
            )
        model = str(preset_cfg.get("model") or "")
    run_id = uuid.uuid4().hex
    dossiers.begin_maintenance_run(scope, run_id=run_id, task_id=lease.task_id,
        work_session_id=session["work_session_id"], input_count=len(events), input_chars=len(prompt),
        token_budget=int(cfg["max_tokens_per_call"]), model=model, preset=preset,
        identity_revision=identity_revision, prompt_revision=PROMPT_REVISION)
    _reserve_global_budget(cfg); started = time.monotonic()
    try:
        from core import llm_client
        raw = await asyncio.wait_for(llm_client.chat(
            _request_messages(prompt), max_tokens_override=int(cfg["max_tokens_per_call"]),
            call_category="consolidation", char_id=principal.char_id,
            preset_name=cfg["background_preset"] or None,
        ), timeout=float(cfg["call_timeout_seconds"]))
        operations = _parse(raw)
    except Exception as exc:
        elapsed = time.monotonic() - started; code = "provider_limited" if "429" in str(exc) or "rate" in str(exc).lower() else "model_error"
        dossiers.finish_maintenance_run(scope, run_id, status="failed", error_code=code, wall_seconds=elapsed)
        _record_wall(elapsed, error=code, backoff_seconds=int(cfg["retry_backoff_seconds"]))
        work_sessions.fail_work_session(principal, session["work_session_id"], error_code=code)
        try:
            return task_manager.fail_task(principal, lease, error_code=code, retry=True,
                                          retry_delay_seconds=int(cfg["retry_backoff_seconds"]))
        except Exception as lease_error:
            # The provider can outlive the task lease. Preserve the failed
            # maintenance receipt without crashing the worker process; the
            # runtime reconciler will mark the task outcome_unknown safely.
            logger.warning("maintenance task lease lost after model failure: %s", lease_error)
            return {"status": "outcome_unknown", "task_id": lease.task_id,
                    "error_code": "lease_lost_after_model_failure"}
    elapsed = time.monotonic() - started; _record_wall(elapsed)
    current = task_manager.get_task(principal, lease.task_id)
    if current.get("cancel_requested"):
        dossiers.finish_maintenance_run(scope, run_id, status="canceled", wall_seconds=elapsed)
        work_sessions.cancel_work_session(principal, session["work_session_id"])
        return task_manager.acknowledge_cancel(principal, lease)
    if current.get("pause_requested"):
        dossiers.finish_maintenance_run(scope, run_id, status="paused", wall_seconds=elapsed)
        work_sessions.cancel_work_session(principal, session["work_session_id"])
        return task_manager.acknowledge_pause(principal, lease)
    fresh_cfg = config()
    if fresh_cfg["paused"]:
        dossiers.finish_maintenance_run(
            scope, run_id, status="paused", error_code="control_paused", wall_seconds=elapsed,
        )
        work_sessions.fail_work_session(
            principal, session["work_session_id"], error_code="control_paused",
        )
        return task_manager.fail_task(
            principal, lease, error_code="control_paused", retry=True,
            retry_delay_seconds=int(cfg["retry_backoff_seconds"]),
        )
    grant_changed = int(fresh_cfg["grant_revision"]) != int(cfg["grant_revision"])
    scheduler_disabled = (not operator_pass) and (not fresh_cfg["enabled"])
    if grant_changed or scheduler_disabled:
        dossiers.finish_maintenance_run(scope, run_id, status="revoked", error_code="grant_changed", wall_seconds=elapsed)
        work_sessions.fail_work_session(principal, session["work_session_id"], error_code="grant_changed")
        return task_manager.fail_task(principal, lease, error_code="grant_changed", retry=False)
    operation_id = hashlib.sha256((lease.task_id + ":" + ":".join(item["source_revision"] for item in events)).encode()).hexdigest()[:32]
    try:
        result = dossiers.apply_operations(scope, operations, operation_id=operation_id,
            actor=f"character:{principal.char_id}", chain="maintenance",
            processing_items=_processing_items(events, operations), maintenance_run_id=run_id,
            maintenance_wall_seconds=elapsed)
    except Exception as exc:
        dossiers.finish_maintenance_run(scope, run_id, status="failed", error_code=getattr(exc, "code", "commit_error"), wall_seconds=elapsed)
        work_sessions.fail_work_session(principal, session["work_session_id"], error_code="commit_error")
        return task_manager.fail_task(principal, lease, error_code="commit_error", retry=True,
                                      retry_delay_seconds=int(cfg["retry_backoff_seconds"]))
    checkpoint = max(item["ingest_sequence"] for item in events)
    work_sessions.complete_work_session(principal, session["work_session_id"],
        artifact_id=f"memory:{lease.task_id}", artifact_version=max(1, checkpoint))
    quality_hits = 0
    try:
        from core.memory import history_reconciliation
        quality = history_reconciliation._inspect_quality(operations, events, related)
        quality_hits = sum(1 for hit in (quality.get("hits") or {}).values() if hit)
    except Exception:
        quality_hits = 0
    return task_manager.complete_task(principal, lease, result_metadata={
        "outcome_code": "consolidation_committed",
        "artifact_ids": [f"memory:{lease.task_id}"],
        "counters": {"processed": len(events), "operations": len(result["results"]),
                     "coverage_ingest_sequence": checkpoint,
                     "input_tokens": max(1, (len(prompt) + 3) // 4),
                     "output_tokens": max(0, (len(str(raw or "")) + 3) // 4),
                     "wall_seconds": round(elapsed, 4),
                     "quality_hits": quality_hits},
    })


def _discover_scopes() -> list[TaskPrincipal]:
    from core.agent_runtime import task_store
    from core.asset_registry import get_registry
    values: set[tuple[str, str]] = set()
    for path in task_store.iter_scope_paths():
        scope = task_store.scope_from_path(path)
        if scope: values.add(scope)
    for character in get_registry().list_all("character"):
        root = get_paths().memory_char_root(char_id=character.id)
        if root.exists():
            for directory in root.iterdir():
                if directory.is_dir() and (directory / "event_store.sqlite3").is_file():
                    values.add((directory.name, character.id))
    return [TaskPrincipal.reality(uid, char_id) for uid, char_id in sorted(values)]


def _backoff_active() -> bool:
    with _state_lock: return time.time() < float(_state().get("backoff_until") or 0)


def _reconcile_unknown(principal: TaskPrincipal) -> None:
    """Resolve only outcomes proven by an atomically committed dossier receipt."""
    from core.agent_runtime import task_manager, work_sessions
    from core.memory import dossiers

    scope = MemoryScope.reality_scope(principal.uid, principal.char_id)
    for task in task_manager.list_tasks(principal, limit=100):
        if task["capability"] != CAPABILITY or task["status"] != "outcome_unknown":
            continue
        run = dossiers.committed_maintenance_run(scope, task["task_id"])
        if run is None:
            latest = dossiers.latest_maintenance_run(scope, task["task_id"])
            if latest and latest.get("status") in {"failed", "canceled", "paused", "revoked"}:
                try:
                    work_sessions.reconcile_unknown_work_session(
                        principal, latest["work_session_id"], artifact_id="", artifact_version=0,
                        succeeded=False, error_code=str(latest.get("error_code") or "outcome_unverified"),
                    )
                except work_sessions.WorkSessionError:
                    pass
                task_manager.reconcile_outcome_unknown(
                    principal, task["task_id"], succeeded=False,
                    error_code=str(latest.get("error_code") or "outcome_unverified"),
                    result_metadata={"outcome_code": "durable_failure_reconciled"},
                )
            continue
        checkpoint = dossiers.maintenance_checkpoint(scope)
        artifact_id = f"memory:{task['task_id']}"
        try:
            work_sessions.reconcile_unknown_work_session(
                principal,
                run["work_session_id"],
                artifact_id=artifact_id,
                artifact_version=max(1, checkpoint),
            )
        except work_sessions.WorkSessionError:
            logger.warning(
                "[memory_consolidation] committed receipt has no reconcilable session task=%s",
                task["task_id"],
            )
            continue
        task_manager.reconcile_outcome_unknown(
            principal,
            task["task_id"],
            succeeded=True,
            result_metadata={"outcome_code": "durable_receipt_reconciled"},
        )


async def run_operator_pass(
    principal: TaskPrincipal,
    *,
    preset_override: str | None = None,
    stop_at: float | None = None,
) -> dict[str, Any]:
    """Run one bounded same-scope pass for an explicit operator action.

    This bypasses scheduler enablement and the night window, but keeps grant,
    pause, budget, backoff, and foreground yielding. It never sends or captures
    a conversation message and never flips ``memory_consolidation.enabled``.
    """
    cfg = config()
    if preset_override:
        cfg["background_preset"] = str(preset_override)[:128]
    if cfg["paused"]:
        return {"status": "paused", "model_calls": 0, "reason": cfg["pause_reason"] or "paused"}
    if stop_at is not None and time.time() >= float(stop_at):
        return {"status": "stopped", "model_calls": 0, "reason": "stop_deadline_passed"}
    if _backoff_active():
        return {"status": "backoff", "model_calls": 0}
    if not _global_budget_allows(cfg):
        return {"status": "budget_exhausted", "model_calls": 0}
    if _GLOBAL_SEMAPHORE.locked():
        return {"status": "busy", "model_calls": 0}
    from core.agent_runtime import task_manager
    async with _GLOBAL_SEMAPHORE:
        async with _scope_lock(principal.uid, principal.char_id):
            _reconcile_unknown(principal)
            if _foreground_active(principal.uid, cfg):
                return {"status": "foreground_active", "model_calls": 0}
            scope = MemoryScope.reality_scope(principal.uid, principal.char_id)
            events = __import__("core.memory.dossiers", fromlist=["maintenance_candidates"]).maintenance_candidates(
                scope, limit=int(cfg["batch_size"]), max_chars=int(cfg["max_input_chars"]))
            tasks = task_manager.list_tasks(principal, limit=100)
            if any(item["capability"] == CAPABILITY and item["status"] == "outcome_unknown" for item in tasks):
                return {"status": "outcome_unknown", "model_calls": 0}
            queued = [item for item in tasks if item["capability"] == CAPABILITY and item["status"] == "queued"]
            if not queued and events:
                high = events[-1]["ingest_sequence"]
                task_manager.create_task(
                    principal, capability=CAPABILITY, source=OPERATOR_SOURCE,
                    idempotency_key=f"first-night:{principal.char_id}:{principal.uid}:{high}",
                    ttl_seconds=86400, retry_policy="safe", max_attempts=3,
                    request_context={"through": high},
                    request_summary={"source_count": len(events), "through": high},
                )
            lease_seconds = min(900, int(cfg["call_timeout_seconds"]) + 120)
            lease = task_manager.claim_next(principal, capabilities={CAPABILITY}, lease_seconds=lease_seconds)
            if lease is None:
                return {"status": "no_work", "model_calls": 0}
            result = await _run_claimed(principal, lease, cfg, operator_pass=True)
            return {"status": result["status"], "task_id": result["task_id"],
                    "model_calls": 1 if events else 0}


async def tick(*, now: datetime | None = None, only_principal: TaskPrincipal | None = None,
               preset_override: str | None = None) -> dict[str, Any]:
    """Admit at most one fair scope and one model call per scheduler tick."""
    cfg = config()
    if preset_override:
        cfg["background_preset"] = str(preset_override)[:128]
    if not cfg["enabled"]: return {"status": "disabled", "model_calls": 0}
    if cfg["paused"]: return {"status": "paused", "model_calls": 0}
    if not _night_window(cfg, now): return {"status": "outside_window", "model_calls": 0}
    if _backoff_active(): return {"status": "backoff", "model_calls": 0}
    if not _global_budget_allows(cfg): return {"status": "budget_exhausted", "model_calls": 0}
    if _GLOBAL_SEMAPHORE.locked(): return {"status": "busy", "model_calls": 0}
    from core.agent_runtime import task_manager
    principals = [only_principal] if only_principal is not None else _discover_scopes()
    if not principals: return {"status": "no_scope", "model_calls": 0}
    with _state_lock:
        state = _state(); cursor = int(state.get("round_robin_cursor") or 0) % len(principals)
        principals = principals[cursor:] + principals[:cursor]
        state["round_robin_cursor"] = (cursor + 1) % len(principals); _save_state(state)
    async with _GLOBAL_SEMAPHORE:
        for principal in principals:
            async with _scope_lock(principal.uid, principal.char_id):
                _reconcile_unknown(principal)
                if _foreground_active(principal.uid, cfg): continue
                scope = MemoryScope.reality_scope(principal.uid, principal.char_id)
                events = __import__("core.memory.dossiers", fromlist=["maintenance_candidates"]).maintenance_candidates(
                    scope, limit=int(cfg["batch_size"]), max_chars=int(cfg["max_input_chars"]))
                tasks = task_manager.list_tasks(principal, limit=100)
                if any(item["capability"] == CAPABILITY and item["status"] == "outcome_unknown"
                       for item in tasks):
                    continue
                queued = [item for item in tasks if item["capability"] == CAPABILITY and item["status"] == "queued"]
                if not queued and events:
                    high = events[-1]["ingest_sequence"]
                    task_manager.create_task(principal, capability=CAPABILITY, source="maintenance_trigger",
                        idempotency_key=f"auto:{principal.char_id}:{principal.uid}:{high}", ttl_seconds=86400,
                        retry_policy="safe", max_attempts=3, request_context={"through": high},
                        request_summary={"source_count": len(events), "through": high})
                lease_seconds = min(900, int(cfg["call_timeout_seconds"]) + 120)
                lease = task_manager.claim_next(
                    principal, capabilities={CAPABILITY}, lease_seconds=lease_seconds,
                )
                if lease is None: continue
                result = await _run_claimed(principal, lease, cfg)
                return {"status": result["status"], "task_id": result["task_id"], "model_calls": 1 if events else 0}
    return {"status": "no_work", "model_calls": 0}


def _effective_reason(cfg: dict[str, Any]) -> str:
    if not cfg["enabled"]: return "disabled"
    if cfg["paused"]: return cfg["pause_reason"] or "paused"
    if not _night_window(cfg): return "outside_night_window"
    if _backoff_active(): return "failure_backoff"
    if not _global_budget_allows(cfg): return "global_budget_exhausted"
    return "ready"


def runtime_snapshot(*, uid: str | None = None, char_id: str | None = None) -> dict[str, Any]:
    cfg = config(); state = _state(); day, _ = _day_bounds(); usage = (state.get("days") or {}).get(day, {})
    reason = _effective_reason(cfg)
    result = {"schema_version": "memory-consolidation-status.v1", "configured": bool(cfg["enabled"]),
        "effective": reason == "ready", "effective_reason": reason,
        "paused": bool(cfg["paused"]), "pause_reason": cfg["pause_reason"],
        "grant_revision": cfg["grant_revision"],
        "night_window": {"start_hour": cfg["night_start_hour"], "end_hour": cfg["night_end_hour"]},
        "idle_seconds": cfg["idle_seconds"], "budgets": {"calls": int(usage.get("calls") or 0),
        "call_limit": cfg["daily_call_budget"], "tokens": int(usage.get("tokens") or 0),
        "token_limit": cfg["daily_token_budget"], "wall_seconds": float(usage.get("wall_seconds") or 0),
        "wall_limit": cfg["daily_wall_seconds"]}, "backoff_until": float(state.get("backoff_until") or 0),
        "last_error": str(state.get("last_error") or ""), "global_worker_limit": 1,
        "limits": {"batch_size": cfg["batch_size"], "max_input_chars": cfg["max_input_chars"],
                   "max_tokens_per_call": cfg["max_tokens_per_call"]},
        "background_preset": cfg["background_preset"],
    }
    result["budgets"].update({
        "calls_remaining": max(0, cfg["daily_call_budget"] - result["budgets"]["calls"]),
        "tokens_remaining": max(0, cfg["daily_token_budget"] - result["budgets"]["tokens"]),
        "wall_seconds_remaining": max(0.0, cfg["daily_wall_seconds"] - result["budgets"]["wall_seconds"]),
    })
    if uid is not None or char_id is not None:
        if not uid or not char_id:
            raise ValueError("uid_and_char_id_required")
        from core.agent_runtime import task_manager, work_sessions
        from core.memory import dossiers
        principal = TaskPrincipal.reality(uid, char_id)
        tasks = task_manager.list_tasks(principal, capability=CAPABILITY, limit=100)
        sessions = [item for item in work_sessions.list_work_sessions(principal, limit=100)
                    if item["capability"] == CAPABILITY]
        task_counts: dict[str, int] = {}
        for item in tasks: task_counts[item["status"]] = task_counts.get(item["status"], 0) + 1
        session_counts: dict[str, int] = {}
        for item in sessions: session_counts[item["status"]] = session_counts.get(item["status"], 0) + 1
        result["scope"] = {"char_id": char_id, "realm": "reality"}
        result["scope_status"] = dossiers.maintenance_status(MemoryScope.reality_scope(uid, char_id))
        result["scope_status"]["task_status_counts"] = dict(sorted(task_counts.items()))
        result["scope_status"]["work_session_status_counts"] = dict(sorted(session_counts.items()))
        result["scope_status"]["unverified"] += int(task_counts.get("outcome_unknown", 0))
    return result


def reset_failure_backoff() -> None:
    with _state_lock:
        state = _state()
        state["consecutive_failures"] = 0
        state["last_error"] = ""
        state["backoff_until"] = 0.0
        _save_state(state)


def control_scope(principal: TaskPrincipal, action: str) -> dict[str, int]:
    """Apply an explicit task lifecycle action without touching memory content."""
    from core.agent_runtime import task_manager, work_sessions

    if action not in {"pause", "resume", "revoke", "recover_unknown"}:
        raise ValueError("invalid_control_action")
    unknown_before = sum(
        item["status"] == "outcome_unknown"
        for item in task_manager.list_tasks(principal, capability=CAPABILITY, limit=100)
    )
    _reconcile_unknown(principal)
    unknown_after = sum(
        item["status"] == "outcome_unknown"
        for item in task_manager.list_tasks(principal, capability=CAPABILITY, limit=100)
    )
    counts = {
        "changed": 0,
        "receipts_reconciled": max(0, unknown_before - unknown_after),
        "unknown_failed": 0,
    }
    tasks = task_manager.list_tasks(principal, capability=CAPABILITY, limit=100)
    sessions = work_sessions.list_work_sessions(principal, limit=100)
    sessions_by_task = {item["task_id"]: item for item in sessions if item["capability"] == CAPABILITY}
    for task in tasks:
        before = task["status"]
        if action == "pause" and before in {"created", "queued", "running"}:
            task_manager.request_pause(principal, task["task_id"])
        elif action == "resume" and before == "paused":
            task_manager.resume_task(principal, task["task_id"])
        elif action == "revoke" and before not in {"succeeded", "failed", "canceled", "expired", "outcome_unknown"}:
            task_manager.request_cancel(principal, task["task_id"], reason_code="grant_revoked")
        elif action == "recover_unknown" and before == "outcome_unknown":
            session = sessions_by_task.get(task["task_id"])
            if session and session["status"] == "outcome_unknown":
                work_sessions.reconcile_unknown_work_session(
                    principal, session["work_session_id"], artifact_id="", artifact_version=0,
                    succeeded=False, error_code="outcome_unverified",
                )
            task_manager.reconcile_outcome_unknown(
                principal, task["task_id"], succeeded=False, error_code="outcome_unverified",
                result_metadata={"outcome_code": "outcome_unverified"},
            )
            counts["unknown_failed"] += 1
        after = task_manager.get_task(principal, task["task_id"])["status"]
        counts["changed"] += int(after != before)
    if action == "recover_unknown":
        reset_failure_backoff()
    return counts
