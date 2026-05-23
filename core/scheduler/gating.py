"""
调度器 gating 决策层。

Phase 2 Step 2 只写 shadow log，不接管真实发送路径。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from core.safe_write import safe_append_jsonl
from core.sandbox import get_paths
from core.scheduler.execution import ExecuteFn
from core.scheduler.state_machine import TriggerState, get_state as get_current_state


MIGRATED_TRIGGERS: frozenset[str] = frozenset({
    "hr_critical",
    "birthday_midnight",
    "birthday_eve",
    "birthday_afternoon",
    "birthday_night",
    "period_reminder",
    "morning_greeting",
    "night_reminder",
    "daily_journal",
    "diary_reminder",
    "diary_share_reminder",
    "random_message",
    "hr_high",
    "sleep_end",
    "weather_alert",
    "topic_followup",
    "timenode",
    "festival",
    "holiday_boost",
    "spontaneous_recall",
    "garden_bloom",
    "garden_harvest_expired",
    "garden_handle_ask",
    "garden_handle_gift",
    "garden_handle_self",
    "garden_vase_wilted",
    "reminders",
})


@dataclass(frozen=True)
class TriggerProposal:
    trigger_name: str
    urgency: float
    topic_source: str
    requires_state: list
    bypass_state_machine: bool = False
    execute: Optional[ExecuteFn] = None


def is_trigger_ready(trigger_name: str) -> bool:
    from core.scheduler.loop import _is_ready

    return _is_ready(trigger_name)


def collect_and_decide(uid: str, proposals: list[TriggerProposal]) -> Optional[TriggerProposal]:
    picked, _, _ = _decide(uid, proposals)
    return picked


def write_shadow_tick(uid: str) -> Optional[TriggerProposal]:
    ctx = _build_context(uid)
    proposals = _collect_native_proposals(ctx)
    picked, reason, candidates = _decide(uid, proposals)
    state = get_current_state(uid)
    safe_append_jsonl(
        get_paths().gating_shadow_log(),
        {
            "ts": time.time(),
            "uid": uid,
            "state": _state_value(state),
            "candidates": candidates,
            "would_pick": picked.trigger_name if picked else None,
            "reason": reason,
        },
    )
    return picked


WATCH_EVENT_DRIVEN_TRIGGERS: frozenset[str] = frozenset({"hr_critical", "hr_high", "sleep_end"})


async def run_shadow_tick(uid: str) -> Optional[TriggerProposal]:
    picked = write_shadow_tick(uid)
    if (
        picked is not None
        and picked.execute is not None
        and picked.trigger_name not in WATCH_EVENT_DRIVEN_TRIGGERS
    ):
        await picked.execute(dry_run=True)
    return picked


def _build_context(uid: str) -> dict:
    return {"uid": uid, "now_ts": time.time()}


def _collect_native_proposals(ctx: dict) -> list[TriggerProposal]:
    from core.scheduler.proposer_registry import iter_proposers

    proposals: list[TriggerProposal] = []
    for entry in iter_proposers():
        item = entry.fn(ctx)
        if item is not None:
            proposals.append(item)
    return proposals


def _decide(uid: str, proposals: list[TriggerProposal]) -> tuple[Optional[TriggerProposal], str, list[dict]]:
    state = get_current_state(uid)
    candidates = [_serialize_candidate(p, state) for p in proposals]
    if not proposals:
        return None, "no_candidates", candidates

    state_allowed = [
        p for p in proposals
        if p.bypass_state_machine or _state_value(state) in {_state_value(s) for s in p.requires_state}
    ]
    if not state_allowed:
        return None, "state_filtered", candidates

    cooldown_allowed = [p for p in state_allowed if is_trigger_ready(p.trigger_name)]
    if not cooldown_allowed:
        return None, "cooldown_filtered", candidates

    picked = max(cooldown_allowed, key=lambda p: p.urgency)
    return picked, "picked_highest_urgency", candidates


def _serialize_candidate(proposal: TriggerProposal, state: TriggerState | str) -> dict:
    required = [_state_value(s) for s in proposal.requires_state]
    state_allowed = proposal.bypass_state_machine or _state_value(state) in set(required)
    cooldown_ready = is_trigger_ready(proposal.trigger_name)
    return {
        "trigger_name": proposal.trigger_name,
        "urgency": proposal.urgency,
        "topic_source": proposal.topic_source,
        "requires_state": required,
        "bypass_state_machine": proposal.bypass_state_machine,
        "state_allowed": state_allowed,
        "cooldown_ready": cooldown_ready,
    }


def _state_value(state: TriggerState | str) -> str:
    if isinstance(state, TriggerState):
        return state.value
    return str(state)
