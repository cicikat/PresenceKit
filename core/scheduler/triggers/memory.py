import logging
import re
import time
from datetime import datetime

from core.error_handler import log_error
from core.scheduler.loop import _is_ready, _mark, _owner_id, _pipeline_send, _cfg, _char_name, _last_trigger

logger = logging.getLogger(__name__)

_COOLDOWNS_LOCAL = {
    "topic_followup": 24 * 3600,
}


async def _check_topic_followup(force: bool = False):
    """未完结话题追问：每天一次，让LLM判断character_growth里有没有超过3天没提的话题"""
    cfg = _cfg()
    if not cfg.get("topic_followup", True):
        return
    
    elapsed = time.time() - _last_trigger.get("topic_followup", 0)
    if not force and elapsed < _COOLDOWNS_LOCAL["topic_followup"]:
        return

    if not force:
        now = datetime.now()
        if not (14 <= now.hour < 22):
            return

    oid = _owner_id()
    if not oid:
        return

    try:
        from core.memory.character_growth import load as load_growth
        from core.config_loader import get_config
        from core import llm_client

        growth = load_growth(_char_name(), oid)
        if not growth or len(growth) < 20:
            return

        today = datetime.now().strftime("%Y年%m月%d日")

        judge_prompt = [
            {
                "role": "system",
                "content": (
                    "你是一个助手，帮助分析文本中是否存在未完结的话题。\n"
                    "只输出JSON，格式：{\"has_topic\": true/false, \"topic\": \"话题简述或空字符串\"}\n"
                    "不要输出任何其他内容。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"今天是{today}。\n"
                    f"以下是{_char_name()}对用户的认知记录：\n{growth}\n\n"
                    f"请判断：其中有没有用户随口提到、但至今超过3天没有下文的事情？\n"
                    f"比如在做的某件事、想做的某件事、某段关系的进展、某个计划等。\n"
                    f"如果有，提取最值得问起的那一件，用一句话简述（10字以内）。\n"
                    f"如果没有，has_topic返回false。"
                ),
            },
        ]

        result_raw = await llm_client.chat(judge_prompt)
        if not result_raw:
            return

        import json, re
        match = re.search(r"\{.*?\}", result_raw, re.DOTALL)
        if not match:
            return

        result = json.loads(match.group())
        if not result.get("has_topic"):
            logger.info("[scheduler] topic_followup: 无未完结话题，跳过")
            return

        topic = result.get("topic", "").strip()
        if not topic:
            return

        await _pipeline_send(
            f"（{_char_name()}忽然想起来，你之前提到过「{topic}」，不知道后来怎样了）",
            trigger_name="topic_followup",
        )
        _mark("topic_followup")
        logger.info(f"[scheduler] topic_followup 已触发: {topic}")

    except Exception as e:
        log_error("scheduler._check_topic_followup", e)


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
    oid = _owner_id()
    if not oid:
        return None
    try:
        from core.memory.character_growth import load as load_growth

        growth = ctx.get("character_growth")
        if growth is None:
            growth = load_growth(_char_name(), oid)
    except Exception as e:
        log_error("scheduler.propose_topic_followup.load_growth", e)
        return None
    if not growth or len(growth) < 20:
        return None
    ratio = _followup_signal(str(growth))
    if ratio <= 0:
        return None
    topic = _extract_followup_topic(str(growth))
    if not topic:
        return None

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


def _make_topic_followup_execute(topic: str):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        return await execute_prompt(
            trigger_name="topic_followup",
            prompt_factory=lambda: f"（{_char_name()}忽然想起来，你之前提到过「{topic}」，不知道后来怎样了）",
            dry_run=dry_run,
            would_mark=["topic_followup"],
        )

    return execute
