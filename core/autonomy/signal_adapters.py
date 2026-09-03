"""Pure adapters from scheduler/sensor facts to autonomy signals.

Adapters deliberately carry facts only.  They never construct assistant text,
call an LLM, mark a trigger, or send through a channel.
"""
from __future__ import annotations

import hashlib
import math
import time
from datetime import datetime
from typing import Any

from core.autonomy.models import ActionMode, ProactiveSignal


ROUTINE_TRIGGER_NAMES = frozenset({
    "morning_greeting",
    "night_reminder",
    "good_night",
    "midday",
    "random_message",
    "timenode",
})


# Trigger names that historically carried an assistant prompt.  They remain
# useful as observability labels, but their only runtime product is a bounded
# fact consumed by the autonomy runner.
_MIGRATED_TRIGGER_TTLS = {
    "hr_critical": 10 * 60,
    "hr_high": 10 * 60,
    "birthday_midnight": 60 * 60,
    "birthday_eve": 60 * 60,
    "birthday_afternoon": 60 * 60,
    "birthday_night": 60 * 60,
    "period_reminder": 30 * 60,
    "festival": 20 * 60,
}

_DESKTOP_WAKE_TTL_SECONDS = 10 * 60
_DESKTOP_WAKE_MAX_OFFLINE_SECONDS = 30 * 24 * 60 * 60


def registered_signal_adapter(trigger_name: str):
    """Return the producer registered for a migrated conversational trigger."""
    from core.scheduler.gating import trigger_migration_status
    return emit_trigger_signal if trigger_migration_status(trigger_name) == "migrated" else None


def emit_trigger_signal(
    uid: str,
    char_id: str,
    trigger_name: str,
    *,
    evidence: list[dict] | None = None,
    reason: str = "",
    priority: float = 0.2,
    urgency: float | None = None,
    confidence: float = 1.0,
    memory_query: str | dict | None = None,
    action_mode: str | None = None,
    now: float | None = None,
    dedupe_bucket_seconds: int = 15 * 60,
) -> tuple[bool, str]:
    """Queue one factual trigger candidate; never calls an LLM or channel."""
    now = time.time() if now is None else float(now)
    name = str(trigger_name or "").strip()
    if not name:
        return False, "missing_trigger"
    if name in ROUTINE_TRIGGER_NAMES and not routine_trigger_enabled(name):
        return False, "disabled"
    effective_action_mode = (
        ActionMode.NONE.value
        if action_mode is None and name in ROUTINE_TRIGGER_NAMES
        else (action_mode or ActionMode.TALK.value)
    )
    signal_evidence = list(evidence or [{"fact": "trigger_candidate", "trigger": name}])[:12]
    if name in ROUTINE_TRIGGER_NAMES:
        signal_evidence = _with_routine_key(signal_evidence, name)
    ttl = _MIGRATED_TRIGGER_TTLS.get(name, 20 * 60)
    signal = ProactiveSignal(
        source="sensor" if name.startswith("hr_") else "scheduler",
        reason=reason or f"A bounded {name} event is eligible for autonomy evaluation.",
        evidence=signal_evidence,
        created_at=now,
        expires_at=now + ttl,
        priority=priority,
        urgency=priority if urgency is None else urgency,
        confidence=confidence,
        memory_query=memory_query,
        action_mode=effective_action_mode,
        suggested_action="message" if effective_action_mode == ActionMode.TALK.value else "silent",
    )
    from core.autonomy import store
    prefix = "routine" if name in ROUTINE_TRIGGER_NAMES else "trigger"
    key = f"{prefix}:{name}:{int(now // max(60, dedupe_bucket_seconds))}"
    return store.enqueue_signal(uid, char_id, signal, dedupe_key=key)


def emit_scheduler_proposal_signal(
    uid: str,
    char_id: str,
    proposal: Any,
    *,
    now: float | None = None,
) -> tuple[bool, str]:
    """Persist the selected scheduler winner as bounded factual evidence.

    The scheduler has already applied its proposal competition and admission
    rules before this adapter is called.  This function deliberately ignores
    legacy executors and prompt factories: a winner becomes one autonomy
    signal, never another delivery path.
    """
    from core.scheduler.gating import canonical_trigger_name
    name = canonical_trigger_name(str(getattr(proposal, "trigger_name", "") or "").strip())
    if not name:
        return False, "missing_trigger"
    metadata = getattr(proposal, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    evidence = _scheduler_proposal_evidence(name, metadata)
    action_mode = str(metadata.get("autonomy_action_mode") or ActionMode.TALK.value)
    if action_mode not in {item.value for item in ActionMode}:
        action_mode = ActionMode.TALK.value
    memory_query = metadata.get("autonomy_memory_query")
    if not isinstance(memory_query, (str, dict)):
        memory_query = None
    try:
        urgency = min(1.0, max(0.1, float(getattr(proposal, "urgency", 0.2))))
    except (TypeError, ValueError):
        urgency = 0.2
    return emit_trigger_signal(
        uid,
        char_id,
        name,
        evidence=evidence,
        reason=f"Selected scheduler winner: {name}.",
        priority=urgency,
        urgency=urgency,
        memory_query=memory_query,
        action_mode=action_mode,
        now=now,
    )


def _scheduler_proposal_evidence(name: str, metadata: dict[str, Any]) -> list[dict]:
    """Return a small allowlisted projection rather than arbitrary metadata."""
    fields_by_trigger = {
        "festival": ("fact", "festival_key", "reality_date", "calendar_source"),
        "period_reminder": ("fact", "stage", "days_elapsed"),
        "dream_exit": ("fact", "dream_id"),
    }
    allowed = fields_by_trigger.get(name)
    raw = metadata.get("autonomy_evidence")
    if isinstance(raw, list) and allowed:
        projected: list[dict] = []
        for item in raw[:4]:
            if not isinstance(item, dict):
                continue
            row = {
                key: value
                for key, value in item.items()
                if key in allowed and isinstance(value, (str, int, float, bool))
            }
            if row:
                projected.append(row)
        if projected:
            return projected
    if name == "dream_exit":
        dream_id = str(metadata.get("dream_id") or "").strip()
        if dream_id:
            return [{"fact": "dream_exit_ready", "dream_id": dream_id[:128]}]
    return [{"fact": "scheduler_proposal_winner", "trigger": name}]


def adapt_routine(
    source: str,
    *,
    now: float | None = None,
    window: str = "",
    priority: float = 0.15,
) -> ProactiveSignal:
    now = time.time() if now is None else float(now)
    return ProactiveSignal(
        source=source,
        reason="routine",
        evidence=[{
            "fact": "configured_time_window",
            "source": source,
            "routine_key": source,
            "window": window,
        }],
        created_at=now,
        expires_at=now + 15 * 60,
        priority=priority,
        urgency=priority,
        confidence=1.0,
        suggested_action="silent",
    )


def adapt_time_background(
    source: str,
    *,
    now: float | None = None,
    window: str = "",
) -> ProactiveSignal:
    return adapt_routine(source, now=now, window=window, priority=0.1)


def adapt_heart_rate(
    event: dict[str, Any] | None,
    *,
    now: float | None = None,
    ttl_seconds: int = 10 * 60,
) -> ProactiveSignal | None:
    if not isinstance(event, dict):
        return None
    now = time.time() if now is None else float(now)
    measured_at = float(event.get("measured_at") or event.get("received_at") or 0.0)
    if measured_at <= 0.0 or now - measured_at > ttl_seconds:
        return None
    try:
        value = float(event.get("value"))
    except (TypeError, ValueError):
        return None
    previous = event.get("previous_value", event.get("previous"))
    try:
        previous_value = float(previous) if previous is not None else None
    except (TypeError, ValueError):
        previous_value = None
    if previous_value is None:
        direction = str(event.get("direction") or "elevated")
    elif value > previous_value:
        direction = "up"
    elif value < previous_value:
        direction = "down"
    else:
        direction = "unchanged"
    confidence = event.get("confidence", 0.85)
    try:
        confidence = min(1.0, max(0.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    urgency = _bounded_number(event.get("urgency", 0.75 if value > 120 else 0.45), default=0.0)
    try:
        measured_at_display = datetime.fromtimestamp(measured_at).astimezone().isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        measured_at_display = measured_at
    return ProactiveSignal(
        source="heart_rate",
        reason="state_change",
        evidence=[{
            "fact": "heart_rate_measurement",
            "value": value,
            "previous_value": previous_value,
            "direction": direction,
            "measured_at": measured_at,
            "measured_at_local": measured_at_display,
        }],
        created_at=now,
        expires_at=min(now + ttl_seconds, measured_at + ttl_seconds),
        priority=urgency,
        urgency=urgency,
        confidence=confidence,
        suggested_action="message" if urgency >= 0.8 else "question",
    )


def adapt_memory_reactivation(
    memory: dict[str, Any] | None,
    *,
    now: float | None = None,
    ttl_seconds: int = 30 * 60,
    anchor_context: str | dict[str, Any] | None = None,
) -> ProactiveSignal | None:
    if not isinstance(memory, dict):
        return None
    try:
        if float(memory.get("strength", 1.0) or 0.0) <= 0.5:
            return None
    except (TypeError, ValueError):
        return None
    summary = str(memory.get("narrative_summary") or memory.get("summary") or "").strip()
    if not summary:
        facts = memory.get("raw_facts")
        if isinstance(facts, list):
            summary = " ".join(str(item).strip() for item in facts if str(item).strip())[:120]
    if not summary:
        return None
    try:
        from core.scheduler.triggers.time_based import memory_key_for_recall
        memory_key = memory_key_for_recall(memory)
    except Exception:
        memory_key = str(memory.get("id") or "").strip()
    if not memory_key:
        return None
    now = time.time() if now is None else float(now)
    anchor = _normalise_anchor_context(anchor_context)
    if not anchor:
        try:
            from core.scheduler.last_mentioned import (
                is_memory_recall_evaluated,
                is_recently_recalled,
            )

            if is_recently_recalled(memory_key, now_ts=now) or is_memory_recall_evaluated(
                memory_key, now_ts=now
            ):
                return None
        except Exception:
            # Ledger read failures must not break unrelated autonomy sources.
            pass
    evidence = [{
        "fact": "eligible_memory",
        "memory_key": memory_key,
        "recall_stage": "candidate_selected",
        "summary": summary[:160],
    }]
    memory_query: str | dict[str, str] = memory_key
    if anchor:
        evidence.append({
            "fact": "new_anchored_context",
            "memory_key": memory_key,
            "anchor": anchor[:240],
        })
        memory_query = {"memory_key": memory_key, "anchor_context": anchor[:240]}
    return ProactiveSignal(
        source="spontaneous_recall",
        reason="memory_reactivation",
        evidence=evidence,
        memory_query=memory_query,
        created_at=now,
        expires_at=now + ttl_seconds,
        priority=_bounded_number(memory.get("strength"), default=0.35),
        urgency=_bounded_number(memory.get("urgency"), default=0.3),
        confidence=_bounded_number(memory.get("confidence", 0.8), default=0.8),
        suggested_action="suggestion",
    )


def adapt_topic_followup(
    topic: Any,
    *,
    now: float | None = None,
    ttl_seconds: int = 60 * 60,
) -> ProactiveSignal | None:
    if topic is None:
        return None
    topic_text = str(getattr(topic, "topic", "") or "").strip()
    topic_key = str(getattr(topic, "topic_key", "") or "").strip()
    if not topic_text or not topic_key:
        return None
    now = time.time() if now is None else float(now)
    mentioned_at = str(getattr(topic, "mentioned_at", "") or "")
    try:
        age_seconds = max(0.0, float(getattr(topic, "age_seconds", 0.0) or 0.0))
    except (TypeError, ValueError):
        age_seconds = 0.0
    score = _bounded_number(getattr(topic, "score", 0.5) or 0.5, default=0.5)
    return ProactiveSignal(
        source="topic_followup",
        reason="unfinished_topic",
        evidence=[{
            "fact": "unfinished_topic",
            "topic": topic_text[:120],
            "topic_key": topic_key,
            "last_mentioned_at": mentioned_at,
            "age_seconds": round(age_seconds, 1),
        }],
        memory_query=topic_key,
        created_at=now,
        expires_at=now + ttl_seconds,
        priority=score,
        urgency=score,
        confidence=score,
        suggested_action="question",
    )


def adapt_desktop_wake(
    *,
    last_seen: float | None = None,
    now: float | None = None,
    ttl_seconds: int = _DESKTOP_WAKE_TTL_SECONDS,
    event_id: str = "",
    dedupe_key: str = "",
) -> ProactiveSignal:
    now = time.time() if now is None else float(now)
    offline_seconds, duration_known, duration_capped = _bounded_offline_duration(
        last_seen, now=now
    )
    safe_event_id = _safe_correlation_value(event_id)
    dedupe_fingerprint = _correlation_fingerprint(dedupe_key)
    evidence = {
        "fact": "desktop_session_reopened",
        "offline_seconds": round(offline_seconds, 1),
        "offline_duration_known": duration_known,
        "offline_duration_capped": duration_capped,
    }
    if safe_event_id:
        evidence["perceive_event_id"] = safe_event_id
    if dedupe_fingerprint:
        evidence["perceive_dedupe_fingerprint"] = dedupe_fingerprint
    ttl_seconds = max(60, min(int(ttl_seconds), 15 * 60))
    identity = f"desktop-wake:{safe_event_id}" if safe_event_id else ""
    return ProactiveSignal(
        source="desktop_wake",
        reason="session_reopen",
        evidence=[evidence],
        created_at=now,
        expires_at=now + ttl_seconds,
        priority=min(0.7, 0.2 + offline_seconds / (24 * 3600)),
        urgency=0.2,
        confidence=1.0,
        action_mode=ActionMode.REFLECT.value,
        suggested_action=ActionMode.REFLECT.value,
        signal_id=identity,
    )


def enqueue_desktop_wake_signal(
    uid: str,
    char_id: str,
    *,
    last_seen: float | None = None,
    event_id: str = "",
    dedupe_key: str = "",
    now: float | None = None,
) -> tuple[bool, str, ProactiveSignal]:
    """Persist one bounded reopen fact when autonomy is currently enabled."""
    signal = adapt_desktop_wake(
        last_seen=last_seen,
        now=now,
        event_id=event_id,
        dedupe_key=dedupe_key,
    )
    from core.autonomy import store

    state = store.load(uid, char_id)
    try:
        from core.autonomy.effective_state import autonomy_enabled

        enabled = autonomy_enabled(uid, char_id, state)
    except Exception:
        enabled = False
    if not enabled:
        return False, "autonomy_disabled", signal
    persistent_key = (
        f"desktop_wake:{_correlation_fingerprint(dedupe_key)}"
        if dedupe_key
        else f"desktop_wake:{signal.signal_id}"
    )
    queued, status = store.enqueue_signal(
        uid, char_id, signal, dedupe_key=persistent_key
    )
    return queued, status, signal


def adapt_restart(*, started_at: float, now: float | None = None) -> ProactiveSignal:
    now = time.time() if now is None else float(now)
    return ProactiveSignal(
        source="restart",
        reason="routine",
        evidence=[{"fact": "runtime_restarted", "started_at": float(started_at), "observed_at": now}],
        created_at=now,
        expires_at=now + 10 * 60,
        priority=0.05,
        urgency=0.05,
        confidence=1.0,
        suggested_action="silent",
    )


def adapt_trigger(name: str, payload: Any = None, *, now: float | None = None) -> ProactiveSignal | None:
    """Map a legacy trigger payload to a signal without executing it."""
    name = str(name or "").strip()
    if name in ROUTINE_TRIGGER_NAMES:
        return adapt_time_background(name, now=now, window=name)
    if name in {"hr_high", "hr_critical", "heart_rate"}:
        return adapt_heart_rate(payload if isinstance(payload, dict) else None, now=now)
    if name in {"spontaneous_recall", "memory_reactivation"}:
        memory_payload = payload if isinstance(payload, dict) else None
        anchor_context = memory_payload.get("anchor_context") if memory_payload else None
        return adapt_memory_reactivation(
            memory_payload,
            now=now,
            anchor_context=anchor_context,
        )
    if name in {"topic_followup", "unfinished_topic"}:
        return adapt_topic_followup(payload, now=now)
    if name in {"desktop_wake", "reopen"}:
        return adapt_desktop_wake(last_seen=payload if isinstance(payload, (int, float)) else None, now=now)
    if name in {"restart", "runtime_restart"}:
        return adapt_restart(started_at=float(payload or now or time.time()), now=now)
    return None


def collect_external_signals(*, now: float | None = None, context: dict[str, Any] | None = None) -> list[ProactiveSignal]:
    """Collect currently available sensor/memory signals from a read-only context."""
    now = time.time() if now is None else float(now)
    context = context or {}
    result: list[ProactiveSignal] = []
    for adapter_name, payload in (
        ("heart_rate", context.get("heart_rate_event")),
        ("spontaneous_recall", context.get("memory")),
        ("topic_followup", context.get("last_mentioned")),
    ):
        signal = adapt_trigger(adapter_name, payload, now=now)
        if signal is not None:
            result.append(signal)
    if context.get("desktop_wake"):
        result.append(adapt_desktop_wake(last_seen=context.get("last_seen"), now=now))
    return result


def _bounded_number(value: Any, *, default: float = 0.0) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


def _bounded_offline_duration(
    last_seen: float | None, *, now: float
) -> tuple[float, bool, bool]:
    if (
        isinstance(last_seen, bool)
        or not isinstance(last_seen, (int, float))
        or not math.isfinite(float(last_seen))
        or float(last_seen) <= 0
    ):
        return 0.0, False, False
    raw = max(0.0, now - float(last_seen))
    return (
        min(raw, float(_DESKTOP_WAKE_MAX_OFFLINE_SECONDS)),
        True,
        raw > _DESKTOP_WAKE_MAX_OFFLINE_SECONDS,
    )


def _safe_correlation_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 128 and all(ch.isalnum() or ch in "._:-" for ch in raw):
        return raw
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _correlation_fingerprint(value: str) -> str:
    raw = str(value or "").strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24] if raw else ""


def routine_trigger_enabled(name: str) -> bool:
    """Respect the scheduler source switch at production and consumption time."""
    if name not in ROUTINE_TRIGGER_NAMES:
        return True
    try:
        from core.autonomy.effective_state import scheduler_source_enabled

        return scheduler_source_enabled(name)
    except Exception:
        return False


def routine_key_for_signal(signal: ProactiveSignal) -> str:
    for fact in signal.evidence:
        if isinstance(fact, dict) and fact.get("routine_key"):
            return str(fact["routine_key"]).strip()
    return ""


def _with_routine_key(evidence: list[dict], name: str) -> list[dict]:
    result: list[dict] = []
    for raw in evidence:
        item = dict(raw) if isinstance(raw, dict) else {"fact": str(raw)}
        item.setdefault("routine_key", name)
        result.append(item)
    return result


def _normalise_anchor_context(value: str | dict[str, Any] | None) -> str:
    if isinstance(value, dict):
        return " ".join(
            f"{key}={item}"
            for key, item in sorted(value.items())
            if isinstance(item, (str, int, float)) and str(item).strip()
        ).strip()
    return str(value or "").strip()
