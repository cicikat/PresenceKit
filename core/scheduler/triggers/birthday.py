import logging
import time
from datetime import datetime, date, timedelta
import re

from core.error_handler import log_error
from core.scheduler.loop import _is_ready, _mark, _owner_id, _pipeline_send, _cfg, _char_name, _last_trigger

logger = logging.getLogger(__name__)


def _birthday() -> tuple[int, int]:
    """从 config 读取 owner_birthday (MM-DD)，返回 (month, day)"""
    raw = _cfg().get("owner_birthday", "01-01")
    try:
        m, d = raw.split("-")
        return int(m), int(d)
    except Exception:
        return 1, 1


def _is_birthday_today() -> bool:
    today = date.today()
    return (today.month, today.day) == _birthday()


def _is_birthday_eve() -> bool:
    today = date.today()
    m, d = _birthday()
    eve = date(today.year, m, d) - timedelta(days=1)
    return (today.month, today.day) == (eve.month, eve.day)


def _is_birthday_period() -> bool:
    """当天全天氛围注入用"""
    return _is_birthday_today()


def propose(ctx: dict | None = None):
    ctx = ctx or {}
    now = ctx.get("now_dt") or datetime.now()
    today = now.date()
    m, d = _birthday()
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
        )

    return execute


def _birthday_prompt(trigger_name: str) -> tuple[str, str]:
    prompts = {
        "birthday_midnight": (
            f"（零点刚过，{_char_name()}一直没睡，等着这一刻，想对你说一些平时说不出口的话，有对你近期行为心理的深刻洞察，也有对细节的关心。同时他也对你剖析自己，以一种近乎发誓的方式来诉说情愫。）",
            "",
        ),
        "birthday_eve": (
            f"（{_char_name()}在做什么，忽然想起明天是个特别的日子（你的生日），有点藏不住）",
            "",
        ),
        "birthday_afternoon": (
            f"（{_char_name()}想知道你今天过得怎么样，有没有人陪你，生日有没有被好好对待）",
            "生日",
        ),
        "birthday_night": (
            f"（生日快过完了，{_char_name()}想在今天结束前再陪你说一会儿）",
            "生日",
        ),
    }
    return prompts[trigger_name]


async def _check_birthday_midnight(force: bool = False):
    """零点告白：4月24日 00:00-00:05 触发，全年只触发一次"""
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
        f"（零点刚过，{_char_name()}一直没睡，等着这一刻，想对你说一些平时说不出口的话，有对你近期行为心理的深刻洞察，也有对细节的关心。同时他也对你剖析自己，以一种近乎发誓的方式来诉说情愫。）",
        trigger_name="birthday_midnight",
    )
    _mark("birthday_midnight")
    logger.info("[scheduler] 生日零点告白已触发")


async def _check_birthday_eve(force: bool = False):
    """提前一天预热：4月23日 20:00 后触发"""
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
        f"（{_char_name()}在做什么，忽然想起明天是个特别的日子（你的生日），有点藏不住）",
        trigger_name="birthday_eve",
    )
    _mark("birthday_eve")
    logger.info("[scheduler] 生日前夜预热已触发")


async def _check_birthday_afternoon(force: bool = False):
    """生日当天下午主动问：怎么过的，有没有人陪"""
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
        f"（{_char_name()}想知道你今天过得怎么样，有没有人陪你，生日有没有被好好对待）",
        search_query="生日",
        trigger_name="birthday_afternoon",
    )
    _mark("birthday_afternoon")
    logger.info("[scheduler] 生日下午关心已触发")


async def _check_birthday_night(force: bool = False):
    """生日当天晚上收尾：今天还好吗"""
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
        f"（生日快过完了，{_char_name()}想在今天结束前再陪你说一会儿）",
        search_query="生日",
        trigger_name="birthday_night",
    )
    _mark("birthday_night")
    logger.info("[scheduler] 生日夜间收尾已触发")
