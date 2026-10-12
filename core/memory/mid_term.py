"""
过去 12 小时对话压缩视图（mid_term 记忆层）
定位：在 short_term（最近 20 轮 history）和 character_growth 之间，
解决已出 history 窗口但仍属"近期"的记忆缺失。
"""

import json
import logging
import time
import re
from pathlib import Path

from core.error_handler import log_error
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.safe_write import safe_write_json
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

EXPIRE_SECONDS = 12 * 3600
MAX_EVENTS = 20
PROMPT_MAX_EVENTS = 8
PROMPT_CHAR_BUDGET = 1200


def _read_file(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(str(uid), char_id)
    return resolve_path(scope, "mid_term")


def _write_file(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(str(uid), char_id)
    p = resolve_path(scope, "mid_term")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict]:
    """读取所有未过期事件，按 ts 升序返回。文件不存在返回 []。"""
    path = _read_file(uid, char_id=char_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        now = time.time()
        events = [e for e in data.get("events", []) if now - e.get("ts", 0) < EXPIRE_SECONDS]
        return sorted(events, key=lambda e: e["ts"])
    except Exception as e:
        log_error("mid_term.load", e)
        return []


def append(
    uid: str,
    summary: str,
    tags: list[str] | None = None,
    mid_id: str | None = None,
    source_turn_id: str | None = None,
    source_event_ids: list[str] | None = None,
    *,
    char_id: str = DEFAULT_CHAR_ID,
    source: str = "",
    memory_strength: float = 1.0,
    is_trigger_turn: bool = False,
    occurred_at: float | None = None,
    input_modality: str = "",
    asr_low_confidence: bool = False,
    source_refs: list[str] | None = None,
) -> None:
    """追加事件；追加前先清理过期 + 截断到 MAX_EVENTS-1。

    mid_id / source_turn_id 是固化 pipeline 的血缘字段，旧数据缺失按 None 处理。
    occurred_at: 事件真实发生时刻（turn 时刻）；None 时回退到记录时刻 now。
    """
    summary = summary.strip()
    if not summary:
        return
    read_path = _read_file(uid, char_id=char_id)
    write_path = _write_file(uid, char_id=char_id)
    try:
        if read_path.exists():
            data = json.loads(read_path.read_text(encoding="utf-8"))
            events = data.get("events", [])
        else:
            events = []
        now = time.time()
        events = [e for e in events if now - e.get("ts", 0) < EXPIRE_SECONDS]
        if len(events) >= MAX_EVENTS:
            events = events[-(MAX_EVENTS - 1):]
        from core.memory.lineage import normalize_source_event_ids
        entry: dict = {
            "ts": now,
            "occurred_at": occurred_at if isinstance(occurred_at, (int, float)) else now,
            "summary": summary,
            "tags": tags or [],
            "mid_id": mid_id,
            "source_turn_id": source_turn_id,
            "source_event_ids": normalize_source_event_ids(source_event_ids),
            "promoted_to_episodic_id": None,
            "source": source,
            "memory_strength": max(0.0, min(1.0, float(memory_strength))),
            "is_trigger_turn": is_trigger_turn,
        }
        if input_modality == "voice":
            entry["input_modality"] = "voice"
            entry["asr_low_confidence"] = bool(asr_low_confidence)
        if source_refs:
            entry["source_refs"] = list(dict.fromkeys(ref for ref in source_refs
                if isinstance(ref, str) and ref.startswith("activity:") and len(ref) <= 256))[:20]
        events.append(entry)
        safe_write_json(write_path, {"events": events})
    except Exception as e:
        log_error("mid_term.append", e)
        from core import silent_failure
        silent_failure.note("mid_term.append", e)


def mark_promoted(uid: str, mid_id: str, ep_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    """将 mid_term 里某条 entry 的 promoted_to_episodic_id 字段置为 ep_id。幂等。"""
    read_path = _read_file(uid, char_id=char_id)
    write_path = _write_file(uid, char_id=char_id)
    try:
        if not read_path.exists():
            return
        data = json.loads(read_path.read_text(encoding="utf-8"))
        events = data.get("events", [])
        found = False
        for e in events:
            if e.get("mid_id") == mid_id and not e.get("promoted_to_episodic_id"):
                e["promoted_to_episodic_id"] = ep_id
                found = True
                break
        if found:
            safe_write_json(write_path, {"events": events})
    except Exception as e:
        log_error("mid_term.mark_promoted", e)


def delete_event(uid: str, mid_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """Delete one mid-term event by mid_id.

    Returns True if found and removed. Appends provenance record on success.
    """
    read_path = _read_file(uid, char_id=char_id)
    write_path = _write_file(uid, char_id=char_id)
    try:
        if not read_path.exists():
            return False
        data = json.loads(read_path.read_text(encoding="utf-8"))
        events = data.get("events", [])
        before_gist = ""
        new_events = []
        for e in events:
            if e.get("mid_id") == mid_id:
                before_gist = e.get("summary", "")[:120]
            else:
                new_events.append(e)
        if len(new_events) == len(events):
            return False
        safe_write_json(write_path, {"events": new_events})

        try:
            from core.memory import provenance_log
            provenance_log.append(
                uid, char_id,
                artifact="mid_term",
                field=mid_id,
                before_gist=before_gist,
                after_gist="",
                trigger_signal="explicit_forget",
                origin={"source": "admin"},
            )
        except Exception:
            pass
        return True
    except Exception as e:
        log_error("mid_term.delete_event", e)
        return False


def clear(uid: str, *, char_id: str = DEFAULT_CHAR_ID, origin: dict | None = None) -> int:
    """Clear the user's current mid-term bucket and return the removed count."""
    read_path = _read_file(uid, char_id=char_id)
    write_path = _write_file(uid, char_id=char_id)
    try:
        if not read_path.exists():
            return 0
        data = json.loads(read_path.read_text(encoding="utf-8"))
        events = data.get("events", [])
        if not events:
            return 0
        before_gist = "；".join(str(event.get("summary", "")) for event in events)[:120]
        safe_write_json(write_path, {"events": []})
        try:
            from core.memory import provenance_log
            provenance_log.append(
                uid, char_id,
                artifact="mid_term",
                field="all",
                before_gist=before_gist,
                after_gist="",
                trigger_signal="explicit_forget",
                origin=origin or {"source": "assistant_tool", "tool": "clear_midterm"},
            )
        except Exception:
            pass
        return len(events)
    except Exception as e:
        log_error("mid_term.clear", e)
        return 0


def format_for_prompt(
    uid: str, *, char_id: str = DEFAULT_CHAR_ID, history: list[dict] | None = None,
) -> str:
    """Only supplement selected history, with dated, bounded derived summaries."""
    events = [e for e in load(uid, char_id=char_id) if not e.get("invalidated")]
    history = history or []
    turn_ids = {m.get("_turn_id") for m in history if m.get("_turn_id")}
    # Exact textual containment only: do not guess semantic equivalence.
    history_texts = [re.sub(r"\s+", "", str(m.get("content", ""))) for m in history]

    def event_time(event):
        occurred = event.get("occurred_at")
        return occurred if isinstance(occurred, (int, float)) and occurred > 0 else event["ts"]

    candidates = []
    seen = set()
    for event in sorted(events, key=event_time, reverse=True):
        summary = re.sub(r"\s+", " ", str(event.get("summary", ""))).strip()
        if not summary or event.get("source_turn_id") in turn_ids:
            continue
        normalized = re.sub(r"\s+", "", summary)
        if normalized in seen or any(normalized in text for text in history_texts if text):
            continue
        seen.add(normalized)
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(event_time(event)))
        line = f"- {stamp}｜{summary}"
        # Never cut a summary into an ambiguous unfinished fragment.
        if len(line) > PROMPT_CHAR_BUDGET:
            continue
        if sum(len(item[1]) + 1 for item in candidates) + len(line) > PROMPT_CHAR_BUDGET:
            continue
        candidates.append((event_time(event), line))
        if len(candidates) >= PROMPT_MAX_EVENTS:
            break
    return "\n".join(line for _, line in sorted(candidates, key=lambda item: item[0]))
