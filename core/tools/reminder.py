"""
备忘录工具
数据存储在 data/reminders/{user_id}.json
每条格式：{id, content, remind_at, done}
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from core.error_handler import log_error
from core.sandbox import get_paths, safe_user_id
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

# 支持的时间格式（按优先级顺序尝试）
_TIME_FMTS = [
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%m-%d %H:%M",
    "%m/%d %H:%M",
    "%H:%M",
]


def _read_path(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    uid = safe_user_id(user_id)
    return get_paths().user_memory_root(uid, char_id=char_id) / "reminders.json"


def _write_path(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """写路径：始终写新布局。"""
    uid = safe_user_id(user_id)
    p = get_paths().user_memory_root(uid, char_id=char_id) / "reminders.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(user_id: str) -> list:
    p = _read_path(user_id)
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log_error("reminder._load", e)
    return []


def _save(user_id: str, items: list):
    try:
        with open(_write_path(user_id), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error("reminder._save", e)


def _parse_time(time_str: str) -> datetime | None:
    """解析时间字符串，支持多种格式；仅有 HH:MM 时若已过则推到明天"""
    now = datetime.now()
    time_str = time_str.strip()
    for fmt in _TIME_FMTS:
        try:
            dt = datetime.strptime(time_str, fmt)
            if fmt == "%H:%M":
                # 仅时分：补当天日期，若已过则推到明天
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
                if dt <= now:
                    dt += timedelta(days=1)
            elif fmt in ("%m-%d %H:%M", "%m/%d %H:%M"):
                # 无年份：补当年
                dt = dt.replace(year=now.year)
            return dt
        except ValueError:
            continue
    return None


def add_reminder(user_id: str, content: str, remind_at_str: str) -> str:
    """
    添加一条备忘录。
    返回描述字符串（供 LLM 读取后转换成角色语气回复）。
    """
    dt = _parse_time(remind_at_str)
    if dt is None:
        return (
            f"无法解析时间格式：{remind_at_str}，"
            "请使用 HH:MM 或 MM-DD HH:MM 或 YYYY-MM-DD HH:MM"
        )

    # Durable lifecycle is owned by the same character's Agent Runtime 副链.
    # Do not fall back to the retired reminder JSON store on partial runtime failure.
    try:
        from core.agent_runtime.models import TaskPrincipal, CausationRef
        from core.agent_runtime.scheduler_capability import create_schedule
        principal = TaskPrincipal.reality(user_id, DEFAULT_CHAR_ID)
        receipt = create_schedule(principal, content=content, due_at=dt.timestamp(),
                                  idempotency_key=f"reminder:{user_id}:{content}:{dt.isoformat()}",
                                  causation_ref=CausationRef("reality_turn", f"reminder:{user_id}:{dt.isoformat()}"))
        return f"已记住：{content!r}，将在 {dt.strftime('%Y-%m-%d %H:%M')} 提醒你"
    except Exception as exc:
        logger.error("runtime scheduler unavailable; reminder was not created: %s", exc)
        return "提醒暂时无法创建，请稍后再试"


def get_reminders(user_id: str) -> list:
    """返回未完成的备忘录列表"""
    return [item for item in _load(user_id) if not item.get("done")]


def mark_done(user_id: str, reminder_id: str):
    """标记指定备忘录为已完成"""
    items = _load(user_id)
    for item in items:
        if item.get("id") == reminder_id:
            item["done"] = True
            break
    _save(user_id, items)


def prune_done_reminders(user_id: str, cutoff_days: int = 30) -> int:
    """删除 done=True 且 remind_at 早于 cutoff_days 天前的备忘录。返回删除数。"""
    items = _load(user_id)
    cutoff = datetime.now() - timedelta(days=cutoff_days)
    kept, pruned = [], 0
    for item in items:
        if not item.get("done"):
            kept.append(item)
            continue
        try:
            remind_at = datetime.strptime(item["remind_at"], "%Y-%m-%d %H:%M")
        except Exception:
            kept.append(item)
            continue
        if remind_at >= cutoff:
            kept.append(item)
        else:
            pruned += 1
    if pruned:
        _save(user_id, kept)
        logger.info("[reminder] 已清理 %d 条旧 done 备忘录 (uid=%s)", pruned, user_id)
    return pruned


def get_due_reminders(user_id: str) -> list:
    """返回当前已到点但未完成的备忘录（remind_at <= 现在）"""
    now = datetime.now()
    due = []
    for item in _load(user_id):
        if item.get("done"):
            continue
        try:
            remind_at = datetime.strptime(item["remind_at"], "%Y-%m-%d %H:%M")
            if remind_at <= now:
                due.append(item)
        except Exception:
            continue
    return due
