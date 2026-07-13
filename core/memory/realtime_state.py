"""
realtime_state — 桌面端实时状态快照（纯内存，重启清零）。
存最近一次 POST /sensor/realtime 推送的数据，无持久化。
"""
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

IDLE_LEFT_THRESHOLD  = 300  # 秒：超过此值视为用户已离开
SENSOR_GAP_THRESHOLD = 120  # 秒：两次推送间隔超过此值视为 sensor producer 中断

_snapshot: Optional[dict] = None
_continuous_at_desk_seconds: int = 0


def update(payload: dict) -> None:
    """
    接收 POST /sensor/realtime 的请求体 dict（已经过 Pydantic 校验）。
    原样存入，附加 received_at。整体替换，不 merge。
    同时维护 _continuous_at_desk_seconds 累积值。
    """
    global _snapshot, _continuous_at_desk_seconds
    try:
        now = time.time()
        idle = payload.get("input", {}).get("idle_seconds", 0)
        window = payload.get("window_seconds", 0)

        if _snapshot is None:
            # 首次推送
            _continuous_at_desk_seconds = window if idle < IDLE_LEFT_THRESHOLD else 0
        else:
            gap = now - _snapshot["received_at"]
            if gap > SENSOR_GAP_THRESHOLD:
                # sensor producer 中断过，保守重置
                _continuous_at_desk_seconds = window if idle < IDLE_LEFT_THRESHOLD else 0
            elif idle >= IDLE_LEFT_THRESHOLD:
                _continuous_at_desk_seconds = 0
            else:
                _continuous_at_desk_seconds += window

        _snapshot = {**payload, "received_at": now}
    except Exception as e:
        logger.warning(f"[realtime_state] update 失败: {e}")


def get() -> Optional[dict]:
    """无数据返回 None，有数据返回 _snapshot 的浅拷贝。"""
    if _snapshot is None:
        return None
    return dict(_snapshot)


def get_presence() -> str:
    """
    从 _snapshot["input"]["idle_seconds"] 派生在线状态：
      idle < 60        -> "active"
      60 <= idle < 300 -> "idle"
      idle >= 300      -> "away"
    无数据返回 "active"（与 StateEngine 默认一致）。
    """
    if _snapshot is None:
        return "active"
    try:
        idle = _snapshot.get("input", {}).get("idle_seconds", 0)
        if idle < 60:
            return "active"
        if idle < 300:
            return "idle"
        return "away"
    except Exception as e:
        logger.warning(f"[realtime_state] get_presence 失败: {e}")
        return "active"


def get_continuous_at_desk_seconds() -> int:
    return _continuous_at_desk_seconds
