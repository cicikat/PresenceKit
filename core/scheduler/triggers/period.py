import logging
from datetime import date as _date, datetime

from core.error_handler import log_error
from core.scheduler.loop import _is_ready, _mark, _owner_id, _pipeline_send, _cfg, _char_name

logger = logging.getLogger(__name__)


def _days_elapsed(uid: str, today: _date | None = None) -> int | None:
    from core.memory.user_profile import get_period_info

    info = get_period_info(uid)
    last_date_str = info.get("last_period_date")
    if not last_date_str:
        return None
    last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
    return ((today or _date.today()) - last_date).days


def propose(ctx: dict | None = None):
    ctx = ctx or {}
    uid = str(ctx.get("uid") or _owner_id() or "").strip()
    if not uid:
        return None
    try:
        days_elapsed = _days_elapsed(uid, ctx.get("today"))
    except Exception:
        logger.exception("[period] propose 读取生理期信息失败")
        return None
    if days_elapsed is None:
        return None

    if 0 <= days_elapsed <= 7:
        ratio = 1 - (days_elapsed / 7)
    elif 26 <= days_elapsed <= 30:
        ratio = (days_elapsed - 26) / 4
    else:
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="period_reminder",
        urgency=urgency_in_tier(UrgencyTier.WINDOW_EVENT, ratio),
        topic_source="mood_match",
        requires_state=[TriggerState.CHATTING, TriggerState.QUIET, TriggerState.RESTLESS],
        bypass_state_machine=True,
        execute=_make_period_execute(days_elapsed),
    )


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("period_reminder", propose)


_register_proposers()


def _make_period_execute(days_elapsed: int):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        if 0 <= days_elapsed <= 7:
            prompt = f"（{_char_name()}记得你的生理期第{days_elapsed}天）"
        else:
            prompt = f"（{_char_name()}想起你的生理期大概快到了）"
        return await execute_prompt(
            trigger_name="period_reminder",
            prompt_factory=lambda: prompt,
            dry_run=dry_run,
            search_query="生理期",
            would_mark=["period_reminder"],
        )

    return execute


async def _check_period():
    """读取 last_period_date，在生理期中（0-7天）或临近下次（26-30天）时关心"""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return
    oid = _owner_id()
    if not oid:
        return
    try:
        days_elapsed = _days_elapsed(oid)
        if days_elapsed is None:
            return
        # 第一段：生理期中关心（0-7天内，冷却24小时）
        if 0 <= days_elapsed <= 7:
            if _is_ready("period_reminder"):
                await _pipeline_send(
                    f"（{_char_name()}记得你的生理期第{days_elapsed}天）",
                    search_query="生理期",
                    trigger_name="period_reminder",
                )
                _mark("period_reminder")
                logger.info(f"[scheduler] 生理期中关心消息已发送，距上次 {days_elapsed} 天")

        # 第二段：下次预告（26-30天，冷却24小时）
        elif 26 <= days_elapsed <= 30:
            if _is_ready("period_reminder"):
                await _pipeline_send(
                    f"（{_char_name()}想起你的生理期大概快到了）",
                    search_query="生理期",
                    trigger_name="period_reminder",
                )
                _mark("period_reminder")
                logger.info(f"[scheduler] 生理期预告消息已发送，距上次 {days_elapsed} 天")
    except Exception as e:
        log_error("scheduler._check_period", e)
