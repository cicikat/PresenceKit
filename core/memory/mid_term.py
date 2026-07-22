"""
过去 12 小时对话压缩视图（mid_term 记忆层）
定位：在 short_term（最近 20 轮 history）和 character_growth 之间，
解决已出 history 窗口但仍属"近期"的记忆缺失。
"""

import json
import logging
import time
from pathlib import Path

from core.error_handler import log_error
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.safe_write import safe_write_json
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

EXPIRE_SECONDS = 12 * 3600
MAX_EVENTS = 20


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
    *,
    char_id: str = DEFAULT_CHAR_ID,
    source: str = "",
    memory_strength: float = 1.0,
    is_trigger_turn: bool = False,
    occurred_at: float | None = None,
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
        entry: dict = {
            "ts": now,
            "occurred_at": occurred_at if isinstance(occurred_at, (int, float)) else now,
            "summary": summary,
            "tags": tags or [],
            "mid_id": mid_id,
            "source_turn_id": source_turn_id,
            "promoted_to_episodic_id": None,
            "source": source,
            "memory_strength": max(0.0, min(1.0, float(memory_strength))),
            "is_trigger_turn": is_trigger_turn,
        }
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


def format_for_prompt(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> str:
    """读取 + 时间桶分组 + 渲染成 prompt 段落。空返空串。"""
    events = load(uid, char_id=char_id)
    if not events:
        return ""
    now = time.time()
    bucket_soon: list[str] = []      # < 1h
    bucket_few: list[str] = []       # 1-4h
    bucket_early: list[str] = []     # 4-12h

    for e in events:
        hours_ago = (now - e["ts"]) / 3600
        if hours_ago < 1:
            bucket_soon.append(e["summary"])
        elif hours_ago < 4:
            bucket_few.append(e["summary"])
        else:
            bucket_early.append(e["summary"])

    # 按时间顺序排列（早→近）
    filled = [
        (label, items)
        for label, items in [
            ("早些时候", bucket_early),
            ("几小时前", bucket_few),
            ("刚才", bucket_soon),
        ]
        if items
    ]
    if not filled:
        return ""

    lines = [f"{label}：{'、'.join(items)}" for label, items in filled]

    if len(lines) == 1:
        return lines[0]
    return "过去 12 小时：\n" + "\n".join(lines)
