"""Overflow proposer: speak only when enough grounded reasons accumulate."""

from __future__ import annotations

import logging
import random

from core.scheduler.overflow_bucket import (
    OVERFLOW_JITTER,
    OVERFLOW_THRESHOLD,
    OverflowSignals,
    compute_signals,
)

logger = logging.getLogger(__name__)


def propose(ctx: dict | None = None):
    ctx = ctx or {}
    from core.config_loader import get_config

    cfg = get_config().get("scheduler", {})
    if not cfg.get("overflow_trigger", True):
        return None

    from core.scheduler.loop import _active_char_id_or_none, _is_ready, _owner_id

    uid = str(ctx.get("uid") or _owner_id()).strip()
    if not uid:
        return None

    char_id = str(ctx.get("char_id") or _active_char_id_or_none() or "").strip()
    if not char_id:
        return None
    if not _is_ready("overflow", char_id=char_id):
        return None
    try:
        signals = compute_signals(uid, char_id=char_id)
    except Exception as exc:
        logger.warning("[overflow_proposer] compute_signals failed: %s", exc)
        return None

    jitter = random.uniform(-OVERFLOW_JITTER, OVERFLOW_JITTER)
    if not signals.is_overflow(jitter=jitter):
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    score_ratio = min(1.0, signals.bucket_score() / (OVERFLOW_THRESHOLD * 1.5))
    return TriggerProposal(
        trigger_name="overflow",
        urgency=urgency_in_tier(UrgencyTier.REACTIVE, score_ratio),
        topic_source="overflow_bucket",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        execute=_make_execute(signals),
    )


def _build_context_hint(signals: OverflowSignals) -> str:
    detail = signals.top_signal_detail
    if signals.top_signal == "episodic" and detail:
        return f"最近又想起了这件事：{detail}"
    if signals.top_signal == "hidden_need":
        return detail or "有一点想靠近她、确认她此刻好不好"
    if signals.top_signal == "garden":
        return f"花园里有件事想和她分享：{detail}"
    if signals.top_signal == "mood":
        return detail or "此刻的情绪很满，想找她说句话"
    if signals.top_signal == "time_gap":
        return detail or "她有一阵子没有消息了，开始想她"
    return "此刻有一句话想主动告诉她"


def _build_overflow_prompt(signals: OverflowSignals) -> str:
    return (
        f"（你此刻的内心状态：{_build_context_hint(signals)}。\n"
        "这不是定时问候。顺着这个真实缘由，自然地联系她；可以说想到的事、"
        "当下的感受，或只简单问候一句。不要向她解释触发机制，也不要像客服回访。）"
    )


def _make_execute(signals: OverflowSignals):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        return await execute_prompt(
            trigger_name="overflow",
            prompt_factory=lambda: _build_overflow_prompt(signals),
            dry_run=dry_run,
            would_mark=["overflow"],
        )

    return execute


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("overflow", propose)


_register_proposers()
