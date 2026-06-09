"""
Activity transcript — per-session JSONL storage.

Path: data/runtime/activity/{char_id}/{uid}/{activity_type}/{session_id}/transcript.jsonl

Allowed entry types: user_chat / assistant_chat.
Forbidden writes: short_term / event_log / user_hidden_state / afterglow / impression.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.sandbox import get_paths

logger = logging.getLogger(__name__)

_MAX_BUFFER = 500  # cap on in-memory lines while loading


def _path(char_id: str, uid: str, activity_type: str, session_id: str) -> Path:
    return get_paths().activity_session_dir(
        char_id=char_id, uid=uid, activity_type=activity_type, session_id=session_id,
    ) / "transcript.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_entry(
    char_id: str,
    uid: str,
    activity_type: str,
    session_id: str,
    entry: dict,
) -> None:
    """Append one JSON entry to transcript.jsonl (creates parent dirs if needed)."""
    p = _path(char_id, uid, activity_type, session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        line = json.dumps(entry, ensure_ascii=False)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.error("[transcript] append failed session=%s: %s", session_id, e)


def load_recent(
    char_id: str,
    uid: str,
    activity_type: str,
    session_id: str,
    limit: int = 6,
) -> list[dict]:
    """Return the last *limit* entries from transcript.jsonl (oldest-first)."""
    p = _path(char_id, uid, activity_type, session_id)
    if not p.exists():
        return []
    entries: list[dict] = []
    try:
        for raw in p.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
            if len(entries) > _MAX_BUFFER:
                entries = entries[-_MAX_BUFFER:]
    except Exception as e:
        logger.error("[transcript] load failed session=%s: %s", session_id, e)
        return []
    return entries[-limit:] if limit > 0 else entries
