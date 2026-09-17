import logging
import re
from datetime import datetime

from core.error_handler import log_error
from core.scheduler.loop import _owner_id, _cfg
from core.scheduler.last_mentioned import (
    LastMentionedTopic,
    is_recently_followed,
    mark_topic_followed,
    mark_topic_followed_shadow,
    recall_last_mentioned,
)

logger = logging.getLogger(__name__)


def _followup_signal(growth: str) -> float:
    if "未跟进话题" not in growth:
        return 0.0
    tail = growth.split("未跟进话题", 1)[1]
    lines = [line.strip() for line in tail.splitlines() if line.strip().startswith("-")]
    if not lines:
        return 0.0
    useful = [line for line in lines if "暂无" not in line and "无" not in line]
    return min(1.0, len(useful) / 3)


def propose(ctx: dict | None = None):
    ctx = ctx or {}
    cfg = _cfg()
    if not cfg.get("topic_followup", True):
        return None
    now = ctx.get("now_dt") or datetime.now()
    if not (14 <= now.hour < 22):
        return None
    oid = str(ctx.get("uid") or _owner_id()).strip()
    if not oid:
        return None
    try:
        topic = ctx.get("last_mentioned")
        if topic is None:
            topic = recall_last_mentioned(oid, now=now)
    except Exception as e:
        log_error("scheduler.propose_topic_followup.last_mentioned", e)
        return None
    if topic is None:
        return None
    if not isinstance(topic, LastMentionedTopic):
        return None
    now_ts = float(ctx.get("now_ts") or now.timestamp())
    from core.scheduler import execution as scheduler_execution

    use_shadow_dedupe = scheduler_execution.EXECUTE_MODE == "dry_run"
    if is_recently_followed(topic.topic_key, now_ts=now_ts, shadow=use_shadow_dedupe):
        return None
    ratio = max(0.0, min(1.0, float(topic.score)))

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="topic_followup",
        urgency=urgency_in_tier(UrgencyTier.REACTIVE, ratio),
        topic_source="last_mentioned",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        execute=_make_topic_followup_execute(topic),
    )


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("topic_followup", propose)


_register_proposers()


def _extract_followup_topic(growth: str) -> str:
    if "未跟进话题" not in growth:
        return ""
    tail = growth.split("未跟进话题", 1)[1]
    for line in tail.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        if "暂无" in line or "无" in line:
            continue
        topic = line.lstrip("-").strip()
        topic = re.split(r"[:：]", topic, maxsplit=1)[0].strip() or topic
        return topic[:20]
    return ""


def _make_topic_followup_execute(topic: LastMentionedTopic):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        result = await execute_prompt(
            trigger_name="topic_followup",
            prompt_factory=lambda: _topic_followup_prompt(topic),
            dry_run=dry_run,
            would_mark=["topic_followup"],
            topic_key=topic.topic_key,
            after_send=lambda: mark_topic_followed(topic.topic_key),
            # C: 锚点已是具体未跟进话题的原文，不是宽泛种子词，检索层保持开启。
            recall_policy="anchored",
        )
        if dry_run:
            mark_topic_followed_shadow(topic.topic_key)
        return result

    return execute


def _topic_followup_prompt(topic: LastMentionedTopic) -> str:
    return (
        f"（你想起最近这段还没接住的事：{topic.context}\n"
        f"顺着「{topic.topic}」轻轻问她一句后来怎么样了。别像总结旧档案，"
        f"要像顺着最近聊天自然想起来。）"
    )
