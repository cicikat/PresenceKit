"""Character-to-character private exchange: transcript + presence stamp storage.

Storage only (Brief 86). Session orchestration (window/budget/pair selection/
generation) lives in `core/scheduler/triggers/private_exchange.py`; the admin
read-only endpoint (`admin/routers/relations.py`) reads through here too.

Hard boundary (DESIGN.md §十一 决策 9.5，决策 3「自产内容不固化」)：a transcript
appended here is the *only* legitimate resting place for the raw dialogue. Any
new write path that forwards this text into short_term/mid_term/episodic/
identity/event_log/向量库 violates this brief's premise — the only legal
reflow is the char_relations projection (summary/valence/recent_moments) and
the 12h presence stamp, both handled by the scheduler trigger, not here.
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

TRANSCRIPT_LIMIT = 200
PRESENCE_TTL_SECONDS = 12 * 3600


def _pair(char_a: str, char_b: str) -> tuple[str, str]:
    first, second = sorted((str(char_a), str(char_b)))
    if first == second:
        raise ValueError("private exchange requires two distinct characters")
    return first, second


def append_entry(
    char_a: str, char_b: str, *, speaker_id: str, content: str, ts: float | None = None
) -> bool:
    """Append one turn and trim the file back to TRANSCRIPT_LIMIT lines."""
    from core.sandbox import get_paths
    from core.safe_write import safe_append_jsonl

    path = get_paths().private_exchange_transcript(char_a=char_a, char_b=char_b)
    record = {
        "speaker_id": str(speaker_id),
        "content": str(content),
        "ts": float(ts if ts is not None else time.time()),
    }
    if not safe_append_jsonl(path, record):
        return False
    _trim(path)
    return True


def _trim(path) -> None:
    try:
        if not path.exists():
            return
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) <= TRANSCRIPT_LIMIT:
            return
        path.write_text("\n".join(lines[-TRANSCRIPT_LIMIT:]) + "\n", encoding="utf-8")
    except Exception:
        logger.debug("[private_exchange] trim suppressed", exc_info=True)


def load_transcript(char_a: str, char_b: str, *, limit: int | None = None) -> list[dict]:
    from core.sandbox import get_paths

    path = get_paths().private_exchange_transcript(char_a=char_a, char_b=char_b)
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
    except Exception:
        logger.debug("[private_exchange] load_transcript suppressed", exc_info=True)
        return []
    if limit is not None and limit > 0:
        entries = entries[-limit:]
    return entries


def last_exchange_ts(char_a: str, char_b: str) -> float:
    entries = load_transcript(char_a, char_b, limit=1)
    if not entries:
        return 0.0
    try:
        return float(entries[-1].get("ts", 0.0))
    except (TypeError, ValueError):
        return 0.0


def write_presence_stamp(char_id: str, other_id: str, *, ts: float | None = None) -> bool:
    from core.sandbox import get_paths
    from core.safe_write import safe_write_json

    path = get_paths().private_exchange_presence(char_id=char_id)
    return safe_write_json(
        path,
        {"other_char_id": str(other_id), "ts": float(ts if ts is not None else time.time())},
    )


def read_presence_hint(char_id: str) -> str:
    """Return "刚才和X聊了会儿" for a fresh (<12h) stamp, else "" (missing/expired/error)."""
    from core.sandbox import get_paths

    path = get_paths().private_exchange_presence(char_id=char_id)
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = float(data.get("ts", 0.0))
        other_id = str(data.get("other_char_id") or "")
    except Exception:
        return ""
    if not other_id or time.time() - ts >= PRESENCE_TTL_SECONDS:
        return ""
    try:
        from core.character_name_provider import get_char_name

        name = get_char_name(other_id)
    except Exception:
        name = other_id
    return f"刚才和{name}聊了会儿"
