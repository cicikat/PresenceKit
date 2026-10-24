"""Pure, evidence-bound affect projection. Numbers are engineering parameters."""
from __future__ import annotations

import math

EMOTIONS = ("joy", "anger", "hurt", "concern", "disappointment", "fear", "sadness", "surprise")
STRATEGIES = ("none", "express", "suppress", "reappraise", "pause")
HALF_LIVES = dict(zip(EMOTIONS, (1800, 2700, 7200, 3600, 14400, 1200, 7200, 180)))
LABELS = dict(zip(EMOTIONS, ("happy", "angry", "sad", "gentle", "sad", "surprised", "sad", "surprised")))
PAD = {
    "joy": (.8, .5, .3), "anger": (-.7, .8, .3), "hurt": (-.7, .2, -.4),
    "concern": (-.1, .3, .1), "disappointment": (-.6, -.2, -.3),
    "fear": (-.8, .8, -.7), "sadness": (-.7, -.4, -.5), "surprise": (.1, .8, -.1),
}
MAX_EVENTS = 48
MAX_RECEIPTS = 192
MAX_AGE = 7 * 86400
MIN_SIGNAL = .04


def number(value, lo=0.0, hi=1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("affect number must be numeric")
    if not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError("affect number outside bounds")
    return float(value)


def candidate(raw: dict, known_ids: set[str]) -> dict:
    """Reject the whole proposal, never partially apply malformed inference."""
    if not isinstance(raw, dict):
        raise ValueError("invalid appraisal")
    values = {key: number(raw[key]) for key in EMOTIONS}
    confidence, relevance = number(raw["confidence"]), number(raw["relevance"])
    strategy, impact, agency = raw["strategy"], raw["goal_impact"], raw["agency"]
    target = raw.get("correction_target", "none")
    if strategy not in STRATEGIES or impact not in {"none", "support", "conflict", "danger", "loss", "repair"}:
        raise ValueError("invalid appraisal category")
    if agency not in {"uncertain", "self", "other", "environment"}:
        raise ValueError("invalid appraisal agency")
    if target != "none" and target not in known_ids:
        raise ValueError("unknown correction target")
    if confidence < .55 or (target != "none" and confidence < .7):
        raise ValueError("uncertain appraisal")
    return {"emotions": values, "confidence": confidence, "relevance": relevance,
            "strategy": strategy, "goal_impact": impact, "agency": agency,
            "correction_target": target}


def project(events: list[dict], now: float) -> dict:
    if not isinstance(events, list):
        raise ValueError("invalid affect events")
    vector, mood = ({key: 0.0 for key in EMOTIONS} for _ in range(2))
    sources, active = [], []
    for event in events[-MAX_EVENTS:]:
        if event.get("invalid_source_event_ids"):
            continue
        number(event["occurred_at"], 0, 1e12)
        if not isinstance(event["source_turn_id"], str) or not event["source_turn_id"]:
            raise ValueError("missing source turn")
        at = number(event["appraised_at"], 0, 1e12)
        age = now - at
        if not 0 <= age < MAX_AGE:
            continue
        appraisal = event["appraisal"]
        if appraisal["strategy"] not in STRATEGIES:
            raise ValueError("invalid strategy")
        weight = number(appraisal["confidence"]) * number(appraisal["relevance"])
        peak = 0.0
        for key in EMOTIONS:
            value = number(appraisal["emotions"][key]) * weight
            contribution = value * 2 ** (-age / HALF_LIVES[key])
            vector[key] = 1 - (1 - vector[key]) * (1 - contribution)
            slow = value * .3 * 2 ** (-age / max(21600, HALF_LIVES[key]))
            mood[key] = 1 - (1 - mood[key]) * (1 - slow)
            peak = max(peak, contribution, slow)
        if peak >= MIN_SIGNAL:
            sources.append(event["source_turn_id"])
            active.append(event)
    total = max(1.0, sum(mood.values()))
    pad = {axis: round(sum(mood[key] * PAD[key][i] for key in EMOTIONS) / total, 4)
           for axis, i in (("valence", 0), ("arousal", 1), ("dominance", 2))}
    peak, mood_peak = max(vector.values()), max(mood.values())
    dominant = max(vector, key=vector.get) if peak >= MIN_SIGNAL else ""
    last = active[-1] if active else None
    return {"emotions": {k: round(v, 4) for k, v in vector.items()}, "mood": pad,
            "has_signal": bool(sources), "dominant": dominant,
            "intensity": round(max(peak, mood_peak), 4) if sources else 0.0,
            "strategy": last["appraisal"]["strategy"] if last else "none",
            "source_turn_ids": sources[-8:], "simulated": True}


def apply(state: dict, raw: dict, *, turn_id: str, occurred_at: float, now: float) -> None:
    events = [e for e in state.get("affect_events", []) if 0 <= now - e["occurred_at"] < MAX_AGE]
    proposal = candidate(raw, {e["source_turn_id"] for e in events if not e.get("invalid_source_event_ids")})
    target = proposal.pop("correction_target")
    if target != "none":
        original = next(e for e in events if e["source_turn_id"] == target)
        original["revisions"] = (original.get("revisions", []) + [{
            "appraisal": original["appraisal"], "appraised_at": original["appraised_at"],
            "superseded_by": turn_id,
        }])[-4:]
        original.update(appraisal=proposal, appraised_at=occurred_at, correction_turn_id=turn_id)
        original["source_event_ids"] = list(dict.fromkeys(original.get("source_event_ids", [target + ":user"]) + [turn_id + ":user"]))[-8:]
    elif proposal["relevance"] >= .1:
        events.append({"source_turn_id": turn_id, "occurred_at": occurred_at,
                       "source_event_ids": [turn_id + ":user"],
                       "appraised_at": occurred_at, "appraisal": proposal, "revisions": []})
    state["affect_events"] = events[-MAX_EVENTS:]
