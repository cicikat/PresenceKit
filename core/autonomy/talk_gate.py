from __future__ import annotations

from core.autonomy.models import Disposition


def schema() -> dict:
    return {"type": "function", "function": {"name": "talk_owner", "description": "Only use for one deliberate concise user-visible proactive message. The text must be grounded in the opportunity, current system facts, recent conversation, or a system-executed memory result. Candidate evidence is not dialogue; without a reliable memory anchor do not claim to remember, quote, or refer to what the owner said before. Tool completion alone is not a reason to speak.", "parameters": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 600}, "reason": {"type": "string", "maxLength": 120, "description": "Short reason naming the grounded fact strength: current observation, recent history, anchored memory, or deliberate no-memory choice."}}, "required": ["text", "reason"]}}}


def confirm_schema() -> dict:
    return {"type": "function", "function": {"name": "confirm_talk", "description": "One-time decision after a soft timing block. cancel ends the talk attempt; send_anyway sends once after hard-policy recheck.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["cancel", "send_anyway"]}, "revised_text": {"type": "string", "maxLength": 600}}, "required": ["action"]}}}


def check(uid: str, *, allow_soft: bool = True) -> tuple[str, str]:
    from core.scheduler.proactive_ledger import can_send, continuity_status
    continuity = continuity_status(uid)
    if continuity["consecutive_unanswered_talks"] >= 2:
        return "hard", Disposition.SUPPRESSED_UNANSWERED_CAP.value
    from core.character_loader import is_proactive_disabled
    if is_proactive_disabled(): return "hard", Disposition.SUPPRESSED_PROACTIVE_OFF.value
    from core.scheduler.triggers.dnd import is_dnd
    if is_dnd(uid): return "hard", Disposition.SUPPRESSED_DND.value
    from core.dream.dream_state import DreamGuardStatus, get_reality_guard_status
    try: guard = get_reality_guard_status(uid)
    except Exception: return "hard", Disposition.BLOCKED_DREAM_UNCERTAIN.value
    if guard == DreamGuardStatus.BLOCK_UNCERTAIN: return "hard", Disposition.BLOCKED_DREAM_UNCERTAIN.value
    if guard != DreamGuardStatus.ALLOW: return "hard", Disposition.BLOCKED_DREAM.value
    allowed, why = can_send("autonomy", priority="normal", uid=uid)
    if not allowed:
        if why == "daily_budget_exceeded": return "hard", Disposition.SUPPRESSED_DAILY_BUDGET.value
        return ("soft" if allow_soft else "hard"), why
    return "allow", "ok"


async def send(
    uid: str,
    char_id: str,
    text: str,
    *,
    source: str,
    run_id: str,
    correlation_id: str = "",
    bypass_soft_once: bool = False,
) -> tuple[bool, str]:
    text = str(text or "").strip()
    if not text or len(text) > 600: return False, "empty_text"
    # Reality output sanitation is intentionally applied before deciding that
    # this is a legal talk. A tool loop cannot turn markup/narration into a
    # visible proactive message merely by calling this capability.
    from core.response_processor import strip_render_tags
    from core.reality_output_scrubber import scrub_reality_output_text
    text = (scrub_reality_output_text(strip_render_tags(text)) or "").strip()
    if not text: return False, "empty_text"
    mode, reason = check(uid, allow_soft=True)
    if mode == "hard" or (mode == "soft" and not bypass_soft_once): return False, reason
    from core import pipeline_registry
    pipeline = pipeline_registry.get()
    if pipeline is None: return False, "pipeline_unavailable"
    from channels.registry import get_active
    if not get_active():
        return False, "no_delivery_channel"
    from core.autonomy.store import claim_delivery_correlation
    correlation_id = str(correlation_id or run_id)
    if not claim_delivery_correlation(uid, char_id, correlation_id):
        return False, Disposition.DUPLICATE.value
    from core.turn_sink import TurnSource, record_assistant_turn
    from core.write_envelope import stamp_trigger
    from core.event_context import EventContext
    event_context = EventContext.from_ingress(
        uid=uid,
        char_id=char_id,
        ingress_event_id=f"autonomy:{correlation_id}",
        dedupe_key=f"autonomy:{correlation_id}",
        source=source or "autonomy",
        channel="system",
        kind="trigger",
        actor="system",
    )
    try:
        from core.event_context_observer import record as observe_context
        observe_context(stage="ingress", disposition="accepted", context=event_context)
    except Exception:
        pass
    result = await record_assistant_turn(
        pipeline=pipeline, uid=uid, assistant_text=text, source=TurnSource.TRIGGER,
        trigger_name="autonomy", char_id=char_id, bypass_gate=False,
        audit_extras={
            "source": "autonomy",
            "trigger_source": source,
            "run_id": run_id,
            "correlation_id": correlation_id,
        },
        event_context=event_context,
        envelope=stamp_trigger(),
    )
    if not result.fanout_targets: return False, "no_delivery_channel"
    from core.scheduler.proactive_ledger import record_send
    record_send("autonomy", channel="autonomy", gist=text, uid=uid, char_id=char_id)
    return True, "sent"
