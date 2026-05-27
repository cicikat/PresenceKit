"""
Impression store — data/dreams/impressions/{uid}.json

Physical isolation contract (I1):
  ★ Only impression_loader reads this directory.
  ★ reflect_to_episodic / consolidate_to_identity / retrieve /
    event_log.search / mid_term / short_term / user_identity — never read here.
  Enforcement: by omission from all reality-loader read lists, not by sentinel skip.

Each entry carries sentinels (never_retrieve, not_memory_source, reality_boundary)
as a belt-and-suspenders defence layer (I3).

Decay: slow per-day weight reduction (~0.02/day) for "久一点的模糊印象".
Cap: max 50 entries; trim oldest/lowest-weight on overflow.
"""

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 50
_DECAY_PER_DAY = 0.02
_SECONDS_PER_DAY = 86400.0

_SENTINEL = {
    "never_retrieve": True,
    "not_memory_source": True,
    "reality_boundary": "dream_only",
}


def _impressions_file(uid: str):
    from core.sandbox import get_paths, safe_user_id
    d = get_paths().dreams_impressions_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe_user_id(uid)}.json"


def load_impressions(uid: str) -> list[dict[str, Any]]:
    try:
        return json.loads(_impressions_file(uid).read_text(encoding="utf-8"))
    except Exception:
        return []


def save_impressions(uid: str, entries: list[dict[str, Any]]) -> None:
    from core.safe_write import safe_write_json
    safe_write_json(_impressions_file(uid), entries)


def append_impression(uid: str, entry: dict[str, Any]) -> None:
    """Append one impression, decay existing weights, enforce 50-entry cap."""
    entries = load_impressions(uid)
    entries = _apply_decay(entries)

    stamped = {**_SENTINEL, **entry}
    entries.append(stamped)

    if len(entries) > _MAX_ENTRIES:
        # Keep highest-weight entries; break ties by preferring newer ts
        entries.sort(key=lambda e: (e.get("weight", 0.0), e.get("ts", 0.0)))
        entries = entries[len(entries) - _MAX_ENTRIES :]

    save_impressions(uid, entries)


def get_active_impressions(
    uid: str, now: float | None = None
) -> list[dict[str, Any]]:
    """Return unexpired impressions, newest first. Applies decay in-place."""
    if now is None:
        now = time.time()
    entries = load_impressions(uid)
    entries = _apply_decay(entries)
    active = [
        e for e in entries
        if now < float(e.get("decay_after", 0)) and float(e.get("weight", 0)) > 0.05
    ]
    active.sort(key=lambda e: e.get("ts", 0.0), reverse=True)
    return active


def _apply_decay(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = time.time()
    result = []
    for e in entries:
        last = float(e.get("last_decay_ts") or e.get("ts") or now)
        days = (now - last) / _SECONDS_PER_DAY
        if days >= 1.0:
            w = max(0.0, float(e.get("weight", 0.0)) - _DECAY_PER_DAY * days)
            e = {**e, "weight": round(w, 4), "last_decay_ts": now}
        result.append(e)
    return result
