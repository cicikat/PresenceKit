"""
Probe snapshot ring buffer (memory-only, never persisted to disk).

Stores the last RING_SIZE probe results per uid so the admin panel can inspect
whether a turn used the fast-path, what was sent to the probe LLM, what tools
were called, and what the execution results were.

Usage:
    from core.observe.probe_capture import capture_probe, get_probe_snapshots

    # After probe decision + tool execution:
    capture_probe(uid, snap)

    # In /observe/probe/{uid}:
    snaps = get_probe_snapshots(uid)
"""

from collections import deque
from datetime import datetime, timezone

RING_SIZE = 5

# per-uid ring: uid → deque of snapshot dicts
_rings: dict[str, deque] = {}


def capture_probe(uid: str, snap: dict) -> None:
    """Record one probe result into the ring buffer for uid."""
    if uid not in _rings:
        _rings[uid] = deque(maxlen=RING_SIZE)
    snap.setdefault("captured_at", datetime.now(timezone.utc).isoformat())
    _rings[uid].append(snap)


def get_probe_snapshots(uid: str) -> list[dict]:
    """Return snapshots for uid, newest last."""
    ring = _rings.get(uid)
    return list(ring) if ring else []


def list_probe_uids() -> list[str]:
    """Return all uids that have at least one probe snapshot."""
    return [uid for uid, ring in _rings.items() if ring]
