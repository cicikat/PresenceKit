"""相识时长事实（工单 M5）：第一次对话发生在什么时候。

只读 helper，三级回退：
  1. 事件账本（Memory Event ledger）同 scope 的 MIN(occurred_at)；
  2. event_log 按天文件里最早的日期；
  3. episodic 最小 occurred_at（缺失回退 timestamp）。
全失败返回 None。结果按天缓存（进程内；日期翻篇自然失效）。

只提供事实本身，不做任何关系评价——持续时间怎么说由模型从召回的记忆里自己判断。
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, datetime

from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

# (uid, char_id) -> (cache_day_iso, (ts | None, source))
_cache: dict[tuple[str, str], tuple[str, tuple[float | None, str]]] = {}


def _from_ledger(uid: str, char_id: str) -> float | None:
    from core.memory import event_query
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope(str(uid), char_id)
    opened = event_query._connect(scope)
    if opened is None:
        return None
    path, connection = opened
    with event_query.event_store._lock_for(path):
        try:
            row = connection.execute(
                "SELECT MIN(occurred_at) FROM events WHERE uid = ? AND char_id = ? AND realm = ? "
                "AND redaction_state != 'tombstoned'",
                (scope.uid, scope.character_id, scope.domain),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            connection.close()
    value = row[0] if row else None
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def _from_event_log(uid: str, char_id: str) -> float | None:
    from core.memory import event_log

    days = event_log.list_days(uid, char_id=char_id)
    if not days:
        return None
    earliest = min(days)
    try:
        return datetime.strptime(earliest, "%Y-%m-%d").timestamp()
    except ValueError:
        return None


def _from_episodic(uid: str, char_id: str) -> float | None:
    from core.memory.episodic_memory import _load_memories

    stamps = []
    for mem in _load_memories(uid, char_id=char_id):
        ts = mem.get("occurred_at")
        if not isinstance(ts, (int, float)):
            ts = mem.get("timestamp")
        if isinstance(ts, (int, float)) and ts > 0:
            stamps.append(float(ts))
    return min(stamps) if stamps else None


def first_interaction_info(uid: str, char_id: str = DEFAULT_CHAR_ID) -> tuple[float | None, str]:
    """返回 (timestamp | None, source)；source ∈ ledger / event_log / episodic / none。"""
    key = (str(uid), char_id)
    today = date.today().isoformat()
    cached = _cache.get(key)
    if cached and cached[0] == today:
        return cached[1]
    result: tuple[float | None, str] = (None, "none")
    for source, fn in (("ledger", _from_ledger), ("event_log", _from_event_log), ("episodic", _from_episodic)):
        try:
            ts = fn(str(uid), char_id)
        except Exception as exc:  # 每一级都 fail-open，继续回退
            logger.debug("[relationship_span] %s lookup failed: %s", source, exc)
            continue
        if ts is not None:
            result = (ts, source)
            break
    _cache[key] = (today, result)
    return result


def first_interaction_at(uid: str, char_id: str = DEFAULT_CHAR_ID) -> float | None:
    """第一次对话的时间戳；无法确定返回 None。"""
    return first_interaction_info(uid, char_id)[0]


def format_span_fact(ts: float | None, now: float | None = None) -> str:
    """「你们第一次对话是在 YYYY-MM-DD（约 N 天前）。」ts 为 None 返回空串。"""
    if ts is None:
        return ""
    now = time.time() if now is None else now
    first = datetime.fromtimestamp(ts)
    days = max(0, (datetime.fromtimestamp(now).date() - first.date()).days)
    return f"你们第一次对话是在 {first.strftime('%Y-%m-%d')}（约 {days} 天前）。"


def _reset_cache_for_tests() -> None:
    _cache.clear()
