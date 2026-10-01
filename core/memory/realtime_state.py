"""
realtime_state — 桌面端实时状态快照（纯内存，重启清零）。
存最近一次 POST /sensor/realtime 推送的数据，无持久化。
"""
import logging
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# ── 在场判定统一口径（工单 E1）──────────────────────────────────────────────
# get_presence() / rhythm.is_present() / sensor_events.tick() 共用以下常量。
PRESENCE_FRESHNESS_SECONDS = 90   # 快照超过此龄视为过期（桌面 30s 一包，容 3 个窗口）
PRESENCE_ACTIVE_IDLE_SECONDS = 60  # idle < 此值 -> active
IDLE_LEFT_THRESHOLD  = 300  # 秒：idle >= 此值视为用户已离开（away）
SENSOR_GAP_THRESHOLD = 120  # 秒：两次推送间隔超过此值视为 sensor producer 中断（累计在桌时长用，比新鲜度宽松）
PRESENCE_UNKNOWN = "unknown"  # 无数据或快照过期：不可当 active

# ── 键鼠短历史（工单 E2）──────────────────────────────────────────────────
# 纯内存、重启清零、绝不落盘。桌面每 30s 一个不重叠窗口，保留 <= 600s 且 <= 40 条。
INPUT_HISTORY_MAX_AGE_SECONDS = 600
INPUT_HISTORY_MAX_ENTRIES = 40
INPUT_SUMMARY_WINDOW_SECONDS = 300  # prompt 侧只看近 5 分钟
INPUT_SUMMARY_MIN_COVERED_SECONDS = 60  # 覆盖不足 1 分钟不下结论

_snapshot: Optional[dict] = None
_input_history: deque = deque(maxlen=INPUT_HISTORY_MAX_ENTRIES)  # (received_at, window_s, keys, clicks)
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
        _record_input_history(now, payload)
    except Exception as e:
        logger.warning(f"[realtime_state] update 失败: {e}")


def get() -> Optional[dict]:
    """无数据返回 None，有数据返回 _snapshot 的浅拷贝。"""
    if _snapshot is None:
        return None
    return dict(_snapshot)


def get_presence(now: Optional[float] = None) -> str:
    """
    从 _snapshot["input"]["idle_seconds"] 派生在线状态：
      idle < 60        -> "active"
      60 <= idle < 300 -> "idle"
      idle >= 300      -> "away"
    无数据、快照超过 PRESENCE_FRESHNESS_SECONDS、或解析失败 -> "unknown"
    （桌面断流不得被误判为在线）。
    """
    if _snapshot is None:
        return PRESENCE_UNKNOWN
    try:
        current = time.time() if now is None else float(now)
        if current - float(_snapshot.get("received_at") or 0) > PRESENCE_FRESHNESS_SECONDS:
            return PRESENCE_UNKNOWN
        idle = _snapshot.get("input", {}).get("idle_seconds", 0)
        if idle < PRESENCE_ACTIVE_IDLE_SECONDS:
            return "active"
        if idle < IDLE_LEFT_THRESHOLD:
            return "idle"
        return "away"
    except Exception as e:
        logger.warning(f"[realtime_state] get_presence 失败: {e}")
        return PRESENCE_UNKNOWN


def _record_input_history(now: float, payload: dict) -> None:
    inp = payload.get("input") or {}
    window = int(payload.get("window_seconds", 0) or 0)
    if window <= 0:
        return
    _input_history.append(
        (now, window, int(inp.get("keystrokes", 0) or 0), int(inp.get("mouse_clicks", 0) or 0))
    )
    while _input_history and now - _input_history[0][0] > INPUT_HISTORY_MAX_AGE_SECONDS:
        _input_history.popleft()


def get_input_activity(now: Optional[float] = None) -> Optional[dict]:
    """
    近 INPUT_SUMMARY_WINDOW_SECONDS 内键鼠频率聚合（每分钟速率）。
    覆盖时长不足、无数据或快照已过期（断流）返回 None。
    """
    current = time.time() if now is None else float(now)
    if _snapshot is None or current - float(_snapshot.get("received_at") or 0) > PRESENCE_FRESHNESS_SECONDS:
        return None
    covered = keys = clicks = 0
    for received_at, window, k, c in list(_input_history):
        if current - received_at <= INPUT_SUMMARY_WINDOW_SECONDS:
            covered += window
            keys += k
            clicks += c
    if covered < INPUT_SUMMARY_MIN_COVERED_SECONDS:
        return None
    return {
        "covered_seconds": covered,
        "keys_per_min": keys * 60.0 / covered,
        "clicks_per_min": clicks * 60.0 / covered,
    }


def describe_input_activity(now: Optional[float] = None) -> list[str]:
    """
    把近几分钟键鼠频率转成定性短语（不暴露裸数字）。
    阈值是经验值：>=120 键/分约快速打字，>=30 且覆盖 >=120s 为持续打字
    （同时替代桌面端从未发送的 edit_hint 的「正在认真输入」推导）。
    """
    act = get_input_activity(now)
    if act is None:
        return []
    kpm, cpm = act["keys_per_min"], act["clicks_per_min"]
    phrases: list[str] = []
    if kpm >= 120:
        phrases.append("近几分钟在快速打字")
    elif kpm >= 30 and act["covered_seconds"] >= 120:
        phrases.append("近几分钟一直在认真打字")
    elif kpm > 0:
        phrases.append("近几分钟只偶尔敲几下键盘")
    if cpm >= 20:
        phrases.append("鼠标点得很频繁")
    elif cpm >= 3:
        phrases.append("时不时点一下鼠标")
    elif cpm > 0:
        phrases.append("只是偶尔动一下鼠标")
    if not phrases:
        phrases.append("近几分钟键盘和鼠标几乎没动")
    return phrases


def _reset_for_tests() -> None:
    global _snapshot, _continuous_at_desk_seconds
    _snapshot = None
    _continuous_at_desk_seconds = 0
    _input_history.clear()


def get_continuous_at_desk_seconds() -> int:
    return _continuous_at_desk_seconds
