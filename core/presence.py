from core.sandbox import get_paths
from core.safe_write import safe_write_json
from core.scheduler.rhythm import is_quiet_sleep_time
import time, json

_LAST_SEEN_MIN_SECS = 6 * 3600   # 2.55 层显示阈值（6 小时）
_GAP_HINT_MIN_SECS = 600          # 消息边界时间提示阈值（10 分钟）


def format_gap_text(gap_seconds: float) -> str:
    """把秒数格式化为人类可读描述，如 '约3小时12分钟'。< 1分钟返回空字符串。"""
    minutes = gap_seconds / 60
    if minutes < 1:
        return ""
    if minutes < 60:
        return f"约{int(minutes)}分钟"
    hours = gap_seconds / 3600
    h = int(hours)
    m = int(minutes) % 60
    if hours < 24:
        return f"约{h}小时{m}分钟" if m else f"约{h}小时"
    days = int(hours // 24)
    rh = int(hours) % 24
    return f"约{days}天{rh}小时" if rh else f"约{days}天"


def get_gap_from_history(history: list[dict]) -> float | None:
    """从 short_term 历史中提取最近一条 user 消息的 timestamp，返回距 now 的秒数。"""
    for entry in reversed(history):
        if entry.get("role") == "user":
            ts = entry.get("timestamp")
            if ts:
                return time.time() - float(ts)
    return None


def update_last_message(user_id: str) -> None:
    """记录用户本次说话时间"""
    p = get_paths().presence()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    data[user_id] = {"last_message_at": time.time()}
    safe_write_json(p, data)


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
