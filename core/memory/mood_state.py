"""Single character-scoped affect store; outward expression is an independent view."""
from __future__ import annotations

from copy import deepcopy
import json
import threading
import time

from core import affect
from core.sandbox import get_paths
from core.safe_write import safe_write_json
from core.data_paths import DEFAULT_CHAR_ID

EMOTION_INTENSITY = {"neutral": 0.0, "gentle": .3, "thinking": .2, "happy": .6,
                     "sad": .6, "surprised": .7, "angry": .8, "sleepy": .3, "yandere": 1.0}
TTL_SECONDS = 1800
_STORE_LOCK = threading.RLock()
_DEFAULT = {"current": "", "previous": "", "intensity": 0.0, "updated_at": 0.0,
            "has_signal": False, "source_turn_id": "", "source_at": 0.0,
            "expires_at": 0.0, "version": 0, "schema_version": 3,
            "affect_events": [], "receipts": [], "appraisal_status": "not_run"}


def enabled() -> bool:
    from core.config_loader import get_config
    return bool((get_config().get("affective_engine") or {}).get("enabled", True))


def _read(char_id: str) -> dict:
    try:
        raw = json.loads(get_paths().mood_state(char_id=char_id).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") not in (2, 3):
            return deepcopy(_DEFAULT)
        state = {**deepcopy(_DEFAULT), **raw, "schema_version": 3}
        state["intensity"] = affect.number(state["intensity"])
        affect.number(state.get("last_source_at", 0), 0, 1e12)
        affect.number(state["version"], 0, 1e12)
        state["affect_events"] = state["affect_events"][-affect.MAX_EVENTS:]
        affect.project(state["affect_events"], time.time())
        if not isinstance(state["receipts"], list) or not all(isinstance(x, str) for x in state["receipts"]):
            raise ValueError("invalid receipts")
        return state
    except FileNotFoundError:
        return deepcopy(_DEFAULT)
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        state = deepcopy(_DEFAULT)
        state["appraisal_status"] = "invalid_store"
        return state


def load(*, char_id: str = DEFAULT_CHAR_ID) -> dict:
    with _STORE_LOCK:
        state = _read(char_id)
    now = time.time()
    try:
        valid = (state.get("has_signal") and state.get("current") in EMOTION_INTENSITY and state.get("source_turn_id")
                 and affect.number(state.get("source_at", 0), 0, 1e12) <= now
                 < affect.number(state.get("expires_at", 0), 0, 1e12))
    except ValueError:
        valid = False
    if not valid:
        state.update(current="", intensity=0.0, has_signal=False)
    state["affect"] = affect.project(state["affect_events"], now)
    state["affect"]["enabled"] = enabled()
    return state


def save(state: dict, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    with _STORE_LOCK:
        durable = {k: v for k, v in state.items() if k != "affect"}
        if not safe_write_json(get_paths().mood_state(char_id=char_id), durable):
            raise OSError("affect persistence failed")


def update(new_emotion: str, new_intensity: float | None = None, source: str = "explicit",
           *, char_id: str = DEFAULT_CHAR_ID, source_turn_id: str = "",
           source_at: float | None = None, ttl_seconds: float = TTL_SECONDS,
           retract: bool = False) -> dict:
    with _STORE_LOCK:
        state = _read(char_id)
        now = time.time()
        if source not in ("explicit", "perform") or not source_turn_id or source_at is None:
            return load(char_id=char_id)
        occurred = affect.number(source_at, 0, 1e12)
        if (occurred > now or occurred < float(state.get("last_source_at") or 0)
                or source_turn_id in state["receipts"] or state.get("last_turn_id") == source_turn_id):
            return load(char_id=char_id)
        state.update(last_source_at=occurred, last_turn_id=source_turn_id,
                     version=int(state.get("version") or 0) + 1, schema_version=3)
        state["receipts"] = (state["receipts"] + [source_turn_id])[-affect.MAX_RECEIPTS:]
        if retract:
            state.update(current="", intensity=0.0, has_signal=False, expires_at=0.0)
        elif new_emotion in EMOTION_INTENSITY:
            ttl = min(TTL_SECONDS, affect.number(ttl_seconds, 0, 1e12))
            if occurred + ttl > now:
                state.update(previous=state.get("current", ""), current=new_emotion,
                             source_at=occurred, source_turn_id=source_turn_id,
                             intensity=affect.number(new_intensity if new_intensity is not None else EMOTION_INTENSITY[new_emotion]),
                             has_signal=True, updated_at=occurred, expires_at=occurred + ttl, source=source)
        save(state, char_id=char_id)
        return load(char_id=char_id)


def invalidate_sources(event_ids: list[str], *, char_id: str, revert: bool = False) -> list[str]:
    """Exact canonical event IDs only; withdrawal never revives an older guess."""
    with _STORE_LOCK:
        if not get_paths().mood_state(char_id=char_id).exists():
            return []
        state = _read(char_id)
        changed = []
        known = {ref for event in state["affect_events"] for ref in event.get("source_event_ids", [])}
        if state.get("last_turn_id"):
            known.add(state["last_turn_id"] + ":user")
        prior_withdrawn = set(state.get("withdrawn_event_ids", []))
        hit_refs = set(event_ids) & known
        withdrawn = prior_withdrawn - hit_refs if revert else prior_withdrawn | hit_refs
        if withdrawn != prior_withdrawn:
            state["withdrawn_event_ids"] = sorted(withdrawn)[-affect.MAX_RECEIPTS:]
            changed.extend(ref.removesuffix(":user") for ref in hit_refs)
        for event in state["affect_events"]:
            hit = set(event_ids) & set(event.get("source_event_ids", []))
            old = set(event.get("invalid_source_event_ids", []))
            current = old - hit if revert else old | hit
            if old != current:
                event["invalid_source_event_ids"] = sorted(current)
                if event["source_turn_id"] not in changed:
                    changed.append(event["source_turn_id"])
        if changed:
            state["version"] += 1
            save(state, char_id=char_id)
        return changed


def record_appraisal(raw: dict | None, *, char_id: str, turn_id: str,
                     occurred_at: float, evidence: str, status: str) -> dict:
    """Commit a network result only while its source turn is still current."""
    with _STORE_LOCK:
        state = _read(char_id)
        if (not enabled() or state.get("last_turn_id") != turn_id
                or state.get("last_appraisal_turn_id") == turn_id
                or state.get("last_source_at") != occurred_at
                or turn_id + ":user" in state.get("withdrawn_event_ids", [])):
            return load(char_id=char_id)
        state["last_appraisal_turn_id"] = turn_id
        state["appraisal_status"] = status
        state["version"] += 1
        if raw is not None:
            try:
                affect.apply(state, raw, turn_id=turn_id, occurred_at=occurred_at, now=time.time())
                for event in state["affect_events"]:
                    if event["source_turn_id"] == turn_id:
                        event["evidence_excerpt"] = evidence[:600]
                    elif event.get("correction_turn_id") == turn_id:
                        event["correction_excerpt"] = evidence[:600]
                state["appraisal_status"] = "applied"
            except (ValueError, KeyError, TypeError):
                state["appraisal_status"] = "rejected"
        save(state, char_id=char_id)
        return load(char_id=char_id)


def get_current(*, char_id: str = DEFAULT_CHAR_ID) -> str:
    """Compatibility: explicit outward expression only (avatar/voice consumers)."""
    return load(char_id=char_id).get("current", "")


def get_intensity(*, char_id: str = DEFAULT_CHAR_ID) -> float:
    return load(char_id=char_id).get("intensity", 0.0)


def get_inner_current(*, char_id: str = DEFAULT_CHAR_ID) -> str:
    state = load(char_id=char_id)
    inner = state["affect"]
    if inner["enabled"] and inner["has_signal"]:
        return affect.LABELS.get(inner["dominant"], "")
    return state["current"]
