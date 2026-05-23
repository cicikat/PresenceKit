"""garden_daily — 每天扫一次 harvest 过期 / handle / vase 枯萎，关键事件让叶瑄说话。"""

import logging
import random
import time

from core.scheduler.loop import _is_ready, _mark, _pipeline_send, _char_name
from core.garden import manager as garden_manager

logger = logging.getLogger(__name__)

# 状态变更必执行；发言只有 30% 概率触发。
# 例外：ask / gift 是社交动作，必发（不过 sample）。
SAMPLE_TALK_PROB = 0.30
GARDEN_EVENT_PROPOSAL_TTL_SECONDS = 24 * 3600
_LAST_DAILY_EVENTS: list[dict] = []


async def _check_garden_daily() -> None:
    if not _is_ready("garden_daily"):
        return
    _mark("garden_daily")

    try:
        events = garden_manager.daily_check()
    except Exception:
        logger.exception("[garden] daily_check failed")
        return

    for event in events:
        _remember_daily_event(event)
        await _emit(event)


async def _emit(event: dict) -> None:
    etype = event["type"]
    name = event.get("name", "?")
    char = _char_name()

    if etype == "harvest_expired":
        if random.random() < SAMPLE_TALK_PROB:
            if not _is_ready("garden_harvest_expired"):
                return
            await _pipeline_send(
                f"（{char}发现那株{name}放太久枯掉了，悄悄处理掉了）",
                trigger_name="garden_harvest_expired",
            )
            _mark("garden_harvest_expired")
        return

    if etype == "vase_wilted":
        if random.random() < SAMPLE_TALK_PROB:
            if not _is_ready("garden_vase_wilted"):
                return
            await _pipeline_send(
                f"（花瓶里那株{name}枯掉了，{char}默默把它收了）",
                trigger_name="garden_vase_wilted",
            )
            _mark("garden_vase_wilted")
        return

    if etype == "harvest_handle":
        action = event.get("handle_action")
        # ask / gift 必发（社交动作，不过 sample）
        if action == "ask":
            if not _is_ready("garden_handle_ask"):
                return
            await _pipeline_send(
                f"（{char}捧着那株{name}，不确定该怎么办，想问问你）",
                trigger_name="garden_handle_ask",
            )
            _mark("garden_handle_ask")
            return
        if action == "gift":
            if not _is_ready("garden_handle_gift"):
                return
            language = event.get("language", "")
            tail = f"——{language}" if language else ""
            await _pipeline_send(
                f"（{char}想把那株{name}送给你{tail}）",
                trigger_name="garden_handle_gift",
            )
            _mark("garden_handle_gift")
            return
        # dry / vase / silent 走 sample
        if action in ("dry", "vase"):
            if random.random() < SAMPLE_TALK_PROB:
                if not _is_ready("garden_handle_self"):
                    return
                verb = "做成干花" if action == "dry" else "放进了花瓶"
                await _pipeline_send(
                    f"（{char}把那株{name}{verb}，没有特别说什么）",
                    trigger_name="garden_handle_self",
                )
                _mark("garden_handle_self")
        # action == "silent"：什么都不做


def _remember_daily_event(event: dict) -> None:
    _LAST_DAILY_EVENTS.append({**event, "received_at": time.time()})
    del _LAST_DAILY_EVENTS[:-20]


def _proposal_for(trigger_name: str, event_type: str, action: str | None = None, ctx: dict | None = None):
    ctx = ctx or {}
    now_ts = float(ctx.get("now_ts") or time.time())
    events = ctx.get("garden_daily_events") or _LAST_DAILY_EVENTS
    matches = []
    for event in events:
        if event.get("type") != event_type:
            continue
        if action is not None and event.get("handle_action") != action:
            continue
        if now_ts - float(event.get("received_at") or 0) <= GARDEN_EVENT_PROPOSAL_TTL_SECONDS:
            matches.append(event)
    if not matches:
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    picked = max(matches, key=lambda event: float(event.get("received_at") or 0))
    newest = float(picked.get("received_at") or 0)
    ratio = 1 - min(1.0, max(0.0, (now_ts - newest) / GARDEN_EVENT_PROPOSAL_TTL_SECONDS))
    return TriggerProposal(
        trigger_name=trigger_name,
        urgency=urgency_in_tier(UrgencyTier.REACTIVE, ratio),
        topic_source="mood_match",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        execute=_make_garden_daily_execute(trigger_name, picked),
    )


def propose_garden_harvest_expired(ctx: dict | None = None):
    return _proposal_for("garden_harvest_expired", "harvest_expired", ctx=ctx)


def propose_garden_vase_wilted(ctx: dict | None = None):
    return _proposal_for("garden_vase_wilted", "vase_wilted", ctx=ctx)


def propose_garden_handle_ask(ctx: dict | None = None):
    return _proposal_for("garden_handle_ask", "harvest_handle", action="ask", ctx=ctx)


def propose_garden_handle_gift(ctx: dict | None = None):
    return _proposal_for("garden_handle_gift", "harvest_handle", action="gift", ctx=ctx)


def propose_garden_handle_self(ctx: dict | None = None):
    ctx = ctx or {}
    now_ts = float(ctx.get("now_ts") or time.time())
    events = ctx.get("garden_daily_events") or _LAST_DAILY_EVENTS
    self_events = [
        event for event in events
        if event.get("type") == "harvest_handle"
        and event.get("handle_action") in ("dry", "vase")
        and now_ts - float(event.get("received_at") or 0) <= GARDEN_EVENT_PROPOSAL_TTL_SECONDS
    ]
    if not self_events:
        return None
    return _proposal_for(
        "garden_handle_self",
        "harvest_handle",
        action=self_events[-1].get("handle_action"),
        ctx={**ctx, "garden_daily_events": self_events},
    )


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("garden_harvest_expired", propose_garden_harvest_expired)
    register_proposer("garden_handle_ask", propose_garden_handle_ask)
    register_proposer("garden_handle_gift", propose_garden_handle_gift)
    register_proposer("garden_handle_self", propose_garden_handle_self)
    register_proposer("garden_vase_wilted", propose_garden_vase_wilted)


_register_proposers()


def _garden_daily_prompt(trigger_name: str, event: dict) -> str:
    name = event.get("name", "?")
    char = _char_name()
    if trigger_name == "garden_harvest_expired":
        return f"（{char}发现那株{name}放太久枯掉了，悄悄处理掉了）"
    if trigger_name == "garden_vase_wilted":
        return f"（花瓶里那株{name}枯掉了，{char}默默把它收了）"
    if trigger_name == "garden_handle_ask":
        return f"（{char}捧着那株{name}，不确定该怎么办，想问问你）"
    if trigger_name == "garden_handle_gift":
        language = event.get("language", "")
        tail = f"——{language}" if language else ""
        return f"（{char}想把那株{name}送给你{tail}）"
    action = event.get("handle_action")
    verb = "做成干花" if action == "dry" else "放进了花瓶"
    return f"（{char}把那株{name}{verb}，没有特别说什么）"


def _make_garden_daily_execute(trigger_name: str, event: dict):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        return await execute_prompt(
            trigger_name=trigger_name,
            prompt_factory=lambda: _garden_daily_prompt(trigger_name, event),
            dry_run=dry_run,
            would_mark=[trigger_name],
            reads_cache_ok=True,
        )

    return execute
