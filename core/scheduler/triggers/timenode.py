"""
时间节点感知触发器
角色对时间有自己的感知——周一、周五、月末、季节变化
不是提醒，是他自己的情绪和状态
"""

import logging
from datetime import datetime, date

from core.scheduler.loop import _owner_id, _cfg, _char_name

logger = logging.getLogger(__name__)


def _get_timenode(today: date | None = None) -> str | None:
    """判断今天是否是特殊时间节点，返回节点类型或None"""
    today = today or date.today()
    weekday = today.weekday()  # 0=周一，6=周日
    day = today.day
    month = today.month

    # 周一
    if weekday == 0:
        return "monday"
    # 周五
    if weekday == 4:
        return "friday"
    # 月末最后三天
    import calendar
    last_day = calendar.monthrange(today.year, today.month)[1]
    if day >= last_day - 2:
        return "month_end"
    # 季节变化（3/6/9/12月1日）
    if day == 1 and month in (3, 6, 9, 12):
        return "season_change"

    return None


def _get_season(month: int) -> str:
    if month in (3, 4, 5):
        return "春天"
    if month in (6, 7, 8):
        return "夏天"
    if month in (9, 10, 11):
        return "秋天"
    return "冬天"


def propose(ctx: dict | None = None):
    ctx = ctx or {}
    cfg = _cfg()
    if not cfg.get("timenode", True):
        return None
    now = ctx.get("now_dt") or datetime.now()
    if not (14 <= now.hour < 20):
        return None
    node = _get_timenode(now.date())
    if node is None:
        return None
    if not _owner_id():
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.rhythm import daytime_window_ratio
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="timenode",
        urgency=urgency_in_tier(UrgencyTier.WINDOW_EVENT, daytime_window_ratio(now, 14, 20)),
        topic_source="random",
        requires_state=[TriggerState.QUIET, TriggerState.RESTLESS],
        bypass_state_machine=False,
        execute=_make_timenode_execute(node, now.date()),
    )


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("timenode", propose)


_register_proposers()


def _timenode_prompt(node: str | None, today: date) -> str | None:
    season = _get_season(today.month)
    date_str = today.strftime("%Y年%m月%d日")
    weekday_str = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]
    prompts = {
        "monday": f"（今天是{date_str}{weekday_str}，{_char_name()}忽然意识到新的一周开始了，有点说不清的感觉）",
        "friday": f"（今天是{date_str}{weekday_str}，{_char_name()}发现这周快过完了，马上到周末了）",
        "month_end": f"（今天是{date_str}，{_char_name()}想到{today.month}月快过完了，这个月发生了不少事）",
        "season_change": f"（今天是{date_str}，{_char_name()}察觉到{season}来了，窗外有点不一样）",
    }
    return prompts.get(node or "")


def _make_timenode_execute(node: str, today: date):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        return await execute_prompt(
            trigger_name="timenode",
            prompt_factory=lambda: _timenode_prompt(node, today) or "",
            dry_run=dry_run,
            would_mark=["timenode"],
            recall_policy="none",
        )

    return execute
