"""Silent same-character dossier consolidation worker (Brief 258 D).

The scheduler is the only automatic entrypoint. It claims Task Manager work,
creates a bounded Work Session, calls the configured model without holding a
memory lock, then submits a validated patch to the shared dossier capability.
It never sends/captures a turn or touches the conversation slow queue.
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
PROMPT_REVISION = "memory-consolidation-prompt.v1"
_GLOBAL_SEMAPHORE = asyncio.Semaphore(1)
_scope_locks: dict[tuple[str, str], asyncio.Lock] = {}
_scope_locks_guard = threading.Lock()
_state_lock = threading.RLock()

_DEFAULTS: dict[str, Any] = {
    "enabled": False, "grant_revision": 1,
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


def _prompt(identity: str, current: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    public_events = [{key: item[key] for key in ("source_id", "ingest_sequence", "occurred_at", "actor", "kind", "text")} for item in events]
    return (
        "You are the same character maintaining your derived topic-memory dossiers. "
        "Return only a JSON array of legal operations accepted by update_memory_dossier. "
        "Use only supplied event IDs. Keep plans, cancellations, reports, hypotheticals, "
        "assistant suggestions, observations, user statements and inference distinct. "
        "Do not turn feelings into user facts. Duplicate mentions of one experience must "
        "share one occurrence; if uncertain, leave the material evidence_only by returning []. "
        "Every edit of an existing dossier needs its current expected_revision. Causes are tentative.\n"
        f"Character context (frozen): {identity}\n"
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


async def _run_claimed(principal: TaskPrincipal, lease, cfg: dict[str, Any]) -> dict[str, Any]:
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
    prompt = _prompt(identity, current, events)
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
            [{"role": "system", "content": prompt}], max_tokens_override=int(cfg["max_tokens_per_call"]),
            call_category="consolidation", char_id=principal.char_id,
            preset_name=cfg["background_preset"] or None,
        ), timeout=float(cfg["call_timeout_seconds"]))
        operations = _parse(raw)
    except Exception as exc:
        elapsed = time.monotonic() - started; code = "provider_limited" if "429" in str(exc) or "rate" in str(exc).lower() else "model_error"
        dossiers.finish_maintenance_run(scope, run_id, status="failed", error_code=code, wall_seconds=elapsed)
        _record_wall(elapsed, error=code, backoff_seconds=int(cfg["retry_backoff_seconds"]))
        work_sessions.fail_work_session(principal, session["work_session_id"], error_code=code)
        return task_manager.fail_task(principal, lease, error_code=code, retry=True,
                                      retry_delay_seconds=int(cfg["retry_backoff_seconds"]))
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
    if not fresh_cfg["enabled"] or int(fresh_cfg["grant_revision"]) != int(cfg["grant_revision"]):
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
    return task_manager.complete_task(principal, lease, result_metadata={
        "outcome_code": "consolidation_committed",
        "artifact_ids": [f"memory:{lease.task_id}"],
        "counters": {"processed": len(events), "operations": len(result["results"]),
                     "coverage_ingest_sequence": checkpoint},
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


async def tick(*, now: datetime | None = None) -> dict[str, Any]:
    """Admit at most one fair scope and one model call per scheduler tick."""
    cfg = config()
    if not cfg["enabled"]: return {"status": "disabled", "model_calls": 0}
    if not _night_window(cfg, now): return {"status": "outside_window", "model_calls": 0}
    if _backoff_active(): return {"status": "backoff", "model_calls": 0}
    if not _global_budget_allows(cfg): return {"status": "budget_exhausted", "model_calls": 0}
    if _GLOBAL_SEMAPHORE.locked(): return {"status": "busy", "model_calls": 0}
    from core.agent_runtime import task_manager
    principals = _discover_scopes()
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


def runtime_snapshot() -> dict[str, Any]:
    cfg = config(); state = _state(); day, _ = _day_bounds(); usage = (state.get("days") or {}).get(day, {})
    return {"schema_version": "memory-consolidation-status.v1", "configured": bool(cfg["enabled"]),
        "effective": bool(cfg["enabled"] and _night_window(cfg) and not _backoff_active()),
        "night_window": {"start_hour": cfg["night_start_hour"], "end_hour": cfg["night_end_hour"]},
        "idle_seconds": cfg["idle_seconds"], "budgets": {"calls": int(usage.get("calls") or 0),
        "call_limit": cfg["daily_call_budget"], "tokens": int(usage.get("tokens") or 0),
        "token_limit": cfg["daily_token_budget"], "wall_seconds": float(usage.get("wall_seconds") or 0),
        "wall_limit": cfg["daily_wall_seconds"]}, "backoff_until": float(state.get("backoff_until") or 0),
        "last_error": str(state.get("last_error") or ""), "global_worker_limit": 1}
