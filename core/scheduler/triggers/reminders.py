"""Shadow proposal support for due reminders."""

from __future__ import annotations

from datetime import datetime


def propose(ctx: dict | None = None):
    ctx = ctx or {}
    from core.scheduler.loop import _owner_id

    uid = _owner_id()
    if not uid:
        return None
    now = ctx.get("now_dt") or datetime.now()
    due = ctx.get("due_reminders")
    if due is None:
        from core.tools.reminder import get_due_reminders

        due = get_due_reminders(uid)
    if not due:
        return None

    most_overdue = 0.0
    picked_item = None
    for item in due:
        try:
            remind_at = datetime.strptime(item["remind_at"], "%Y-%m-%d %H:%M")
        except Exception:
            continue
        overdue = (now - remind_at).total_seconds()
        if picked_item is None or overdue > most_overdue:
            most_overdue = overdue
            picked_item = item
    if most_overdue < 0:
        return None
    if picked_item is None:
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    ratio = min(1.0, most_overdue / 3600)
    return TriggerProposal(
        trigger_name="reminders",
        urgency=urgency_in_tier(UrgencyTier.WINDOW_EVENT, ratio),
        topic_source="random",
        requires_state=[TriggerState.CHATTING, TriggerState.QUIET, TriggerState.RESTLESS],
        bypass_state_machine=True,
        execute=None,
        metadata={
            "observation_only": True,
            "schedule_id": picked_item.get("schedule_id") or picked_item.get("id"),
        },
    )


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("reminders", propose)


_register_proposers()
