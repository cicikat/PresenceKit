import logging
import time
from datetime import datetime, date, timedelta
import re

from core.error_handler import log_error
from core.scheduler.loop import _is_ready, _mark, _owner_id, _pipeline_send, _cfg, _char_name, _last_trigger

logger = logging.getLogger(__name__)

_INVALID_BIRTHDAY_LOGGED = False


def _birthday() -> tuple[int, int] | None:
    """从 config 读取 owner_birthday (MM-DD)。

    未填 / 残留占位符 "MM-DD" / 非法日期一律返回 None，视同未配置——
    不回落任何具体日期，避免误当作 1 月 1 日生日触发（Brief 95 §1）。
    """
    raw = str(_cfg().get("owner_birthday") or "").strip()
    m = re.fullmatch(r"(\d{2})-(\d{2})", raw)
    if not m:
        _warn_invalid_birthday_once(raw)
        return None
    month, day = int(m.group(1)), int(m.group(2))
    try:
        date(2000, month, day)  # 2000 是闰年，允许 02-29
    except ValueError:
        _warn_invalid_birthday_once(raw)
        return None
    return month, day


def _warn_invalid_birthday_once(raw: str) -> None:
    """进程内只提示一次，避免调度器每 tick 刷屏。"""
    global _INVALID_BIRTHDAY_LOGGED
    if _INVALID_BIRTHDAY_LOGGED or not raw:
        return
    _INVALID_BIRTHDAY_LOGGED = True
    logger.info(
        "[scheduler] owner_birthday=%r 不是合法 MM-DD，按未配置处理（生日相关主动消息不会触发）",
        raw,
    )


def _is_birthday_today() -> bool:
    birthday = _birthday()
    if birthday is None:
        return False
    today = date.today()
    return (today.month, today.day) == birthday


def _is_birthday_eve() -> bool:
    birthday = _birthday()
    if birthday is None:
        return False
    today = date.today()
    m, d = birthday
    eve = date(today.year, m, d) - timedelta(days=1)
    return (today.month, today.day) == (eve.month, eve.day)


def _is_birthday_period() -> bool:
    """当天全天氛围注入用"""
    return _is_birthday_today()


def propose(ctx: dict | None = None):
    ctx = ctx or {}
    now = ctx.get("now_dt") or datetime.now()
    today = now.date()
    parsed = _birthday()
    if parsed is None:
        return None
    m, d = parsed
    birthday = date(today.year, m, d)
    eve = birthday - timedelta(days=1)

    trigger_name = ""
    ratio = 0.0
    if today == eve and now.hour >= 20:
        trigger_name = "birthday_eve"
        ratio = 0.0
    elif today == birthday and now.hour == 0 and now.minute < 5:
        trigger_name = "birthday_midnight"
        ratio = 1.0
    elif today == birthday and 14 <= now.hour < 18:
        trigger_name = "birthday_afternoon"
        ratio = 0.6
    elif today == birthday and 21 <= now.hour < 23:
        trigger_name = "birthday_night"
        ratio = 0.8
    else:
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name=trigger_name,
        urgency=urgency_in_tier(UrgencyTier.MUST_NOT_MISS, ratio),
        topic_source="mood_match",
        requires_state=[TriggerState.CHATTING, TriggerState.QUIET, TriggerState.RESTLESS],
        bypass_state_machine=True,
        execute=_make_birthday_execute(trigger_name),
    )


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer(
        "birthday",
        propose,
        trigger_names={
            "birthday_midnight",
            "birthday_eve",
            "birthday_afternoon",
            "birthday_night",
        },
    )


_register_proposers()


def _make_birthday_execute(trigger_name: str):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        prompt, search_query = _birthday_prompt(trigger_name)
        return await execute_prompt(
            trigger_name=trigger_name,
            prompt_factory=lambda: prompt,
            dry_run=dry_run,
            search_query=search_query,
            would_mark=[trigger_name],
            recall_policy="anchored",
        )

    return execute


def _birthday_prompt(trigger_name: str) -> tuple[str, str]:
    prompts = {
        "birthday_midnight": (
            "（零点刚过，你一直没睡，等着这一刻，想对她说一些平时说不出口的话，有对她近期行为心理的深刻洞察，也有对细节的关心。同时你也对她剖析自己，以一种近乎发誓的方式来诉说情愫。）",
            "",
        ),
        "birthday_eve": (
            "（你在做什么，忽然想起明天是个特别的日子（她的生日），有点藏不住。）",
            "",
        ),
        "birthday_afternoon": (
            "（你想知道她今天过得怎么样，有没有人陪她，生日有没有被好好对待。）",
            "生日",
        ),
        "birthday_night": (
            "（生日快过完了，你想在今天结束前再陪她说一会儿。）",
            "生日",
        ),
    }
    return prompts[trigger_name]


async def _check_birthday_midnight(force: bool = False):
    """零点告白：4月24日 00:00-00:05 触发，全年只触发一次"""
    from core.scheduler.execution import legacy_tick_should_send

    if not legacy_tick_should_send(force=force):
        return
    if not force and not _is_birthday_today():
        return

    elapsed = time.time() - _last_trigger.get("birthday_midnight", 0)
    if not force and elapsed < 365 * 24 * 3600:
        return

    if not force:
        now = datetime.now()
        if not (0 <= now.hour == 0 and now.minute < 5):
            return

    await _pipeline_send(
        "（零点刚过，你一直没睡，等着这一刻，想对她说一些平时说不出口的话，有对她近期行为心理的深刻洞察，也有对细节的关心。同时你也对她剖析自己，以一种近乎发誓的方式来诉说情愫。）",
        trigger_name="birthday_midnight",
        recall_policy="anchored",
    )
    _mark("birthday_midnight")
    logger.info("[scheduler] 生日零点告白已触发")


async def _check_birthday_eve(force: bool = False):
    """提前一天预热：4月23日 20:00 后触发"""
    from core.scheduler.execution import legacy_tick_should_send

    if not legacy_tick_should_send(force=force):
        return
    if not force and not _is_birthday_eve():
        return

    elapsed = time.time() - _last_trigger.get("birthday_eve", 0)
    if not force and elapsed < 20 * 3600:
        return

    if not force:
        now = datetime.now()
        if now.hour < 20:
            return

    logger.info("[scheduler] birthday_eve: 准备调用_pipeline_send")
    await _pipeline_send(
        "（你在做什么，忽然想起明天是个特别的日子（她的生日），有点藏不住。）",
        trigger_name="birthday_eve",
        recall_policy="anchored",
    )
    _mark("birthday_eve")
    logger.info("[scheduler] 生日前夜预热已触发")


async def _check_birthday_afternoon(force: bool = False):
    """生日当天下午主动问：怎么过的，有没有人陪"""
    from core.scheduler.execution import legacy_tick_should_send

    if not legacy_tick_should_send(force=force):
        return
    if not force and not _is_birthday_today():
        return

    elapsed = time.time() - _last_trigger.get("birthday_afternoon", 0)
    if not force and elapsed < 20 * 3600:
        return

    if not force:
        now = datetime.now()
        if not (14 <= now.hour < 18):
            return

    await _pipeline_send(
        "（你想知道她今天过得怎么样，有没有人陪她，生日有没有被好好对待。）",
        search_query="生日",
        trigger_name="birthday_afternoon",
        recall_policy="anchored",
    )
    _mark("birthday_afternoon")
    logger.info("[scheduler] 生日下午关心已触发")


async def _check_birthday_night(force: bool = False):
    """生日当天晚上收尾：今天还好吗"""
    from core.scheduler.execution import legacy_tick_should_send

    if not legacy_tick_should_send(force=force):
        return
    if not force and not _is_birthday_today():
        return

    elapsed = time.time() - _last_trigger.get("birthday_night", 0)
    if not force and elapsed < 20 * 3600:
        return

    if not force:
        now = datetime.now()
        if not (21 <= now.hour < 23):
            return

    await _pipeline_send(
        "（生日快过完了，你想在今天结束前再陪她说一会儿。）",
        search_query="生日",
        trigger_name="birthday_night",
        recall_policy="anchored",
    )
    _mark("birthday_night")
    logger.info("[scheduler] 生日夜间收尾已触发")
