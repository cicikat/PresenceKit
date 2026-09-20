"""
screen_peek — 角色自主查看当前窗口全文内容的受控工具。

总开关：config.screen_peek.enabled（默认 false）。
冷却：同一文件/窗口 screen_peek.cooldown_minutes 分钟内只触发一次。
内容来源：realtime_state 快照的 screen.visible_text / screen.clickable_text。
敏感窗口已在 sensor.py 入口 fail-closed，此处不再重复过滤。
"""

import logging
import re
import time

logger = logging.getLogger(__name__)

# 内存冷却表：{ normalized_key → last_peek_ts }
_cooldown_cache: dict[str, float] = {}


def _normalize_key(raw: str) -> str:
    """把标题规范化为冷却表 key：小写、去除多余空白和符号。"""
    key = raw.strip().casefold()
    key = re.sub(r"[\s\-_/\\|:：·•]+", "_", key)
    return key[:100]


def _cooldown_minutes() -> int:
    from core.config_loader import get_config
    return int(get_config().get("screen_peek", {}).get("cooldown_minutes", 30))


def _is_enabled() -> bool:
    from core.config_loader import get_config
    return bool(get_config().get("screen_peek", {}).get("enabled", False))


async def peek_screen_content() -> str:
    """
    读取当前快照的 screen.visible_text / clickable_text，拼成受控摘要返回。
    调用前先查总开关和冷却。
    """
    if not _is_enabled():
        return "屏幕内容查看功能未开启，无法读取。"

    try:
        from core.memory import realtime_state
        snap = realtime_state.get()
    except Exception as e:
        logger.warning("[screen_peek] 读取 realtime_state 失败 type=%s", type(e).__name__)
        return "暂时无法读取屏幕内容。"

    if snap is None:
        return "当前没有可用的屏幕快照。"

    # 冷却 key 优先取 title_hint，回退到 window_title
    title_hint = str(snap.get("focus", {}).get("title_hint", "")).strip()
    window_title = str((snap.get("screen") or {}).get("window_title", "")).strip()
    raw_key = title_hint or window_title or "_unknown_"
    ck = _normalize_key(raw_key)

    now = time.time()
    last_ts = _cooldown_cache.get(ck)
    cooldown_secs = _cooldown_minutes() * 60
    if last_ts is not None and (now - last_ts) < cooldown_secs:
        remaining = int((cooldown_secs - (now - last_ts)) / 60)
        return f"刚看过这个窗口，先不重复看了（还需 {remaining} 分钟冷却）。"

    screen = snap.get("screen") or {}
    visible = [str(x).strip() for x in screen.get("visible_text", []) if str(x).strip()]
    clickable = [str(x).strip() for x in screen.get("clickable_text", []) if str(x).strip()]

    if not visible and not clickable:
        return "当前快照没有可读取的屏幕文本内容。"

    # 写冷却时间戳
    _cooldown_cache[ck] = now

    parts: list[str] = []
    if title_hint:
        parts.append(f"【窗口】{title_hint}")
    if visible:
        excerpt = "；".join(visible[:20])
        parts.append(f"【可见文字】{excerpt}")
    if clickable:
        excerpt = "；".join(clickable[:10])
        parts.append(f"【可交互元素】{excerpt}")

    return "\n".join(parts)
