"""
sensor_events.py — sensor 候选事件检测器。

每次 tick() 由 scheduler 主循环调用（频率 ~30s），检查 8 类候选事件，
维护各事件独立 cooldown（模块级内存，重启清零）。
不调 LLM，不发消息，不持久化。
"""
import logging
import time
from datetime import datetime
from typing import Optional

from core.memory import realtime_state
from core import activity_manager
from core.scheduler.rhythm import is_quiet_sleep_time

logger = logging.getLogger(__name__)

# ── 事件类型常量 ─────────────────────────────────────────────────────────────
PRESENCE_LEFT        = "PRESENCE_LEFT"
PRESENCE_RETURNED    = "PRESENCE_RETURNED"
LONG_FOCUS           = "LONG_FOCUS"
FOCUS_SCATTERED      = "FOCUS_SCATTERED"
SILENT_TOGETHER      = "SILENT_TOGETHER"
APP_CATEGORY_CHANGED = "APP_CATEGORY_CHANGED"
LATE_NIGHT_ACTIVE    = "LATE_NIGHT_ACTIVE"
LONG_AT_DESK         = "LONG_AT_DESK"

# ── 每事件 cooldown（秒）────────────────────────────────────────────────────
_COOLDOWN_SECS: dict[str, int] = {
    PRESENCE_LEFT:        30 * 60,
    PRESENCE_RETURNED:    20 * 60,
    LONG_FOCUS:           60 * 60,
    FOCUS_SCATTERED:      45 * 60,
    SILENT_TOGETHER:      40 * 60,
    APP_CATEGORY_CHANGED: 30 * 60,
    LATE_NIGHT_ACTIVE:    60 * 60,
    LONG_AT_DESK:         90 * 60,
}

# ── APP 类别映射 ─────────────────────────────────────────────────────────────
APP_CATEGORY: dict[str, str] = {
    "code.exe":       "work",
    "pycharm64.exe":  "work",
    "idea64.exe":     "work",
    "explorer.exe":   "neutral",
    "powershell.exe": "work",
    "wt.exe":         "work",
    "obsidian.exe":   "work",
    "chrome.exe":     "leisure",
    "msedge.exe":     "leisure",
    "firefox.exe":    "leisure",
    "steam.exe":      "leisure",
    "wechat.exe":     "leisure",
    "qq.exe":         "leisure",
    "spotify.exe":    "leisure",
    "vlc.exe":        "leisure",
    "com.sankuai.meituan": "takeout",
    "com.meituan.android.waimai": "takeout",
    "me.ele": "takeout",
    "com.taobao.taobao": "shopping",
}


def _app_category(app: str) -> str:
    lowered = app.lower()
    if any(word in lowered for word in ("meituan", "waimai", "ele", "外卖", "美团", "饿了么")):
        return "takeout"
    if any(word in lowered for word in ("taobao", "tmall", "jd", "shopping", "淘宝", "天猫", "京东")):
        return "shopping"
    return APP_CATEGORY.get(lowered, "neutral")


# ── 模块级状态（内存，重启清零）─────────────────────────────────────────────
_cooldowns: dict[str, float] = {}
_last_presence: Optional[str] = None
_last_presence_changed_at: Optional[float] = None
_last_app: Optional[str] = None
_last_app_category: Optional[str] = None
_last_chat_at: Optional[float] = None
_last_proactive_at: Optional[float] = None
_last_presence_was_sleep_guarded: bool = False
_focus_window_in_app_started_at: Optional[float] = None
_focus_window_in_app_name: Optional[str] = None
_recent_switch_events: list[float] = []   # 各次切换的 unix 时间戳


# ── 内部辅助 ─────────────────────────────────────────────────────────────────

def _keystroke_density(snap: dict) -> str:
    try:
        keystrokes     = snap["input"]["keystrokes"]
        window_seconds = snap["window_seconds"]
        if window_seconds <= 0:
            return "未知"
        rate = keystrokes / window_seconds
        if rate < 0.3:
            return "稀疏"
        if rate < 1.5:
            return "一般"
        return "密集"
    except Exception:
        return "未知"


def _in_cooldown(event_type: str) -> bool:
    return time.time() < _cooldowns.get(event_type, 0.0)


def _set_cooldown(event_type: str) -> None:
    _cooldowns[event_type] = time.time() + _COOLDOWN_SECS[event_type]


def _build_context(snap: dict, presence: str) -> dict:
    from core.scheduler.presence_model import derive_presence_state
    now = time.time()
    idle_secs    = snap.get("input", {}).get("idle_seconds", 0)
    at_desk_secs = realtime_state.get_continuous_at_desk_seconds()

    # away_since: use _last_presence_changed_at only when user was already absent
    # (if they just left this tick, _last_presence is still "active" → away_since=None,
    # which conservatively maps to BRIEFLY_AWAY rather than GENUINELY_ABSENT)
    away_since = _last_presence_changed_at if _last_presence in ("idle", "away") else None

    ps = derive_presence_state(
        idle_seconds=idle_secs,
        continuous_at_desk_seconds=at_desk_secs,
        last_chat_at=_last_chat_at,
        last_proactive_at=_last_proactive_at,
        now=now,
        away_since=away_since,
    )

    try:
        from core import pipeline_registry
        _pl = pipeline_registry.get()
        _char_id = getattr(_pl, "_active_character_id", None) or "yexuan"
        ye_xuan_activity = activity_manager.get_current(char_id=_char_id).get("current", "")
    except Exception:
        ye_xuan_activity = ""
    screen = snap.get("screen", {}) or {}
    visible_text   = screen.get("visible_text", []) or []
    clickable_text = screen.get("clickable_text", []) or []
    screen_text_hint  = "；".join(str(x) for x in visible_text[:8]   if str(x).strip())
    screen_click_hint = "；".join(str(x) for x in clickable_text[:8] if str(x).strip())
    focus     = snap.get("focus", {}) or {}
    focus_app = focus.get("app", "") or screen.get("package_name", "")
    title_hint = focus.get("title_hint", "") or screen.get("window_title", "")
    return {
        # ── Semantic presence layer (P1) ─────────────────────────────────────
        "presence_state":       ps,
        "presence_attribution": ps.attribution.value,
        "presence_summary":     ps.state_summary,
        # ── Deprecated raw fields (kept for compatibility, do not use in new templates) ──
        "minutes_since_last_proactive": ps.proactive_gap_min,
        "minutes_since_last_chat":      ps.conversational_gap_min,
        "presence":                     presence,
        "continuous_at_desk_seconds":   at_desk_secs,
        # ── Other context fields ─────────────────────────────────────────────
        "local_hour":          datetime.now().hour,
        "focus_app":           focus_app,
        "focus_title_hint":    title_hint,
        "screen_package":      screen.get("package_name", ""),
        "screen_app_label":    screen.get("app_label", ""),
        "screen_window_title": screen.get("window_title", ""),
        "screen_text_hint":    screen_text_hint[:300],
        "screen_click_hint":   screen_click_hint[:240],
        "keystroke_density":   _keystroke_density(snap),
        "ye_xuan_activity":    ye_xuan_activity,
    }


# ── 对外接口 ─────────────────────────────────────────────────────────────────

def notify_chat_happened() -> None:
    """chat router 调用，记录对话发生，重置 SILENT_TOGETHER / LONG_FOCUS 时间窗。"""
    global _last_chat_at
    _last_chat_at = time.time()
    _cooldowns.pop(SILENT_TOGETHER, None)
    _cooldowns.pop(LONG_FOCUS, None)


def mark_proactive_sent() -> None:
    """sensor_aware trigger 发送成功后调用。"""
    global _last_proactive_at
    _last_proactive_at = time.time()


def get_last_proactive_at() -> Optional[float]:
    return _last_proactive_at


def tick() -> list[dict]:
    """
    检查本 tick 触发的候选事件，返回事件列表。

    每条事件结构:
      {
        "type":      str,   # 事件常量
        "narrative": str,   # 第三人称事实陈述
        "context":   dict,  # 给裁决/prompt 用的快照
      }

    无事件返回 []。sensor-service 未启动或快照过旧（>90s）同样返回 []。
    """
    global _last_presence, _last_presence_changed_at
    global _last_app, _last_app_category
    global _focus_window_in_app_started_at, _focus_window_in_app_name
    global _recent_switch_events
    global _last_presence_was_sleep_guarded

    snap = realtime_state.get()
    if snap is None:
        return []

    now = time.time()
    if now - snap.get("received_at", 0) > 90:
        return []

    presence  = realtime_state.get_presence()
    idle_secs = snap.get("input", {}).get("idle_seconds", 0)

    # ── Sleep guard: 23:00–08:00 + idle≥300s → suppress all events ──────────
    # Prevents "离开/回来/几百分钟没理" during likely sleep. Still maintains
    # _last_presence state so the first post-sleep tick doesn't burst events.
    if is_quiet_sleep_time() and idle_secs >= realtime_state.IDLE_LEFT_THRESHOLD:
        if _last_presence is None or presence != _last_presence:
            _last_presence = presence
            _last_presence_changed_at = now
        _last_presence_was_sleep_guarded = True
        return []

    events: list[dict] = []
    focus        = snap.get("focus", {})
    screen       = snap.get("screen", {}) or {}
    focus_app    = focus.get("app", "") or screen.get("package_name", "")
    switch_count = focus.get("switch_count", 0)
    at_desk_secs = realtime_state.get_continuous_at_desk_seconds()
    ctx          = _build_context(snap, presence)

    # ── 1+2. PRESENCE_LEFT / PRESENCE_RETURNED ───────────────────────────────
    if _last_presence is None:
        # 首次 tick，只记录初始状态，不触发事件
        _last_presence = presence
        _last_presence_changed_at = now
    elif presence != _last_presence:
        # prev_duration = 上一个 presence 持续了多久
        prev_duration = now - (_last_presence_changed_at or now)

        # active 持续 ≥10min 后变 idle/away
        if (
            _last_presence == "active"
            and presence in ("idle", "away")
            and prev_duration >= 10 * 60
            and not _in_cooldown(PRESENCE_LEFT)
        ):
            _set_cooldown(PRESENCE_LEFT)
            events.append({
                "type":      PRESENCE_LEFT,
                "narrative": "她离开了。",
                "context":   ctx,
            })

        # idle/away 持续 ≥5min 后变 active
        # Skip if the idle period was sleep-guarded to avoid "离开了 420 分钟"
        if (
            _last_presence in ("idle", "away")
            and presence == "active"
            and prev_duration >= 5 * 60
            and not _last_presence_was_sleep_guarded
            and not _in_cooldown(PRESENCE_RETURNED)
        ):
            minutes_away = round(prev_duration / 60)
            _set_cooldown(PRESENCE_RETURNED)
            events.append({
                "type":      PRESENCE_RETURNED,
                "narrative": f"她回来了，刚离开了 {minutes_away} 分钟。",
                "context":   ctx,
            })

        _last_presence = presence
        _last_presence_changed_at = now
        _last_presence_was_sleep_guarded = False

    # ── 3. LONG_FOCUS 时间窗口维护 ────────────────────────────────────────────
    if focus_app:
        if focus_app != _focus_window_in_app_name:
            # 应用切换，重置聚焦计时窗口
            _focus_window_in_app_name = focus_app
            _focus_window_in_app_started_at = now

    if (
        _focus_window_in_app_started_at is not None
        and _focus_window_in_app_name
        and not _in_cooldown(LONG_FOCUS)
    ):
        focus_duration = now - _focus_window_in_app_started_at
        # None 视为"很久没聊天"，不阻塞触发
        chat_quiet = _last_chat_at is None or (now - _last_chat_at >= 25 * 60)
        if focus_duration >= 25 * 60 and chat_quiet and _keystroke_density(snap) != "稀疏":
            minutes_focus = round(focus_duration / 60)
            _set_cooldown(LONG_FOCUS)
            events.append({
                "type":      LONG_FOCUS,
                "narrative": (
                    f"她已经在 {_focus_window_in_app_name} 里"
                    f"连续工作了 {minutes_focus} 分钟。"
                ),
                "context": ctx,
            })

    # ── 4. FOCUS_SCATTERED ────────────────────────────────────────────────────
    for _ in range(switch_count):
        _recent_switch_events.append(now)
    five_min_ago = now - 5 * 60
    _recent_switch_events = [t for t in _recent_switch_events if t >= five_min_ago]
    if len(_recent_switch_events) >= 15 and not _in_cooldown(FOCUS_SCATTERED):
        n = len(_recent_switch_events)
        _set_cooldown(FOCUS_SCATTERED)
        events.append({
            "type":      FOCUS_SCATTERED,
            "narrative": f"她在 5 分钟内切换了 {n} 次窗口。",
            "context":   ctx,
        })

    # ── 5. SILENT_TOGETHER ───────────────────────────────────────────────────
    if presence == "active" and not _in_cooldown(SILENT_TOGETHER):
        chat_silence = _last_chat_at is None or (now - _last_chat_at >= 30 * 60)
        if chat_silence:
            if _last_chat_at is None:
                narrative = "她在，但已经很久没说话了。"
            else:
                minutes_silent = round((now - _last_chat_at) / 60)
                narrative = f"她在，但已经 {minutes_silent} 分钟没说话了。"
            _set_cooldown(SILENT_TOGETHER)
            events.append({
                "type":      SILENT_TOGETHER,
                "narrative": narrative,
                "context":   ctx,
            })

    # ── 6. APP_CATEGORY_CHANGED ──────────────────────────────────────────────
    if focus_app:
        current_cat = _app_category(focus_app)
        if (
            _last_app_category is not None
            and current_cat != _last_app_category
            and current_cat != "neutral"
            and _last_app_category != "neutral"
            and not _in_cooldown(APP_CATEGORY_CHANGED)
        ):
            _set_cooldown(APP_CATEGORY_CHANGED)
            events.append({
                "type":      APP_CATEGORY_CHANGED,
                "narrative": (
                    f"她从{_last_app_category}切换到{current_cat}了。"
                ),
                "context": ctx,
            })
        if focus_app != _last_app:
            _last_app = focus_app
            _last_app_category = current_cat

    # ── 7. LATE_NIGHT_ACTIVE ─────────────────────────────────────────────────
    local_hour = datetime.now().hour
    if (
        1 <= local_hour < 6
        and presence == "active"
        and not _in_cooldown(LATE_NIGHT_ACTIVE)
    ):
        _set_cooldown(LATE_NIGHT_ACTIVE)
        events.append({
            "type":      LATE_NIGHT_ACTIVE,
            "narrative": f"已经凌晨 {local_hour} 点了，她还醒着。",
            "context":   ctx,
        })

    # ── 8. LONG_AT_DESK ──────────────────────────────────────────────────────
    if at_desk_secs >= 7200 and not _in_cooldown(LONG_AT_DESK):
        hours = round(at_desk_secs / 3600, 1)
        _set_cooldown(LONG_AT_DESK)
        events.append({
            "type":      LONG_AT_DESK,
            "narrative": f"她已经在桌前坐了 {hours} 小时没起身。",
            "context":   ctx,
        })

    return events
