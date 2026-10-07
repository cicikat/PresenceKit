"""
reply_context — 引用回复(reply_to)前缀构造（Brief 98 §2）。

desktop / mobile 共用契约：聊天请求体可选携带
    reply_to: {text: str, ts: float, message_id?: str}
稳定锚点复用现实事件台账；双方气泡均可引用，作者和时间由服务端核验。
旧文本/时间引用只是客户端声明，不能展开精确上下文。

前缀直接拼进用户消息内容，随 message 一起进入 pipeline（fetch_context /
build_prompt / capture_turn），short_term / mid_term / event_log 因此自然捕获，
无需任何记忆层改造。

校验失败（text 为空、ts 非法）一律静默降级为普通消息，不抛异常——引用回复是
体验增强，不应该因为客户端传参异常而打断整轮对话。
"""

from __future__ import annotations

import time
import math
from datetime import datetime

_MAX_TEXT_LEN = 200
# 允许的未来时间容差：抵消客户端/服务端小幅时钟偏差，不代表真的接受"未来消息"。
_FUTURE_TOLERANCE_SEC = 5.0


def format_relative_time(ts: float, now: float | None = None) -> str:
    """把时间戳格式化为「今天 HH:MM」/「N 天前」/「M月D日」。

    按自然日边界判定（非按 24h 滚动窗口）：
    - 与 now 同一天 → 今天 HH:MM
    - 相差 1-6 个自然日 → N 天前
    - 相差 >=7 个自然日 → M月D日
    """
    if now is None:
        now = time.time()
    dt = datetime.fromtimestamp(ts)
    now_dt = datetime.fromtimestamp(now)
    delta_days = (now_dt.date() - dt.date()).days
    if delta_days <= 0:
        return f"今天 {dt.strftime('%H:%M')}"
    if delta_days <= 6:
        return f"{delta_days}天前"
    return f"{dt.month}月{dt.day}日"


def build_reply_prefix(reply_to: dict | None, now: float | None = None, *, user_id=None, char_id=None) -> str | None:
    """校验 reply_to 并构造前缀；非法输入返回 None（调用方应降级为普通消息）。"""
    if not isinstance(reply_to, dict):
        return None
    if user_id is not None and char_id is not None and reply_to.get('message_id'):
        from core.memory.event_query import EventQueryError
        from core.memory.scope import MemoryScope
        from core.tools.message_context import message_item,get_message_event
        try:
            event = get_message_event(MemoryScope.reality_scope(str(user_id), char_id), str(reply_to['message_id']))
        except EventQueryError:
            event = None
        if event and not event.get('tombstoned') and event['kind'] in {'user_message', 'assistant_message', 'trigger_assistant'}:
            item = message_item(event)
            author = '用户' if item['author'] == 'user' else '角色'
            return f"用户引用回复：作者={author}，时间={item['datetime']}，message_id={item['message_id']}，原文「{item['text'][:_MAX_TEXT_LEN]}」："
        return '用户引用的消息锚点不可核验（可能已裁剪或不属于当前对话），请勿猜测作者、时间或上下文：'
    text = reply_to.get("text")
    ts = reply_to.get("ts")
    if not isinstance(text, str) or not text.strip():
        return None
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return None
    ts = float(ts)
    if now is None:
        now = time.time()
    if not math.isfinite(ts) or ts < 0 or ts > now + _FUTURE_TOLERANCE_SEC:
        return None

    truncated = text.strip()[:_MAX_TEXT_LEN]
    try:
        exact = datetime.fromtimestamp(ts).astimezone().isoformat(timespec='seconds')
    except (OverflowError, OSError, ValueError):
        return None
    return f"用户引用回复：旧引用无稳定锚点，作者未经核验，客户端时间={exact}，原文「{truncated}」："


def apply_reply_prefix(message: str, reply_to: dict | None, now: float | None = None, *, user_id=None, char_id=None) -> str:
    """在 message 前拼 reply_to 前缀；reply_to 缺失/非法时原样返回 message。"""
    prefix = build_reply_prefix(reply_to, now, user_id=user_id, char_id=char_id)
    return (prefix + message) if prefix else message
