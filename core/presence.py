from core.sandbox import get_paths, _TRANSITION_CHARACTER_INNER
from core.safe_write import safe_write_json
from core.scheduler.rhythm import is_quiet_sleep_time
import time, json


def update_last_message(user_id: str) -> None:
    """记录用户本次说话时间"""
    p = get_paths().presence()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    data[user_id] = {"last_message_at": time.time()}
    safe_write_json(p, data)
    if _TRANSITION_CHARACTER_INNER:
        safe_write_json(get_paths()._p("yexuan_inner", "presence.json"), data)


def get_last_seen_text(user_id: str) -> str:
    """
    返回上次说话的自然语言描述，用于注入 prompt。
    分级：
    - < 6小时：返回空字符串（不显示）
    - 6-12小时："{N}小时前"
    - 12-24小时："大约一天前"
    - 1-3天："{N}天前"
    - 3-7天："将近一周前"
    - 7天以上："很久前"
    没有记录时返回空字符串。
    """
    if is_quiet_sleep_time():
        return ""
    p = get_paths().presence()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        last = data.get(user_id, {}).get("last_message_at", 0)
        if not last:
            return ""
        hours = (time.time() - last) / 3600
        if hours < 6:
            return ""
        elif hours < 12:
            return f"{int(hours)}小时前"
        elif hours < 24:
            return "大约一天前"
        elif hours < 72:
            return f"{int(hours // 24)}天前"
        elif hours < 168:
            return "将近一周前"
        else:
            return "很久前"
    except Exception:
        return ""
