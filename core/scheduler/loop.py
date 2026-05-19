"""
主动行为调度器 — 冷却管理、核心工具函数、主循环
"""

import asyncio
import logging
import random
import time
from datetime import datetime, date
from typing import Optional

from core.error_handler import log_error
from core.sandbox import get_paths

logger = logging.getLogger(__name__)

# ── Pipeline 注入（由 main.py 调用 set_pipeline 写入）────────────────────────
_pipeline = None


def set_pipeline(p):
    global _pipeline
    _pipeline = p


# ── 冷却时间（秒）────────────────────────────────────────────────────────────
_COOLDOWNS: dict[str, int] = {
    "morning_greeting":      8 * 3600,   # 早安：8小时（日触发一次）
    "night_reminder":        5 * 3600,   # 晚安：5小时
    "random_message":        4 * 3600,   # 随机日间：4小时冷却，带4小时保底
    "hr_high":              30 * 60,     # 心率>100：30分钟
    "hr_critical":          60 * 60,     # 心率>120：1小时
    "sleep_end":             2 * 3600,   # 睡眠结束：2小时
    "weather_alert":         6 * 3600,   # 特殊天气：6小时
    "period_reminder":      24 * 3600,   # 生理期关心：24小时
    "diary_reminder":       20 * 3600,   # 日记提醒：20小时
    "diary_inject":          6 * 3600,   # 日记注入：6小时
    "daily_journal":         1 * 3600,   # 每日手账：1小时冷却（深夜触发）
    "diary_share_reminder":  8 * 3600,   # 日记分享提醒：8小时
    "sleep_report":         20 * 3600,   # 睡眠报告：20小时
    "activity_remind":      20 * 3600,   # 运动提醒：20小时
    "topic_followup":       24 * 3600,   # 未完结话题追问：24小时
    "birthday_midnight": 365 * 24 * 3600,
    "birthday_eve":        20 * 3600,
    "birthday_afternoon":  20 * 3600,
    "birthday_night":      20 * 3600,
    "timenode":            20 * 3600,
    "festival":            20 * 3600,
    "holiday_boost":        2 * 3600,
    "episodic_decay":      20 * 3600,   # 情景记忆衰减：20小时
    "spontaneous_recall":   4 * 3600,   # 主动回忆：4小时冷却
    "dlq_monitor":         24 * 3600,   # DLQ 扫描：24小时
    "episodic_sweep":      30 * 60,     # mid_term 老化扫描：30分钟
    "garden_water":        30 * 60,     # 花园自动浇水：30分钟
    "garden_daily":        24 * 3600,   # 花园每日扫描：harvest/vase 状态
    "garden_bloom":         8 * 3600,   # 开花发言冷却（同株短期不重复）
    "garden_harvest_expired": 4 * 3600,
    "garden_handle_ask":    4 * 3600,
    "garden_handle_gift":   4 * 3600,
    "garden_handle_self":   4 * 3600,
    "garden_vase_wilted":   4 * 3600,
}

# 冷却跟踪 {trigger_name: last_unix_timestamp}
_last_trigger: dict[str, float] = {}

def _load_scheduler_state():
    """启动时从文件读回冷却状态"""
    try:
        import json
        p = get_paths().scheduler_state()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            triggers = d.get("triggers", {})
            _last_trigger.update({k: float(v) for k, v in triggers.items()})
            logger.info(f"[scheduler] 冷却状态已恢复，{len(triggers)} 个触发器")
    except Exception as e:
        logger.warning(f"[scheduler] 冷却状态读取失败: {e}")

_load_scheduler_state()


# 上次主动分享日记的时间戳（由 diary_tool 调用 mark_diary_shared 更新）
def _get_last_diary_share() -> float:
    try:
        p = get_paths().scheduler_state()
        if p.exists():
            import json
            d = json.loads(p.read_text(encoding="utf-8"))
            return float(d.get("last_diary_share", 0))
    except Exception:
        pass
    return 0.0


_last_diary_share: float = _get_last_diary_share()

# 调度器启动时间戳（用于冷启动保护）
_scheduler_start_time: float = time.time()

# sensor_aware 上次 tick 时间戳（模块级，重启清零）
_last_sensor_aware_tick: float = 0.0

# 上次用户消息时间戳（用于调度抢占检查）
_last_user_message_time: float = 0.0

# 调度器 task 句柄
_scheduler_task: Optional[asyncio.Task] = None

# 高优先级触发器：用户活跃时也强制发送
_HIGH_PRIORITY_TRIGGERS: frozenset[str] = frozenset({
    "birthday_midnight",
    "birthday_eve",
    "birthday_afternoon",
    "birthday_night",
    "period_reminder",
    "hr_critical",
})


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def _cfg() -> dict:
    from core.config_loader import get_config
    return get_config().get("scheduler", {})


def _is_ready(name: str) -> bool:
    """检查触发器是否已度过冷却期"""
    elapsed = time.time() - _last_trigger.get(name, 0)
    return elapsed >= _COOLDOWNS.get(name, 3600)


def _mark(name: str):
    """记录触发时间，同时持久化"""
    _last_trigger[name] = time.time()
    try:
        import json
        p = get_paths().scheduler_state()
        existing = {}
        if p.exists():
            existing = json.loads(p.read_text(encoding="utf-8"))
        existing["triggers"] = _last_trigger
        p.write_text(json.dumps(existing), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[scheduler] 冷却状态写入失败: {e}")


def _owner_id() -> str:
    return str(_cfg().get("owner_id", "")).strip()


def _char_name() -> str:
    from core.config_loader import get_config
    return get_config().get("character", {}).get("name", "他")


async def _send(content: str, behavior: dict | None = None):
    """广播到所有活跃通道。"""
    oid = _owner_id()
    if not oid:
        logger.warning("[scheduler] owner_id 未配置，跳过发送")
        return
    from channels.registry import broadcast
    await broadcast(content, oid, behavior=behavior)


def mark_user_active():
    global _last_user_message_time
    _last_user_message_time = time.time()


def _user_active_recently(window_seconds: int = 120) -> bool:
    return (time.time() - _last_user_message_time) < window_seconds


async def _pipeline_send(
    prompt: str,
    search_query: str = "",
    trigger_name: str = "",
    behavior: dict | None = None,
    output_mode: str = "speak",   # "speak" | "return"
    record_turn: bool = True,
) -> Optional[str]:
    """通过 Pipeline 生成角色回复，再向 owner 发送。
    Pipeline 未注入时降级直接发送 prompt 原文并打 warning。
    search_query 指定时用于 fetch_context，否则用 prompt。
    trigger_name 用于优先级判断：高优先级触发器不受活跃窗口限制。
    output_mode="return"：post_process 写记忆，但不调 _send，返回 reply 文本。
    output_mode="speak"（默认）：发送后返回 None，保持原有行为。
    """
    if trigger_name not in _HIGH_PRIORITY_TRIGGERS and _user_active_recently():
        logger.info(f"[scheduler] 用户活跃中，跳过低优先级触发: {trigger_name}")
        return None
    oid = _owner_id()
    if not oid:
        logger.warning("[scheduler._pipeline_send] owner_id 未配置，跳过")
        return None
    try:
        if _pipeline is None:
            logger.warning("[scheduler._pipeline_send] pipeline 未注入，降级直接发送")
            if output_mode != "return":
                await _send(prompt, behavior=behavior)
            return None

        from core.scheduler.triggers.birthday import _is_birthday_period
        if _is_birthday_period():
            prompt = prompt + "\n（今天是她的生日，4月24日）"
        _states = ["在思考", "在翻阅她的日记", "在想她说过的话", "在看窗外", "在灵体出游", "在家里"]
        prompt = prompt + f"\n（{_char_name()}此刻{random.choice(_states)}）"
        context = await _pipeline.fetch_context(oid, search_query or prompt)
        messages, _ = _pipeline.build_prompt(oid, prompt, context)
        reply    = await _pipeline.run_llm(messages)
        if reply:
            turn_result = None
            if record_turn:
                from core.turn_sink import TurnSource, record_assistant_turn
                if trigger_name == "sensor_aware":
                    source = TurnSource.SENSOR
                elif trigger_name in ("hr_high", "hr_critical", "sleep_end"):
                    source = TurnSource.WATCH
                else:
                    source = TurnSource.TRIGGER
                turn_result = await record_assistant_turn(
                    assistant_text=reply,
                    uid=oid,
                    source=source,
                    trigger_name=trigger_name or "scheduler",
                    fanout=[] if output_mode == "return" else "all",
                    bypass_gate=(trigger_name == "hr_critical"),
                    pipeline=_pipeline,
                )
            if output_mode == "return":
                return reply
            if turn_result and turn_result.fanout_failures:
                logger.warning(
                    "[scheduler._pipeline_send] fanout 部分失败: %s",
                    turn_result.fanout_failures,
                )
        else:
            logger.warning("[scheduler._pipeline_send] LLM 返回空内容")
    except Exception as e:
        log_error("scheduler._pipeline_send", e)
    return None


def _user_talked_today(user_id: str) -> bool:
    """检查用户今天在事件日志中是否有记录"""
    today = date.today().strftime("%Y-%m-%d")
    p = get_paths().event_log() / user_id / f"{today}.md"
    return p.exists() and p.stat().st_size > 10


def mark_diary_shared():
    global _last_diary_share
    _last_diary_share = time.time()
    try:
        import json
        p = get_paths().scheduler_state()
        existing = {}
        if p.exists():
            existing = json.loads(p.read_text(encoding="utf-8"))
        existing["last_diary_share"] = _last_diary_share
        p.write_text(json.dumps(existing), encoding="utf-8")
    except Exception as e:
        log_error("scheduler.mark_diary_shared", e)


# ── sensor_aware tick
async def _check_sensor_aware():
    global _last_sensor_aware_tick
    sa_cfg = _cfg().get("sensor_aware", {})
    if not sa_cfg.get("enabled", False):
        return
    interval = sa_cfg.get("tick_interval_seconds", 30)
    now = time.time()
    if now - _last_sensor_aware_tick < interval:
        return
    _last_sensor_aware_tick = now
    from core.scheduler.triggers.sensor_aware import handle_tick
    try:
        await handle_tick()
    except Exception:
        logger.exception("[scheduler] sensor_aware_tick 失败")


# ── 备忘录到点提醒
async def _check_reminders():
    """检查 owner 的备忘录是否有到点条目，有则发送提醒后标记完成"""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return
    oid = _owner_id()
    if not oid:
        return
    try:
        from core.tools.reminder import get_due_reminders, mark_done
        due = get_due_reminders(oid)
        for item in due:
            await _pipeline_send(
                f"备忘录提醒时间到了：{item['content']}，用{_char_name()}的方式提醒她",
                trigger_name="reminders",
            )
            mark_done(oid, item["id"])
            logger.info(f"[scheduler] 备忘录提醒已发送: {item['content']}")
    except Exception as e:
        log_error("scheduler._check_reminders", e)


# ═══════════════════════════════════════════════════════════════════════════════
# 状态查询（供管理面板）
# ═══════════════════════════════════════════════════════════════════════════════

def get_status() -> dict:
    """返回所有触发器的状态信息"""
    now = time.time()
    result = {}
    for name, cooldown in _COOLDOWNS.items():
        last = _last_trigger.get(name, 0)
        elapsed = now - last if last > 0 else cooldown + 1
        remaining = max(0, cooldown - elapsed)
        result[name] = {
            "last_triggered": (
                datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M:%S")
                if last > 0 else "从未"
            ),
            "cooldown_sec":   cooldown,
            "remaining_sec":  int(remaining),
            "ready":          remaining == 0,
        }
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 手动触发（供管理面板调用）
# ═══════════════════════════════════════════════════════════════════════════════

async def manual_trigger(name: str) -> str:
    """手动触发指定动作（绕过冷却时间和条件检查）。"""
    _last_trigger[name] = 0  # 清零冷却

    try:
        from core.scheduler.triggers.time_based import (
            _check_morning, _check_night, _check_random_message,
        )

        if name == "morning_greeting":
            await _check_morning(force=True)
        elif name == "night_reminder":
            await _check_night(force=True)
        elif name == "random_message":
            await _check_random_message(force=True)
        elif name == "daily_journal":
            oid = _owner_id()
            if not oid:
                return "owner_id 未配置"
            from core.memory.event_log import get_recent_days
            today_log = get_recent_days(oid, days=1)
            log_hint = today_log[:800] if today_log and len(today_log) > 10 else "今天还没有对话记录"
            await _pipeline_send(
                f"（深夜，{_char_name()}回想起今天和你说过的话，提笔写下今天的感受——"
                f"今天的对话内容：{log_hint}）",
                trigger_name="daily_journal",
            )
            _mark("daily_journal")
        elif name == "period_reminder":
            oid = _owner_id()
            if not oid:
                return "owner_id 未配置"
            from core.memory.user_profile import get_period_info
            from datetime import date as _date
            info = get_period_info(oid)
            last_date_str = info.get("last_period_date")
            if last_date_str:
                last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
                days_elapsed = (_date.today() - last_date).days
                await _pipeline_send(
                    f"（{_char_name()}记得你的生理期已经来了{days_elapsed}天，悄悄关心一下）",
                    trigger_name="period_reminder",
                )
            else:
                await _pipeline_send(
                    f"（{_char_name()}想关心一下你的身体状况）",
                    trigger_name="period_reminder",
                )
            _mark("period_reminder")
        elif name == "diary_reminder":
            oid = _owner_id()
            if not oid:
                return "owner_id 未配置"
            from datetime import date as _date, timedelta
            yesterday = (_date.today() - timedelta(days=1)).strftime("%m月%d日")
            await _pipeline_send(
                f"（{_char_name()}想起来，{yesterday}好像没看到你写日记）",
                trigger_name="diary_reminder",
            )
            _mark("diary_reminder")
        elif name == "diary_share_reminder":
            oid = _owner_id()
            if not oid:
                return "owner_id 1043484516"
            await _pipeline_send(
                f"（{_char_name()}想起来，好像很久没看到你的日记了，故作不经意地提一句）",
                trigger_name="diary_share_reminder",
            )
            _mark("diary_share_reminder")
        elif name == "topic_followup":
            from core.scheduler.triggers.memory import _check_topic_followup
            await _check_topic_followup(force=True)
        elif name == "birthday_midnight":
            from core.scheduler.triggers.birthday import _check_birthday_midnight
            await _check_birthday_midnight(force=True)
        elif name == "birthday_eve":
            from core.scheduler.triggers.birthday import _check_birthday_eve
            await _check_birthday_eve(force=True)
        elif name == "birthday_afternoon":
            from core.scheduler.triggers.birthday import _check_birthday_afternoon
            await _check_birthday_afternoon(force=True)
        elif name == "birthday_night":
            from core.scheduler.triggers.birthday import _check_birthday_night
            await _check_birthday_night(force=True)
        elif name == "timenode":
            from core.scheduler.triggers.timenode import _check_timenode
            await _check_timenode(force=True)
        elif name == "festival":
            from core.scheduler.triggers.festival import _check_festival
            await _check_festival(force=True)
        elif name == "holiday_boost":
            from core.scheduler.triggers.festival import _check_holiday_boost
            await _check_holiday_boost(force=True)
        else:
            return f"未知触发器: {name}"
        return f"{name} 已触发"
    except Exception as e:
        log_error(f"scheduler.manual_trigger.{name}", e)
        return f"{name} 触发失败: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# 主循环 & 启动
# ═══════════════════════════════════════════════════════════════════════════════

async def _loop():
    """调度器主循环，每 60 秒检查一次"""
    logger.info("[scheduler] 调度器已启动，每 60 秒检查一次")
    _sa = _cfg().get("sensor_aware", {})
    if _sa.get("enabled", False):
        logger.info(
            "[scheduler] sensor_aware ENABLED, tick interval=%ds",
            _sa.get("tick_interval_seconds", 30),
        )
    else:
        logger.info("[scheduler] sensor_aware DISABLED by config")
    while True:
        try:
            cfg = _cfg()
            if cfg.get("enabled", True):
                from core.scheduler.triggers.time_based import (
                    _check_morning, _check_night, _check_random_message,
                    _check_weather, _check_daily_journal, _check_episodic_decay,
                    _check_spontaneous_recall, check_activity_switch,
                    _check_dlq_monitor,
                )
                from core.scheduler.triggers.diary import (
                    _check_diary_reminder, _check_diary_inject, _check_diary_share_reminder,
                )
                from core.scheduler.triggers.period import _check_period
                from core.scheduler.triggers.memory import _check_topic_followup
                from core.scheduler.triggers.birthday import (
                    _check_birthday_midnight, _check_birthday_eve,
                    _check_birthday_afternoon, _check_birthday_night,
                )
                from core.scheduler.triggers.timenode import _check_timenode
                from core.scheduler.triggers.festival import _check_festival, _check_holiday_boost
                from core.scheduler.triggers.episodic_sweep import _check_episodic_sweep
                from core.scheduler.triggers.garden_water import _check_garden_water
                from core.scheduler.triggers.garden_daily import _check_garden_daily

                await asyncio.gather(
                    _check_morning(),
                    _check_night(),
                    _check_random_message(),
                    _check_weather(),
                    _check_reminders(),
                    _check_period(),
                    _check_diary_reminder(),
                    _check_diary_inject(),
                    _check_daily_journal(),
                    _check_episodic_decay(),
                    _check_spontaneous_recall(),
                    _check_diary_share_reminder(),
                    _check_topic_followup(),
                    _check_birthday_midnight(),
                    _check_birthday_eve(),
                    _check_birthday_afternoon(),
                    _check_birthday_night(),
                    _check_timenode(),
                    _check_festival(),
                    _check_holiday_boost(),
                    check_activity_switch(),
                    _check_dlq_monitor(),
                    _check_episodic_sweep(),
                    _check_garden_water(),
                    _check_garden_daily(),
                    _check_sensor_aware(),
                    return_exceptions=True,
                )
        except Exception as e:
            log_error("scheduler._loop", e)
        await asyncio.sleep(60)


def start() -> asyncio.Task:
    """启动调度器后台 Task，返回 Task 对象供 main.py 管理"""
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_loop())
    logger.info("[scheduler] 调度器 Task 已创建")
    return _scheduler_task


# 暴露给外部（admin/routers/watch.py 通过 scheduler.on_watch_event 调用）
from core.scheduler.triggers.watch import on_watch_event  # noqa: E402
