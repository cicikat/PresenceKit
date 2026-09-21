"""Small bounded durable store for autonomy config, jobs, source state and audit runs."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from threading import Lock, RLock

from core.autonomy.models import Job, Opportunity, Run, Signal, evaluation_status_for
from core.safe_write import safe_write_json
from core.sandbox import get_paths

MAX_JOBS = 60
MAX_RUNS = 100
MAX_PENDING_SIGNALS = 120
LEASE_SECONDS = 90
_DREAM_ONE_SHOT_SOURCES = frozenset({"desktop_wake"})
_scope_locks: dict[tuple[str, str], RLock] = {}
_scope_locks_guard = Lock()

DEFAULT_CONFIG = {
    "enabled": False,
    "talk_enabled": True,
    "daily_evaluation_budget": 96,
    "min_interval_seconds": 15 * 60,
    "max_steps": 4,
    "max_tools": 4,
    "max_write_tools": 1,
    "total_timeout_seconds": 120,
    "tool_timeout_seconds": 30,
    "circuit_failure_threshold": 3,
    "circuit_cooldown_seconds": 3600,
    "schedule": {"enabled": False, "time": "12:00", "weekdays": list(range(7)), "timezone": "local", "window": [], "restart_miss_policy": "skip"},
    "interval": {"enabled": False, "seconds": 6 * 3600},
    "overflow": {"enabled": False, "threshold": 1.6},
    "tools": {},
}


def _path(uid: str, char_id: str):
    return get_paths().autonomy_state(uid, char_id=char_id)


def _scope_lock(uid: str, char_id: str) -> RLock:
    key = (str(uid), str(char_id))
    with _scope_locks_guard:
        lock = _scope_locks.get(key)
        if lock is None:
            lock = RLock()
            _scope_locks[key] = lock
        return lock


def _default() -> dict:
    return {
        "config": deepcopy(DEFAULT_CONFIG),
        "jobs": [],
        "runs": [],
        "sources": {},
        "pending_signals": [],
        "delivered_correlations": [],
        "daily": {"day": "", "evaluations": 0, "tools": 0, "talks": 0},
        "circuit": {"consecutive_failures": 0, "open_until": 0.0},
    }


def _load_unlocked(uid: str, char_id: str) -> dict:
    data = _default()
    try:
        path = _path(uid, char_id)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key in data:
                    if key in raw and isinstance(raw[key], type(data[key])):
                        data[key] = raw[key]
                if isinstance(raw.get("config"), dict):
                    merged = deepcopy(DEFAULT_CONFIG)
                    merged.update(raw["config"])
                    for section in ("schedule", "interval", "overflow"):
                        if isinstance(raw["config"].get(section), dict):
                            merged[section] = {**DEFAULT_CONFIG[section], **raw["config"][section]}
                    data["config"] = merged
    except Exception:
        # Config/state read failure is fail-safe: no autonomous start is admitted.
        data["config"]["enabled"] = False
    return data


def load(uid: str, char_id: str) -> dict:
    with _scope_lock(uid, char_id):
        return _load_unlocked(uid, char_id)


def _save_unlocked(uid: str, char_id: str, state: dict) -> bool:
    state["jobs"] = list(state.get("jobs", []))[-MAX_JOBS:]
    state["runs"] = list(state.get("runs", []))[-MAX_RUNS:]
    state["pending_signals"] = list(state.get("pending_signals", []))[-MAX_PENDING_SIGNALS:]
    state["delivered_correlations"] = list(state.get("delivered_correlations", []))[-MAX_RUNS:]
    path = _path(uid, char_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return bool(safe_write_json(path, state))


def save(uid: str, char_id: str, state: dict) -> bool:
    with _scope_lock(uid, char_id):
        return _save_unlocked(uid, char_id, state)


def replace_config(uid: str, char_id: str, config: dict) -> bool:
    """Replace only the config partition without overwriting concurrent jobs."""
    with _scope_lock(uid, char_id):
        state = _load_unlocked(uid, char_id)
        state["config"] = deepcopy(config)
        return _save_unlocked(uid, char_id, state)


def enqueue_signal(uid: str, char_id: str, signal: Signal, *, dedupe_key: str = "") -> tuple[bool, str]:
    """Persist a trigger fact for the next autonomy opportunity.

    Trigger producers use this small queue instead of constructing prompts or
    starting an assistant turn.  Duplicate facts in the same time bucket are
    collapsed before the autonomy runner merges the queue.
    """
    if not isinstance(signal, Signal):
        raise TypeError("signal must be a Signal")
    with _scope_lock(uid, char_id):
        state = _load_unlocked(uid, char_id)
        key = str(dedupe_key or signal.signal_id)
        for raw in state.get("pending_signals", []):
            if str(raw.get("dedupe_key") or "") == key:
                return False, "duplicate"
        state.setdefault("pending_signals", []).append({
            "dedupe_key": key,
            "signal": signal.to_dict(),
            "queued_at": time.time(),
        })
        return bool(_save_unlocked(uid, char_id, state)), "queued"


def drain_pending_signals(uid: str, char_id: str) -> list[Signal]:
    """Claim and remove queued producer facts for one autonomy tick."""
    with _scope_lock(uid, char_id):
        state = _load_unlocked(uid, char_id)
        pending = list(state.get("pending_signals", []))
        if not pending:
            return []
        state["pending_signals"] = []
        _save_unlocked(uid, char_id, state)
    result: list[Signal] = []
    for item in pending:
        raw = item.get("signal") if isinstance(item, dict) else None
        try:
            if isinstance(raw, dict):
                result.append(Signal.from_dict(raw))
        except (TypeError, ValueError):
            continue
    return result


def discard_pending_signals_by_source(
    uid: str, char_id: str, sources: set[str] | frozenset[str]
) -> list[Signal]:
    """Remove one-shot pending facts for sources that must never replay later."""
    wanted = {str(source) for source in sources}
    with _scope_lock(uid, char_id):
        state = _load_unlocked(uid, char_id)
        kept: list[dict] = []
        discarded: list[Signal] = []
        for item in state.get("pending_signals", []):
            raw = item.get("signal") if isinstance(item, dict) else None
            try:
                signal = Signal.from_dict(raw) if isinstance(raw, dict) else None
            except (TypeError, ValueError):
                signal = None
            if signal is not None and signal.source in wanted:
                discarded.append(signal)
            else:
                kept.append(item)
        if len(kept) != len(state.get("pending_signals", [])):
            state["pending_signals"] = kept
            _save_unlocked(uid, char_id, state)
        return discarded


def record_signal_outcome(
    uid: str,
    char_id: str,
    signal: Signal,
    *,
    disposition: str,
    event_status: str,
) -> None:
    """Keep a bounded lifecycle record for a signal discarded before merging."""
    opportunity = Opportunity(
        signals=[signal.to_dict()],
        priority=max(float(signal.priority), float(signal.urgency or 0.0)),
        reason=signal.reason,
        expiry=signal.expiry,
        memory_query=[signal.memory_query] if signal.memory_query not in (None, "", []) else [],
        action_mode=signal.action_mode,
        created_at=signal.created_at,
        id=f"signal:{signal.signal_id}",
        urgency=float(signal.urgency or 0.0),
        confidence=float(signal.confidence or 0.0),
        suggested_action=signal.suggested_action,
    )
    job = Job(
        uid=str(uid),
        char_id=str(char_id),
        source="autonomy",
        created_at=signal.created_at,
        ttl_seconds=max(60, min(int(max(0.0, signal.expiry - signal.created_at)), 3600)),
        dedupe_key=f"terminal:{signal.signal_id}",
        status="done",
        opportunity=opportunity.to_dict(),
        signal_sources=[signal.source],
    )
    finished_at = time.time()
    run = Run(
        uid=str(uid),
        char_id=str(char_id),
        source="autonomy",
        job_id=job.id,
        finished_at=finished_at,
        disposition=disposition,
        events=[{"status": event_status, "signal_id": signal.signal_id, "source": signal.source}],
        opportunity_id=opportunity.id,
        signal_count=1,
        evaluation_status=("expired" if disposition == "expired" else evaluation_status_for(disposition)),
    )
    with _scope_lock(uid, char_id):
        state = _load_unlocked(uid, char_id)
        state["jobs"].append(job.to_dict())
        state["runs"].append(run.to_dict())
        _save_unlocked(uid, char_id, state)


def claim_delivery_correlation(uid: str, char_id: str, correlation_id: str) -> bool:
    """Return true only for the first talk attempt for one opportunity."""
    correlation_id = str(correlation_id or "").strip()
    if not correlation_id:
        return True
    with _scope_lock(uid, char_id):
        state = _load_unlocked(uid, char_id)
        delivered = state.setdefault("delivered_correlations", [])
        if correlation_id in delivered:
            return False
        delivered.append(correlation_id)
        return bool(_save_unlocked(uid, char_id, state))


def enqueue(
    uid: str,
    char_id: str,
    source: str,
    *,
    dedupe_key: str = "",
    ttl_seconds: int = 20 * 60,
    opportunity: Opportunity | dict | None = None,
) -> tuple[Job | None, str]:
    with _scope_lock(uid, char_id):
        state = _load_unlocked(uid, char_id)
        for raw in state["jobs"]:
            if raw.get("status") in {"pending", "processing"} and raw.get("dedupe_key") and raw.get("dedupe_key") == dedupe_key:
                return None, "duplicate"
        opportunity_dict = opportunity.to_dict() if isinstance(opportunity, Opportunity) else (dict(opportunity) if isinstance(opportunity, dict) else {})
        signal_sources = sorted({str(item.get("source") or "") for item in opportunity_dict.get("signals", []) if isinstance(item, dict) and item.get("source")})
        job = Job(
            uid=str(uid), char_id=str(char_id), source=str(source), dedupe_key=dedupe_key,
            ttl_seconds=max(60, min(int(ttl_seconds), 3600)), opportunity=opportunity_dict,
            signal_sources=signal_sources,
        )
        state["jobs"].append(job.to_dict())
        _save_unlocked(uid, char_id, state)
        return job, "queued"


def enqueue_opportunity(
    uid: str,
    char_id: str,
    signals: list[Signal],
    *,
    dedupe_key: str = "",
    ttl_seconds: int = 20 * 60,
) -> tuple[Job | None, str]:
    """Merge all signals from one tick into exactly one durable autonomy job."""
    try:
        opportunity = Opportunity.merge(signals)
    except ValueError:
        # A restart or a slow queue can leave every candidate past its TTL.
        # Treat that as a normal admission miss, never as a replayable job.
        return None, "expired"
    return enqueue(
        uid,
        char_id,
        "autonomy",
        dedupe_key=dedupe_key or f"opportunity:{opportunity.id}",
        ttl_seconds=ttl_seconds,
        opportunity=opportunity,
    )


def claim_due(uid: str, char_id: str) -> Job | None:
    with _scope_lock(uid, char_id):
        state = _load_unlocked(uid, char_id)
        now = time.time()
        changed = False
        for raw in state["jobs"]:
            if raw.get("status") == "processing" and float(raw.get("lease_until") or 0) <= now:
                raw["status"] = "pending"; raw["lease_until"] = 0; changed = True
        for raw in state["jobs"]:
            if raw.get("status") != "pending":
                continue
            if float(raw.get("next_attempt_at") or 0) > now:
                continue
            job = Job.from_dict(raw)
            if now - job.created_at > job.ttl_seconds:
                raw["status"] = "done"; changed = True
                state["runs"].append(Run(
                    uid=uid, char_id=char_id, source=job.source, job_id=job.id,
                    disposition="expired", finished_at=now, opportunity_id=str((job.opportunity or {}).get("id") or ""),
                    signal_count=len(job.signal_sources), evaluation_status="expired",
                ).to_dict())
                continue
            raw["status"] = "processing"; raw["lease_until"] = now + LEASE_SECONDS; raw["lease_token"] = __import__("uuid").uuid4().hex; raw["attempts"] = int(raw.get("attempts") or 0) + 1
            _save_unlocked(uid, char_id, state)
            return Job.from_dict(raw)
        if changed:
            _save_unlocked(uid, char_id, state)
        return None


def renew(job: Job, *, seconds: int = LEASE_SECONDS) -> bool:
    """Extend only the claim that owns this job; stale workers fail closed."""
    with _scope_lock(job.uid, job.char_id):
        state = _load_unlocked(job.uid, job.char_id)
        for raw in state["jobs"]:
            if raw.get("id") != job.id:
                continue
            if raw.get("status") != "processing" or raw.get("lease_token") != job.lease_token:
                return False
            raw["lease_until"] = time.time() + max(10, int(seconds))
            return _save_unlocked(job.uid, job.char_id, state)
        return False


def finish(job: Job, run: Run, *, retry: bool = False) -> None:
    with _scope_lock(job.uid, job.char_id):
        state = _load_unlocked(job.uid, job.char_id)
        owns_claim = False
        for raw in state["jobs"]:
            if raw.get("id") == job.id:
                if job.lease_token and raw.get("lease_token") != job.lease_token:
                    # Another worker reclaimed this job after our lease expired.
                    # Preserve its lease/state; retain only the bounded audit run.
                    break
                owns_claim = True
                raw["status"] = "pending" if retry else "done"
                raw["lease_until"] = 0
                raw["lease_token"] = ""
                if retry:
                    # Bounded exponential retry for temporary guards. It is durable
                    # and never turns a denied talk into a tight per-tick loop.
                    attempts = max(1, int(raw.get("attempts") or 1))
                    raw["next_attempt_at"] = time.time() + min(30 * (2 ** (attempts - 1)), 15 * 60)
                else:
                    raw["next_attempt_at"] = 0
        state["runs"].append(run.to_dict())
        if owns_claim:
            _update_finish_counters(state, job, run)
        _save_unlocked(job.uid, job.char_id, state)


def finish_dream_blocked_with_signal_split(
    job: Job,
    run: Run,
    *,
    now: float | None = None,
) -> Job | None:
    """Finish a wake-containing Dream block and atomically queue non-wake signals."""
    if run.disposition not in {"blocked_dream", "blocked_dream_uncertain"}:
        raise ValueError("signal split requires a Dream-blocked run")
    now = time.time() if now is None else float(now)
    with _scope_lock(job.uid, job.char_id):
        state = _load_unlocked(job.uid, job.char_id)
        parent = next((raw for raw in state["jobs"] if raw.get("id") == job.id), None)
        owns_claim = bool(
            parent is not None
            and parent.get("status") == "processing"
            and job.lease_token
            and parent.get("lease_token") == job.lease_token
        )
        if not owns_claim:
            _append_run_once(state, run)
            _save_unlocked(job.uid, job.char_id, state)
            return None

        retryable: list[Signal] = []
        for index, raw_signal in enumerate((job.opportunity or {}).get("signals") or []):
            try:
                signal = Signal.from_dict(raw_signal)
            except (TypeError, ValueError) as exc:
                run.events.append({
                    "status": "signal_terminal_invalid",
                    "signal_index": index,
                    "outcome": "invalid",
                    "error": type(exc).__name__,
                })
                continue
            if signal.source in _DREAM_ONE_SHOT_SOURCES:
                run.events.append({
                    "status": "signal_terminal_one_shot",
                    "signal_id": signal.signal_id,
                    "source": signal.source,
                    "outcome": "not_replayed",
                })
            elif signal.expiry > 0 and signal.expiry <= now:
                run.events.append({
                    "status": "signal_terminal_expired",
                    "signal_id": signal.signal_id,
                    "source": signal.source,
                    "outcome": "expired",
                    "expires_at": signal.expiry,
                })
            else:
                retryable.append(signal)

        parent["status"] = "done"
        parent["lease_until"] = 0
        parent["lease_token"] = ""
        parent["next_attempt_at"] = 0

        child = _dream_retry_child(job, run, retryable, state=state, now=now)
        if child is not None:
            state["jobs"].append(child.to_dict())
            run.events.append({
                "status": "dream_retry_child_queued",
                "child_job_id": child.id,
                "child_opportunity_id": str((child.opportunity or {}).get("id") or ""),
                "signal_ids": [signal.signal_id for signal in retryable],
                "signal_sources": child.signal_sources,
            })
        elif retryable:
            for signal in retryable:
                run.events.append({
                    "status": "signal_terminal_expired",
                    "signal_id": signal.signal_id,
                    "source": signal.source,
                    "outcome": "parent_ttl_expired",
                })

        _append_run_once(state, run)
        _update_finish_counters(state, job, run)
        _save_unlocked(job.uid, job.char_id, state)
        return child


def _append_run_once(state: dict, run: Run) -> None:
    if not any(raw.get("id") == run.id for raw in state["runs"]):
        state["runs"].append(run.to_dict())


def _dream_retry_child(
    job: Job,
    run: Run,
    signals: list[Signal],
    *,
    state: dict,
    now: float,
) -> Job | None:
    if not signals:
        return None
    parent_remaining = float(job.created_at) + float(job.ttl_seconds) - now
    if parent_remaining <= 0:
        return None
    finite_signal_ttls = [signal.expiry - now for signal in signals if signal.expiry > 0]
    ttl_seconds = min([parent_remaining, *finite_signal_ttls])
    if ttl_seconds <= 0:
        return None
    opportunity = Opportunity.merge(signals, now=now)
    dedupe_key = f"dream-retry:{job.id}:{run.id}"
    if any(raw.get("dedupe_key") == dedupe_key for raw in state["jobs"]):
        return None
    attempts = max(1, int(job.attempts or 1))
    return Job(
        uid=job.uid,
        char_id=job.char_id,
        source="autonomy",
        created_at=now,
        ttl_seconds=ttl_seconds,
        dedupe_key=dedupe_key,
        next_attempt_at=now + min(30 * (2 ** (attempts - 1)), 15 * 60),
        opportunity=opportunity.to_dict(),
        signal_sources=sorted({signal.source for signal in signals}),
        retry_parent_job_id=job.id,
        retry_parent_run_id=run.id,
    )


def _update_finish_counters(state: dict, job: Job, run: Run) -> None:
    from core.autonomy.models import is_admission_only_disposition

    finished_at = run.finished_at or time.time()
    sources = job.signal_sources or [job.source]
    admission_only = is_admission_only_disposition(run.disposition)
    for source in sources:
        source_state = state.setdefault("sources", {}).setdefault(source, {})
        # Always record attempts for observability, but only real evaluations
        # advance last_evaluated_at / min_interval / interval due clocks.
        source_state["last_attempt_at"] = finished_at
        if not admission_only:
            source_state["last_evaluated_at"] = finished_at
            if source == "interval" and state["config"].get("interval", {}).get("enabled"):
                source_state["next_due_at"] = finished_at + int(state["config"]["interval"].get("seconds") or 0)
            elif source == "overflow":
                source_state["next_due_at"] = finished_at + int(state["config"].get("min_interval_seconds") or 0)
    roll_daily(state)
    if not admission_only:
        state["daily"]["evaluations"] += 1
        state["daily"]["tools"] += len(run.tool_names)
        state["daily"]["talks"] += int(run.talk_sent)
    circuit = state.setdefault("circuit", {"consecutive_failures": 0, "open_until": 0.0})
    if admission_only:
        return
    failure = run.disposition in {"tool_failed", "tool_outcome_unknown", "llm_failed", "timeout", "lease_lost"}
    if failure:
        circuit["consecutive_failures"] = int(circuit.get("consecutive_failures") or 0) + 1
        if circuit["consecutive_failures"] >= int(state["config"].get("circuit_failure_threshold") or 3):
            circuit["open_until"] = time.time() + int(state["config"].get("circuit_cooldown_seconds") or 3600)
    elif run.disposition.startswith("completed_") or run.disposition.startswith("talk_") or run.disposition == "canceled_by_user_activity":
        circuit.update({"consecutive_failures": 0, "open_until": 0.0})


def roll_daily(state: dict) -> None:
    from core.scheduler.rhythm import logical_day
    day = logical_day(__import__("datetime").datetime.fromtimestamp(time.time())).isoformat()
    daily = state.setdefault("daily", {})
    if daily.get("day") != day:
        daily.update({"day": day, "evaluations": 0, "tools": 0, "talks": 0})


def source_last_evaluated(state: dict, source: str) -> float:
    return float((state.get("sources", {}).get(source, {}) or {}).get("last_evaluated_at") or 0)


def circuit_open(state: dict, *, now: float | None = None) -> bool:
    return float((state.get("circuit") or {}).get("open_until") or 0) > (time.time() if now is None else now)
