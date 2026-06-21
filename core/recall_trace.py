"""
core/recall_trace.py — per-turn recall audit log.

Appends one JSONL record per pipeline turn to:
  data/runtime/memory/{char_id}/{uid}/recall_trace/{date}.jsonl

Diagnostic only — never raises, never read by the generation path.
Written by fetch_context(); read by future GET /debug/recall endpoint.
"""
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def write_trace(uid: str, char_id: str, trace: dict) -> None:
    """Append one recall trace record for (uid, char_id).

    Swallows all exceptions — a trace write failure must never block the pipeline.
    """
    try:
        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path

        scope = MemoryScope.reality_scope(uid, char_id)
        trace_dir = resolve_path(scope, "recall_trace")
        trace_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        trace_file = trace_dir / f"{date_str}.jsonl"
        line = json.dumps(trace, ensure_ascii=False, default=str)
        with open(trace_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    except Exception as exc:
        logger.warning("[recall_trace] write failed uid=%s char_id=%s: %s", uid, char_id, exc)
