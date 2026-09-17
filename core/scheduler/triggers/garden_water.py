"""
garden_water trigger — 每 300 分钟 roll 一次自动浇水。
开花时让角色说一句（低频里程碑，不 sample）。
"""

import logging
import time

from core.scheduler.loop import _is_ready, _mark
from core.garden import manager as garden_manager

logger = logging.getLogger(__name__)

GARDEN_EVENT_PROPOSAL_TTL_SECONDS = 10 * 60
_LAST_BLOOM_EVENTS: list[dict] = []


def _active_char_id() -> str | None:
    try:
        import json as _j
        raw = _j.loads(
            __import__("core.sandbox", fromlist=["get_paths"]).get_paths()
            .active_prompt_assets().read_text(encoding="utf-8")
        )
        cid = (raw.get("active_character") or "").strip()
    except Exception:
        logger.warning("[garden_water] active_prompt_assets 读取失败，跳过本次 tick")
        return None

    if not cid:
        logger.warning("[garden_water] active_character 为空，跳过本次 tick")
        return None

    try:
        from core.asset_registry import get_registry
        get_registry().resolve(cid, "character")
    except ValueError:
        logger.warning("[garden_water] active_character %r 不在注册表，跳过本次 tick", cid)
        return None

    return cid


async def _check_garden_water() -> None:
    if not _is_ready("garden_water"):
        return
    _mark("garden_water")

    char_id = _active_char_id()
    if char_id is None:
        return
    try:
        result = garden_manager.auto_water_tick(char_id=char_id)
    except Exception:
        logger.exception("[garden] auto_water_tick failed")
        return

    if not result or not result.get("ok"):
        return

    # 浇水本身不发言；开花（里程碑）事件只记录，实际发言由 propose_garden_bloom()
    # 走 gating 统一决策发送（D4：删除被 EXECUTE_MODE="live" 挡死的 legacy 直发分支，
    # 该分支此前 legacy_tick_should_send() 恒 False，_pipeline_send 从未被调用过）。
    for event in result.get("events", []):
        if event["type"] == "bloom":
            _remember_bloom_event(event)


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
            prompt_factory=lambda: f"（你发现花园里那株{event['name']}开了，站在那里看了一会儿。）",
            dry_run=dry_run,
            would_mark=["garden_bloom"],
            reads_cache_ok=True,
            recall_policy="none",
        )

    return execute
