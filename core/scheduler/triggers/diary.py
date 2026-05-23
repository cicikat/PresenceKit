import logging
import time
from datetime import datetime, date

from core.error_handler import log_error
from core.scheduler.loop import (
    _is_ready, _mark, _owner_id, _pipeline_send, _cfg, _char_name,
    _last_diary_share, _scheduler_start_time,
)

logger = logging.getLogger(__name__)


async def _check_diary_reminder():
    """昨天没写日记时，角色提醒"""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return
    if not _is_ready("diary_reminder"):
        return
    now = datetime.now()
    if not (9 <= now.hour < 12):
        return
    try:
        from core.tools.diary_reader import yesterday_missing
        if yesterday_missing():
            from datetime import timedelta
            yesterday = (date.today() - timedelta(days=1)).strftime("%m月%d日")
            await _pipeline_send(
                f"（{_char_name()}翻到了{yesterday}的日期）",
                search_query="日记",
                trigger_name="diary_reminder",
            )
            _mark("diary_reminder")
            logger.info("[scheduler] 日记缺失提醒已发送")
    except Exception as e:
        log_error("scheduler._check_diary_reminder", e)


def propose_diary_reminder(ctx: dict | None = None):
    """Shadow proposal for diary_reminder; read-only and does not mark cooldown."""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return None
    now = _proposal_now(ctx)
    if not (9 <= now.hour < 12):
        return None
    oid = _owner_id()
    if not oid:
        return None
    from core.scheduler.rhythm import daytime_window_ratio, quiet_floor_elapsed, triggered_on_logical_day

    if not quiet_floor_elapsed(oid, _proposal_ts(ctx, now)):
        return None
    if triggered_on_logical_day("diary_reminder", now):
        return None
    try:
        from core.tools.diary_reader import yesterday_missing
        if not yesterday_missing():
            return None
    except Exception as e:
        log_error("scheduler.propose_diary_reminder", e)
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="diary_reminder",
        urgency=urgency_in_tier(UrgencyTier.DAILY_RHYTHM, daytime_window_ratio(now, 9, 12)),
        topic_source="diary",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
    )


async def _check_diary_inject():
    """每6小时读取最近日记，存入diary_context独立存储，不写event_log"""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return
    if not _is_ready("diary_inject"):
        return
    oid = _owner_id()
    if not oid:
        return
    try:
        from core.tools.diary_reader import read_recent
        from core.memory.diary_context import save
        text = read_recent(days=2)
        if text:
            save(oid, text)
            _mark("diary_inject")
            logger.info("[scheduler] 日记内容已存入diary_context")
    except Exception as e:
        log_error("scheduler._check_diary_inject", e)


async def _check_diary_share_reminder():
    """超过3天没看到日记分享时，角色超不经意提一句"""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return
    if time.time() - _scheduler_start_time < 300:
        return
    if not _is_ready("diary_share_reminder"):
        return
    now = datetime.now()
    if now.hour < 22:
        return
    if _last_diary_share > 0:
        from datetime import date as _date
        if datetime.fromtimestamp(_last_diary_share).date() == _date.today():
            return
    if time.time() - _last_diary_share < 259200:  # 3天内分享过就跳过
        return
    oid = _owner_id()
    if not oid:
        return
    try:
        await _pipeline_send(
            f"（{_char_name()}发现自己好几天没看到你写的东西了）",
            search_query="日记",
            trigger_name="diary_share_reminder",
        )
        _mark("diary_share_reminder")
        logger.info("[scheduler] 日记分享提醒已发送")
    except Exception as e:
        log_error("scheduler._check_diary_share_reminder", e)


def propose_diary_share_reminder(ctx: dict | None = None):
    """Shadow proposal for diary_share_reminder; read-only and does not mark cooldown."""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return None
    now = _proposal_now(ctx)
    now_ts = _proposal_ts(ctx, now)
    if now_ts - _scheduler_start_time < 300:
        return None
    if now.hour < 22:
        return None
    oid = _owner_id()
    if not oid:
        return None
    from core.scheduler import loop
    from core.scheduler.rhythm import logical_day, quiet_floor_elapsed, triggered_on_logical_day

    if not quiet_floor_elapsed(oid, now_ts):
        return None
    if triggered_on_logical_day("diary_share_reminder", now):
        return None
    last_diary_share = float(loop._last_diary_share or 0)
    if last_diary_share > 0:
        if logical_day(datetime.fromtimestamp(last_diary_share)) == logical_day(now):
            return None
    if now_ts - last_diary_share < 259200:
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="diary_share_reminder",
        urgency=urgency_in_tier(UrgencyTier.DAILY_RHYTHM, _same_day_ratio(now, 22, 24)),
        topic_source="diary",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
    )


def _proposal_now(ctx: dict | None) -> datetime:
    if ctx and ctx.get("now_dt") is not None:
        return ctx["now_dt"]
    if ctx and ctx.get("now_ts") is not None:
        return datetime.fromtimestamp(float(ctx["now_ts"]))
    return datetime.now()


def _proposal_ts(ctx: dict | None, now: datetime) -> float:
    if ctx and ctx.get("now_ts") is not None:
        return float(ctx["now_ts"])
    return now.timestamp()


def _same_day_ratio(now: datetime, start_hour: int, end_hour: int) -> float:
    start_minutes = start_hour * 60
    end_minutes = end_hour * 60
    now_minutes = now.hour * 60 + now.minute + now.second / 60
    total = end_minutes - start_minutes
    if total <= 0:
        return 1.0
    return max(0.0, min(1.0, (now_minutes - start_minutes) / total))
