"""
Dream prompt snapshot ring buffer (memory-only, never persisted to disk).

Stores the last RING_SIZE dream prompt build results per uid so the admin panel
can inspect the D0–D10 layer stack, scene_tags, world_id, and LLM output for
each dream turn.

Completely separate from core/observe/prompt_capture.py — dream layers use a
different naming scheme (D0_/D4.5_ vs 2_/6d_) and must never be mixed.

Usage:
    from core.observe.dream_capture import capture_dream, get_dream_snapshots

    # After build_dream_prompt() + LLM reply:
    capture_dream(uid, snap)

    # In /observe/dream-prompt/{uid}:
    snaps = get_dream_snapshots(uid)
"""

from collections import deque
from datetime import datetime, timezone

RING_SIZE = 5

# per-uid ring: uid → deque of snapshot dicts
_rings: dict[str, deque] = {}


def capture_dream(uid: str, snap: dict) -> None:
    """Record one dream turn snapshot into the ring buffer for uid."""
    if uid not in _rings:
        _rings[uid] = deque(maxlen=RING_SIZE)
    snap.setdefault("captured_at", datetime.now(timezone.utc).isoformat())
    _rings[uid].append(snap)


def update_dream_llm_output(uid: str, reply: str) -> None:
    """Pair the LLM reply with the latest dream snapshot for uid (in-place update)."""
    ring = _rings.get(uid)
    if ring:
        ring[-1]["llm_output"] = reply


def get_dream_snapshots(uid: str) -> list[dict]:
    """Return snapshots for uid, newest last."""
    ring = _rings.get(uid)
    return list(ring) if ring else []


def list_dream_uids() -> list[str]:
    """Return all uids that have at least one dream snapshot."""
    return [uid for uid, ring in _rings.items() if ring]
