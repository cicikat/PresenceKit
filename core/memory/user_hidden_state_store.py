"""
core/memory/user_hidden_state_store.py
======================================
Phase 1.5 — UserHiddenState persistence (load / save).
Phase 2   — Dream read interface (load_dream_snapshot).

SECURITY NOTE — WriteEnvelope gate:
  This store does NOT enforce envelope gating.  It is the caller's
  responsibility to hold a WriteEnvelope with can_write_memory=True
  before calling save_hidden_state().  The store is intentionally
  policy-free so that callers supply their own envelope logic without
  re-implementing path handling.

  Future callers: you MUST have obtained a WriteEnvelope with
  can_write_memory=True before calling save_hidden_state().
  This store does not check or stamp envelopes.

load_dream_snapshot():
  READ-ONLY.  Returns coarse buckets for Dream LLM prompt input.
  Dream sessions MUST NOT write back to hidden state using this snapshot.
  No write path originates from this function.

Not wired to:
  - Dream pipeline (write path)
  - build_snapshot
  - scheduler
  - automatic save
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from typing import Any

from core.memory.user_hidden_state import (
    AfterglowResidueInput,
    UserHiddenState,
    default_hidden_state,
    from_dict,
    to_dict,
    to_dream_snapshot,
)
from core.safe_write import safe_write_json
from core.sandbox import get_paths

logger = logging.getLogger(__name__)

HIDDEN_STATE_FILENAME = "hidden_state.json"
AFTERGLOW_FILENAME = "afterglow_residue.json"


def load_hidden_state(uid: str | int) -> UserHiddenState:
    """Load UserHiddenState for uid from disk.

    Returns default_hidden_state() if the file does not exist or is
    corrupted.  Never raises.

    SECURITY NOTE: Callers MUST hold a WriteEnvelope with
    can_write_memory=True before mutating and persisting the returned
    state.  This function is read-only and does not emit a
    WriteEnvelope stamp.

    Path: user_memory_root(uid) / hidden_state.json
    """
    path: Path = get_paths().user_memory_root(uid) / HIDDEN_STATE_FILENAME

    if not path.exists():
        return default_hidden_state()

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("[hidden_state] cannot read %s: %s — returning default", path, exc)
        return default_hidden_state()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[hidden_state] corrupt JSON in %s: %s — returning default", path, exc)
        return default_hidden_state()

    try:
        return from_dict(data)
    except Exception as exc:
        logger.warning("[hidden_state] from_dict failed for %s: %s — returning default", path, exc)
        return default_hidden_state()


def load_dream_snapshot(uid: str | int, now: str) -> dict[str, Any]:
    """Load UserHiddenState and return a read-only Dream-safe bucket snapshot.

    This is the single sanctioned read path for Dream context injection.

    Contract:
      - Read-only: does not mutate state, does not write to disk.
      - Returns low-resolution buckets only (no raw scalar values).
      - Safe to pass directly as LLM prompt input for Dream sessions.
      - Does NOT connect to the Dream pipeline (no wiring in this function).
      - Does NOT emit a WriteEnvelope stamp.
      - Returns a neutral mid/neutral snapshot on any load or projection error.

    SECURITY — write-lock:
      Dream sessions MUST NOT write back to hidden state using this snapshot.
      DREAM_DIRECT_WRITABLE = frozenset() — all mutations must flow through
      the Reality-side integrator with can_write_memory=True.

    Path: user_memory_root(uid) / hidden_state.json  (read-only)
    """
    state = load_hidden_state(uid)
    return to_dream_snapshot(state, now)


def _load_afterglow_raw(uid: str | int) -> dict | None:
    """Load raw afterglow residue dict from disk.  Returns None if absent or corrupt.

    Internal helper called by read_afterglow_residue() in user_hidden_state.py.
    Read-only.  Does NOT write anything.  Does NOT emit a WriteEnvelope stamp.
    """
    path: Path = get_paths().user_memory_root(uid) / AFTERGLOW_FILENAME
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("[afterglow] cannot read %s: %s — returning None", path, exc)
        return None


def save_afterglow_residue(
    uid: str | int,
    residue: AfterglowResidueInput,
    created_at: str,
) -> bool:
    """Persist an afterglow residue to disk (called at Dream exit).

    Stores emotional_tags, tone, and created_at.  age_hours is NOT stored —
    it is computed dynamically by read_afterglow_residue() from created_at.

    SECURITY NOTE: Caller MUST hold a WriteEnvelope with can_write_memory=True
    AND source=DREAM_AFTERGLOW before calling this function.  This store does
    NOT enforce the envelope gate.

    Returns True on success, False on I/O error.  Never raises.
    """
    if not isinstance(residue, AfterglowResidueInput):
        logger.warning("[afterglow] save_afterglow_residue: invalid residue type %r", type(residue).__name__)
        return False

    path: Path = get_paths().user_memory_root(uid) / AFTERGLOW_FILENAME
    data = {
        "emotional_tags": list(residue.emotional_tags),
        "tone": residue.tone,
        "created_at": created_at,
    }
    ok = safe_write_json(path, data)
    if not ok:
        logger.error("[afterglow] save failed for uid=%s", uid)
    return ok


def save_hidden_state(uid: str | int, state: UserHiddenState) -> bool:
    """Persist UserHiddenState for uid using an atomic write.

    Returns True on success, False on I/O error.  Never raises.

    SECURITY NOTE: Callers MUST already hold a WriteEnvelope with
    can_write_memory=True before calling this function.  This store
    does NOT enforce the envelope gate — that responsibility belongs
    to the caller (e.g., the Reality-side integrator).

    Path: user_memory_root(uid) / hidden_state.json
    """
    path: Path = get_paths().user_memory_root(uid) / HIDDEN_STATE_FILENAME
    data = to_dict(state)
    ok = safe_write_json(path, data)
    if not ok:
        logger.error("[hidden_state] save failed for uid=%s", uid)
    return ok
