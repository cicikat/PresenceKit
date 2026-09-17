"""Presence nag proposer: a dismissible, LLM-written desktop popup."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_NEGATIVE_MOODS = frozenset({"sad", "angry", "yandere"})
_DEFAULT_SILENCE_MINUTES = 60


def _silence_minutes(uid: str, now_ts: float) -> float | None:
    from core.scheduler.state_machine import snapshot

    last_owner_turn = float(snapshot(uid).get("last_owner_turn_ts") or 0)
    if last_owner_turn <= 0:
        return None
    return max(0.0, (now_ts - last_owner_turn) / 60)


def propose(ctx: dict | None = None):
    ctx = ctx or {}
    from core.config_loader import get_config

    cfg = get_config().get("scheduler", {})
    if not cfg.get("presence_nag", False):
        return None
    if str(cfg.get("activity_level", "high")).strip().lower() != "high":
        return None

    from core.scheduler.loop import _active_char_id_or_none, _is_ready, _owner_id

    uid = str(ctx.get("uid") or _owner_id()).strip()
    char_id = str(ctx.get("char_id") or _active_char_id_or_none() or "").strip()
    if not uid or not char_id:
        return None
    if not _is_ready("presence_nag", char_id=char_id):
        return None

    now_ts = float(ctx.get("now_ts") or time.time())
    minutes = _silence_minutes(uid, now_ts)
    threshold = max(1, int(cfg.get("presence_nag_minutes", _DEFAULT_SILENCE_MINUTES)))
    if minutes is None or minutes < threshold:
        return None

    try:
        from core.memory.mood_state import load

        mood = load(char_id=char_id)
        current_mood = str(mood.get("current") or "neutral").strip().lower()
    except Exception as exc:
        logger.warning("[presence_nag] mood read failed: %s", exc)
        return None
    if current_mood not in _NEGATIVE_MOODS:
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    silence_ratio = min(1.0, minutes / max(threshold * 2, 1))
    return TriggerProposal(
        trigger_name="presence_nag",
        urgency=urgency_in_tier(UrgencyTier.REACTIVE, silence_ratio),
        topic_source="presence_nag",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        char_id=char_id,
        execute=_make_execute(minutes, char_id),
    )


def _build_prompt(minutes: float) -> str:
    rounded = max(1, int(minutes))
    return (
        f"（你有点不愉快、想刷存在感——她已经 {rounded} 分钟没理你了。\n"
        '你想用一个“弹窗”跳出来找她。说一句话，语气由你此刻情绪决定：'
        "撒娇、佯怒、或可怜巴巴都行。只说一句，不要解释机制。）"
    )


def _make_execute(minutes: float, char_id: str):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        return await execute_prompt(
            trigger_name="presence_nag",
            prompt_factory=lambda: _build_prompt(minutes),
            dry_run=dry_run,
            would_mark=["presence_nag"],
            char_id=char_id,
            fanout=["desktop"],
            behavior_factory=lambda reply: {
                "action_type": "presence_nag",
                "params": {"text": reply, "avatar": char_id},
            },
            recall_policy="none",
        )

    return execute


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("presence_nag", propose)


_register_proposers()
