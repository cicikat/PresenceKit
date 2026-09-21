"""Music playback stimulus after ledger commit (ticket 260 F).

Playback events are already in the listening ledger when this module runs.
Dream blocking a talk candidate must not roll back that ledger. Tool results
are never rewrapped as perceive_event. Choose-next causation is cooled for
600s and each session may talk at most twice.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from core.audio_music_contract import (
    AUTONOMY_SIGNAL_SOURCE,
    CONFIG_ROOT,
    FEEDBACK_LOOP_COOLDOWN_S,
    PROGRESS_SIGNAL_MIN_INTERVAL_S,
    SESSION_TALK_BUDGET,
)
from core.autonomy.models import ActionMode, ProactiveSignal
from core.listening_store import listening_lock, load_session, persist_session
from core.perceive_event import PerceiveEvent, PerceiveStatus, receive_perceive_event

_LIFECYCLE_KINDS = frozenset({
    "started", "changed", "paused", "resumed", "finished", "stopped", "error",
})
_SIGNAL_TTL_S = 10 * 60
_CHOOSE_COMMANDS_CAP = 32


def music_autonomy_enabled() -> bool:
    from core.config_loader import get_config
    block = get_config().get(CONFIG_ROOT) or {}
    return block.get("music_autonomy") is True


def remember_choose_command(uid: str, command_id: str) -> None:
    command_id = str(command_id or "").strip()
    if not command_id:
        return
    with listening_lock(uid):
        session = load_session(uid)
        items = [
            item for item in (session.get("choose_command_ids") or [])
            if isinstance(item, str) and item
        ]
        items = [item for item in items if item != command_id]
        items.append(command_id)
        session["choose_command_ids"] = items[-_CHOOSE_COMMANDS_CAP:]
        persist_session(session)


def last_suppress_reason(uid: str) -> str:
    return str(_feedback(load_session(uid)).get("last_reason") or "")


def _fingerprint(value: str) -> str:
    raw = str(value or "").strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24] if raw else ""


def _feedback(session: dict[str, Any]) -> dict[str, Any]:
    raw = session.get("music_feedback")
    return dict(raw) if isinstance(raw, dict) else {}


def _save_feedback(uid: str, updates: dict[str, Any]) -> dict[str, Any]:
    with listening_lock(uid):
        session = load_session(uid)
        stored = _feedback(session)
        stored.update(updates)
        session["music_feedback"] = stored
        persist_session(session)
        return stored


def _progress_too_soon(feedback: dict[str, Any], now: float) -> bool:
    last = float(feedback.get("last_progress_signal_at") or 0.0)
    return last > 0 and (now - last) < PROGRESS_SIGNAL_MIN_INTERVAL_S


def _choose_next_reason(session: dict[str, Any], event: dict[str, Any], now: float) -> str:
    causation = str(event.get("causation_command_id") or "")
    if not causation:
        return ""
    known = {
        item for item in (session.get("choose_command_ids") or [])
        if isinstance(item, str)
    }
    if causation not in known:
        return ""
    feedback = _feedback(session)
    until = float(feedback.get("choose_cooldown_until") or 0.0)
    if until and now < until:
        return "choose_next_cooldown"
    return "choose_next_feedback"


def _action_mode(kind: str) -> str:
    if kind in {"started", "changed", "finished"}:
        return ActionMode.REFLECT.value
    return ActionMode.NONE.value


def _adapt_signal(
    *,
    event: dict[str, Any],
    ledger: dict[str, Any],
    now: float,
) -> ProactiveSignal:
    kind = str(event.get("kind") or "")
    event_id = str(event.get("event_id") or "")
    session = ledger.get("session") or {}
    evidence = {
        "fact": "music_playback_lifecycle",
        "kind": kind,
        "track_id": str(event.get("track_id") or session.get("track_id") or ""),
        "occurrence_id": str(session.get("occurrence_id") or ""),
        "ledger_reason": str(ledger.get("reason") or ""),
        "perceive_event_id": _fingerprint(event_id),
    }
    causation = str(event.get("causation_command_id") or "")
    if causation:
        evidence["causation_fingerprint"] = _fingerprint(causation)
    identity = _fingerprint(event_id) or f"{kind}-{int(now)}"
    return ProactiveSignal(
        source=AUTONOMY_SIGNAL_SOURCE,
        reason=f"A bounded {kind} playback event is eligible for autonomy evaluation.",
        evidence=[evidence],
        created_at=now,
        expires_at=now + _SIGNAL_TTL_S,
        priority=0.35 if kind in {"started", "changed", "finished"} else 0.15,
        urgency=0.2,
        confidence=1.0,
        action_mode=_action_mode(kind),
        suggested_action=_action_mode(kind),
        signal_id=f"music-playback:{identity}",
    )


def _enqueue(uid: str, char_id: str, signal: ProactiveSignal, *, dedupe_key: str) -> tuple[bool, str]:
    from core.autonomy import store
    from core.autonomy.effective_state import autonomy_enabled

    state = store.load(uid, char_id)
    try:
        enabled = autonomy_enabled(uid, char_id, state)
    except Exception:
        enabled = False
    if not enabled:
        return False, "autonomy_disabled"
    return store.enqueue_signal(uid, char_id, signal, dedupe_key=dedupe_key)


async def maybe_emit_after_ledger(
    uid: str,
    event: dict[str, Any],
    ledger: dict[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Enqueue a low-trust music_playback candidate after a successful ledger commit."""
    now = time.time() if now is None else float(now)
    kind = str(event.get("kind") or "")
    if not ledger.get("ok"):
        return {"queued": False, "reason": "ledger_not_committed"}
    if kind not in _LIFECYCLE_KINDS and kind != "progress":
        return {"queued": False, "reason": "ignored_kind"}
    if not music_autonomy_enabled():
        return {"queued": False, "reason": "music_autonomy_disabled"}

    session = load_session(uid)
    char_id = str(session.get("participant_char_id") or "").strip()
    if not char_id:
        return {"queued": False, "reason": "no_participant"}

    feedback = _feedback(session)
    if kind == "progress":
        if ledger.get("reason") == "progress_ignored":
            return {"queued": False, "reason": "progress_ignored"}
        if _progress_too_soon(feedback, now):
            _save_feedback(uid, {"last_reason": "progress_rate_limited"})
            return {"queued": False, "reason": "progress_rate_limited"}

    loop_reason = _choose_next_reason(session, event, now)
    if loop_reason:
        _save_feedback(uid, {
            "last_reason": loop_reason,
            "choose_cooldown_until": now + FEEDBACK_LOOP_COOLDOWN_S,
        })
        return {"queued": False, "reason": loop_reason, "ledger_committed": True}

    talks = int(feedback.get("session_talks") or 0)
    if talks >= SESSION_TALK_BUDGET and _action_mode(kind) != ActionMode.NONE.value:
        _save_feedback(uid, {"last_reason": "session_talk_budget"})
        return {"queued": False, "reason": "session_talk_budget", "ledger_committed": True}

    perceive = PerceiveEvent(
        source=AUTONOMY_SIGNAL_SOURCE,
        uid=uid,
        channel="system",
        kind="trigger",
        payload={"event_id": str(event.get("event_id") or ""), "kind": kind},
        event_id=str(event.get("event_id") or "") or None,
        char_id=char_id,
        created_at=now,
        trust="low_trust",
        require_dream_guard=True,
    )
    gate = await receive_perceive_event(perceive)
    if gate.status != PerceiveStatus.ACCEPTED:
        reason = {
            PerceiveStatus.BLOCKED_DREAM: "blocked_dream",
            PerceiveStatus.DUPLICATE: "duplicate",
        }.get(gate.status, str(gate.status.value))
        _save_feedback(uid, {"last_reason": reason})
        return {"queued": False, "reason": reason, "ledger_committed": True}

    signal = _adapt_signal(event=event, ledger=ledger, now=now)
    queued, status = _enqueue(
        uid, char_id, signal,
        dedupe_key=f"music_playback:{_fingerprint(str(event.get('event_id') or ''))}",
    )
    updates: dict[str, Any] = {"last_reason": "" if queued else status}
    if kind == "progress" and queued:
        updates["last_progress_signal_at"] = now
    if queued and signal.action_mode != ActionMode.NONE.value:
        updates["session_talks"] = talks + 1
    _save_feedback(uid, updates)
    return {
        "queued": queued,
        "reason": "queued" if queued else status,
        "signal_id": signal.signal_id,
        "ledger_committed": True,
    }
