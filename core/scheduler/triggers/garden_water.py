"""
garden_water trigger — 每 30 分钟 roll 一次自动浇水。
开花时让叶瑄说一句（低频里程碑，不 sample）。
"""

import logging
import time

from core.scheduler.loop import _is_ready, _mark, _pipeline_send, _char_name
from core.garden import manager as garden_manager

logger = logging.getLogger(__name__)

GARDEN_EVENT_PROPOSAL_TTL_SECONDS = 10 * 60
_LAST_BLOOM_EVENTS: list[dict] = []


async def _check_garden_water() -> None:
    if not _is_ready("garden_water"):
        return
    _mark("garden_water")

    try:
        result = garden_manager.auto_water_tick()
    except Exception:
        logger.exception("[garden] auto_water_tick failed")
        return

    if not result or not result.get("ok"):
        return

    # 浇水本身不发言；只在开花（里程碑）时说话
    for event in result.get("events", []):
        if event["type"] == "bloom":
            _remember_bloom_event(event)
            if not _is_ready("garden_bloom"):
                continue
            await _pipeline_send(
                f"（{_char_name()}发现花园里那株{event['name']}开了，站在那里看了一会儿）",
                trigger_name="garden_bloom",
            )
            _mark("garden_bloom")


def _remember_bloom_event(event: dict) -> None:
    _LAST_BLOOM_EVENTS.append({**event, "received_at": time.time()})
    del _LAST_BLOOM_EVENTS[:-10]


def propose_garden_bloom(ctx: dict | None = None):
    ctx = ctx or {}
    now_ts = float(ctx.get("now_ts") or time.time())
    events = ctx.get("garden_bloom_events") or _LAST_BLOOM_EVENTS
    fresh = [
        event for event in events
        if now_ts - float(event.get("received_at") or 0) <= GARDEN_EVENT_PROPOSAL_TTL_SECONDS
    ]
    if not fresh:
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    picked = max(fresh, key=lambda event: float(event.get("received_at") or 0))
    newest = float(picked.get("received_at") or 0)
    ratio = 1 - min(1.0, max(0.0, (now_ts - newest) / GARDEN_EVENT_PROPOSAL_TTL_SECONDS))
    return TriggerProposal(
        trigger_name="garden_bloom",
        urgency=urgency_in_tier(UrgencyTier.REACTIVE, ratio),
        topic_source="mood_match",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        execute=_make_garden_bloom_execute(picked),
    )


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("garden_bloom", propose_garden_bloom)


_register_proposers()


def _make_garden_bloom_execute(event: dict):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        return await execute_prompt(
            trigger_name="garden_bloom",
            prompt_factory=lambda: f"（{_char_name()}发现花园里那株{event['name']}开了，站在那里看了一会儿）",
            dry_run=dry_run,
            would_mark=["garden_bloom"],
            reads_cache_ok=True,
        )

    return execute
