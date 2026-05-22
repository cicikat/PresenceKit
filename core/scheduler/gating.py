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
from core.scheduler.state_machine import TriggerState, get_state as get_current_state


@dataclass(frozen=True)
class TriggerProposal:
    trigger_name: str
    urgency: float
    topic_source: str
    requires_state: list
    bypass_state_machine: bool = False


def is_trigger_ready(trigger_name: str) -> bool:
    from core.scheduler.loop import _is_ready

    return _is_ready(trigger_name)


def collect_and_decide(uid: str, proposals: list[TriggerProposal]) -> Optional[TriggerProposal]:
    picked, _, _ = _decide(uid, proposals)
    return picked


def write_shadow_tick(uid: str) -> None:
    proposals = _adapt_legacy_triggers(uid)
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


def _adapt_legacy_triggers(uid: str) -> list[TriggerProposal]:
    del uid
    from core.scheduler import loop

    proposals: list[TriggerProposal] = []
    for name in loop._COOLDOWNS:
        if not loop._is_ready(name):
            continue
        high_priority = name in loop._HIGH_PRIORITY_TRIGGERS
        proposals.append(
            TriggerProposal(
                trigger_name=name,
                urgency=0.9 if high_priority else 0.5,
                topic_source=_topic_source_for(name),
                requires_state=[
                    TriggerState.CHATTING,
                    TriggerState.QUIET,
                    TriggerState.RESTLESS,
                ] if high_priority else [TriggerState.QUIET],
                bypass_state_machine=high_priority,
            )
        )
    return proposals


def _topic_source_for(trigger_name: str) -> str:
    if trigger_name == "topic_followup":
        return "last_mentioned"
    if "diary" in trigger_name or trigger_name == "daily_journal":
        return "diary"
    if "episodic" in trigger_name or trigger_name == "spontaneous_recall":
        return "episodic"
    if trigger_name.startswith("garden"):
        return "mood_match"
    return "random"


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
