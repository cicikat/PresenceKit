"""
调度器 gating 决策层。

发言 proposal 的统一决策层：状态、active-window、DND、defer、冷却与 winner。
"""

from __future__ import annotations

import time
import inspect
from dataclasses import dataclass, replace
from typing import Optional

from core.safe_write import rotate_jsonl_if_needed, safe_append_jsonl
from core.sandbox import get_paths
from core.scheduler.execution import ExecuteFn, is_live_mode
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
    "overflow",
    "presence_nag",
    "dream_exit",
    "letter_writer",
    "coplay_commentary",
})


@dataclass(frozen=True)
class TriggerProposal:
    trigger_name: str
    urgency: float
    topic_source: str
    requires_state: list
    bypass_state_machine: bool = False
    execute: Optional[ExecuteFn] = None
    char_id: str | None = None


def _shadow_cfg() -> dict:
    from core.config_loader import get_config
    return get_config().get("scheduler", {}).get("gating_shadow", {})


def is_trigger_ready(trigger_name: str, *, char_id: str | None = None) -> bool:
    from core.scheduler.loop import _is_ready

    return _is_ready(trigger_name, char_id=char_id)


def _proposal_cooldown_ready(proposal: TriggerProposal) -> bool:
    if proposal.char_id is None:
        return is_trigger_ready(proposal.trigger_name)
    if "char_id" in inspect.signature(is_trigger_ready).parameters:
        return is_trigger_ready(proposal.trigger_name, char_id=proposal.char_id)
    return is_trigger_ready(proposal.trigger_name)


def collect_and_decide(uid: str, proposals: list[TriggerProposal]) -> Optional[TriggerProposal]:
    picked, _, _ = _decide(uid, proposals)
    return picked


def write_shadow_tick(uid: str) -> Optional[TriggerProposal]:
    cfg = _shadow_cfg()
    if not cfg.get("enabled", True):
        return None
    ctx = _build_context(uid)
    proposals = _collect_native_proposals(ctx)
    picked, reason, candidates = _decide(uid, proposals)
    state = get_current_state(uid)
    log_path = get_paths().gating_shadow_log()
    safe_append_jsonl(
        log_path,
        {
            "ts": time.time(),
            "uid": uid,
            "state": _state_value(state),
            "candidates": candidates,
            "would_pick": picked.trigger_name if picked else None,
            "reason": reason,
        },
    )
    max_bytes = int(cfg.get("max_size_mb", 5) * 1024 * 1024)
    keep_n = int(cfg.get("keep", 3))
    rotate_jsonl_if_needed(log_path, max_bytes=max_bytes, keep_n=keep_n)
    return picked


async def run_shadow_tick(uid: str) -> Optional[TriggerProposal]:
    picked = write_shadow_tick(uid)
    if picked is not None and picked.execute is not None:
        await picked.execute(dry_run=not is_live_mode())
    return picked


async def decide_and_execute_event(
    uid: str,
    proposals: list[TriggerProposal],
    *,
    dry_run: bool,
) -> tuple[Optional[TriggerProposal], str, object | None]:
    """Run an event-driven proposal through the same policy decision as tick proposals."""
    picked, reason, _ = _decide(uid, proposals)
    if picked is None or picked.execute is None:
        return picked, reason, None
    result = await picked.execute(dry_run=dry_run)
    return picked, reason, result


def _build_context(uid: str) -> dict:
    from core.scheduler.loop import _active_char_id_or_none

    return {
        "uid": uid,
        "now_ts": time.time(),
        "char_id": _active_char_id_or_none(),
    }


def _collect_native_proposals(ctx: dict) -> list[TriggerProposal]:
    from core.scheduler.proposer_registry import iter_proposers

    proposals: list[TriggerProposal] = []
    for entry in iter_proposers():
        item = entry.fn(ctx)
        if item is not None:
            if item.char_id is None and ctx.get("char_id"):
                item = replace(item, char_id=str(ctx["char_id"]))
            proposals.append(item)
    return proposals


def _decide(uid: str, proposals: list[TriggerProposal]) -> tuple[Optional[TriggerProposal], str, list[dict]]:
    # Deferred imports to avoid circular dependency (gating ↔ loop / dnd).
    from core.scheduler.loop import _user_active_recently
    from core.scheduler.triggers.dnd import is_dnd

    state = get_current_state(uid)
    user_active = _user_active_recently()
    dnd_active = is_dnd(uid)

    # ── proactive=off 闸门（Brief 29 · 3.3）：活跃角色卡关闭主动发言时，拒绝全部
    # 发言类 proposal。维护型扫描（episodic_decay/inner_diary_write/garden 浇水等）
    # 不经过 gating._decide，不受影响。
    from core.character_loader import is_proactive_disabled
    if proposals and is_proactive_disabled():
        candidates = [
            _serialize_candidate(p, state, uid=uid, user_active=user_active, dnd_active=dnd_active)
            for p in proposals
        ]
        return None, "proactive_off", candidates

    # ── Defer queue: handle expired items before building candidates ─────────
    # scan_expired() removes stale entries and returns names that should be
    # force-sent (bypassing active_window) or dropped (already cleaned up).
    from core.scheduler.defer_queue import enqueue_defer, release_defer, scan_expired
    force_send_names, _dropped_names = scan_expired(uid)

    candidates = [
        _serialize_candidate(
            p, state,
            uid=uid,
            user_active=user_active,
            dnd_active=dnd_active,
            force_send_names=force_send_names,
        )
        for p in proposals
    ]
    if not proposals:
        return None, "no_candidates", candidates

    state_allowed = [
        p for p in proposals
        if p.bypass_state_machine or _state_value(state) in {_state_value(s) for s in p.requires_state}
    ]
    if not state_allowed:
        return None, "state_filtered", candidates

    # ── Active-window filter (R2-B / R2-D) ───────────────────────────────────
    # Consult POLICY_TABLE.active_window_behavior before picking a winner.
    # exempt       → always allow
    # defer        → skip this tick when user active; enqueue in defer_queue for
    #                age tracking.  When max_defer_age_secs expires with
    #                on_defer_expire="force_send", the trigger is added to
    #                force_send_names and bypasses active_window on that tick.
    # drop         → skip this tick when user active
    # unknown      → defer by default (conservative)
    if user_active:
        aw_allowed = [
            p for p in state_allowed
            if _policy_active_window_behavior(p.trigger_name) == "exempt"
            or p.trigger_name in force_send_names
        ]
        if not aw_allowed:
            # Enqueue defer-behavior proposals for age tracking.
            # drop-behavior proposals are NOT enqueued (they're intentionally
            # discarded; only defer triggers need expiry semantics).
            for p in state_allowed:
                if _policy_active_window_behavior(p.trigger_name) == "defer":
                    enqueue_defer(uid, p.trigger_name)
            return None, "active_window_filtered", candidates
        state_allowed = aw_allowed

    # ── DND filter (R2-B) ────────────────────────────────────────────────────
    # When the owner has set DND, only emergency-priority triggers pass.
    if dnd_active:
        dnd_allowed = [p for p in state_allowed if _policy_is_emergency(p.trigger_name)]
        if not dnd_allowed:
            return None, "dnd_filtered", candidates
        state_allowed = dnd_allowed

    cooldown_allowed = [p for p in state_allowed if _proposal_cooldown_ready(p)]
    if not cooldown_allowed:
        return None, "cooldown_filtered", candidates

    # ── ProactiveLedger filter (B) ────────────────────────────────────────────
    # Single source of truth for "can this trigger speak right now": global
    # cross-trigger gap (A2 next_allowed_ts, one-time jitter sample) + daily
    # send budget (scheduler.max_daily_proactive). gating stays the decision
    # authority; the ledger is only queried here, never picks a winner itself.
    from core.scheduler.proactive_ledger import can_send as _ledger_can_send
    ledger_allowed = []
    ledger_reasons: set[str] = set()
    for p in cooldown_allowed:
        priority = "emergency" if _policy_is_emergency(p.trigger_name) else "normal"
        allowed, reason = _ledger_can_send(p.trigger_name, priority=priority)
        if allowed:
            ledger_allowed.append(p)
        else:
            ledger_reasons.add(reason)
    if not ledger_allowed:
        # Preserve the pre-existing "global_gap_filtered" reason string for the
        # gap case (observability/verification tooling greps for it); budget
        # exhaustion gets its own distinguishable reason.
        if ledger_reasons == {"daily_budget_exceeded"}:
            return None, "daily_budget_filtered", candidates
        return None, "global_gap_filtered", candidates
    cooldown_allowed = ledger_allowed

    picked = max(cooldown_allowed, key=lambda p: p.urgency)
    # Release from defer queue: trigger was sent (or will be sent this tick).
    release_defer(uid, picked.trigger_name)
    return picked, "picked_highest_urgency", candidates


def _policy_active_window_behavior(trigger_name: str) -> str:
    """Return active_window_behavior from POLICY_TABLE, defaulting to 'defer' for unknowns."""
    from core.scheduler.policy import POLICY_TABLE
    policy = POLICY_TABLE.get(trigger_name)
    return policy.active_window_behavior if policy else "defer"


def _policy_is_emergency(trigger_name: str) -> bool:
    """Return True iff POLICY_TABLE marks trigger as emergency priority."""
    from core.scheduler.policy import POLICY_TABLE
    policy = POLICY_TABLE.get(trigger_name)
    return policy is not None and policy.priority == "emergency"


def _serialize_candidate(
    proposal: TriggerProposal,
    state: TriggerState | str,
    *,
    uid: str = "",
    user_active: bool = False,
    dnd_active: bool = False,
    force_send_names: frozenset[str] | None = None,
) -> dict:
    required = [_state_value(s) for s in proposal.requires_state]
    state_allowed = proposal.bypass_state_machine or _state_value(state) in set(required)
    cooldown_ready = _proposal_cooldown_ready(proposal)
    aw_behavior = _policy_active_window_behavior(proposal.trigger_name)
    _force_send = proposal.trigger_name in (force_send_names or frozenset())
    aw_blocked = user_active and aw_behavior != "exempt" and not _force_send
    dnd_blocked = dnd_active and not _policy_is_emergency(proposal.trigger_name)
    # Defer queue observability: include current deferred age if tracked.
    deferred_age_secs = None
    if uid:
        try:
            from core.scheduler.defer_queue import get_queue_snapshot as _dq_snap
            snap = {e["trigger_name"]: e for e in _dq_snap(uid)}
            entry = snap.get(proposal.trigger_name)
            if entry:
                deferred_age_secs = round(entry["age_secs"], 1)
        except Exception:
            pass
    return {
        "trigger_name": proposal.trigger_name,
        "char_id": proposal.char_id,
        "urgency": proposal.urgency,
        "topic_source": proposal.topic_source,
        "requires_state": required,
        "bypass_state_machine": proposal.bypass_state_machine,
        "state_allowed": state_allowed,
        "cooldown_ready": cooldown_ready,
        "aw_behavior": aw_behavior,
        "aw_blocked": aw_blocked,
        "dnd_blocked": dnd_blocked,
        "force_send": _force_send,
        "deferred_age_secs": deferred_age_secs,
    }


def _state_value(state: TriggerState | str) -> str:
    if isinstance(state, TriggerState):
        return state.value
    return str(state)
