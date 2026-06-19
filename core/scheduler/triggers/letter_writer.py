"""Letter writer proposer: send rare, event-driven email while the owner is quiet."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
import logging
import re
import time

logger = logging.getLogger(__name__)

RECENT_EPISODIC_DAYS = 7
DREAM_TRIGGER_MAX_AGE_HOURS = 6
SIMILARITY_THRESHOLD = 0.7

_last_letter_text = ""


def propose(ctx: dict | None = None):
    ctx = ctx or {}
    from core.config_loader import get_config

    if not get_config().get("mail", {}).get("enabled", False):
        return None

    from core.scheduler.loop import _active_char_id_or_none, _is_ready, _owner_id

    if not _is_ready("letter_writer"):
        return None

    uid = str(ctx.get("uid") or _owner_id()).strip()
    char_id = str(ctx.get("char_id") or _active_char_id_or_none() or "").strip()
    if not uid or not char_id:
        return None

    now_ts = float(ctx.get("now_ts") or time.time())
    reason = _check_trigger_conditions(uid, char_id=char_id, now_ts=now_ts)
    if not reason:
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="letter_writer",
        urgency=urgency_in_tier(UrgencyTier.FILLER, 0.5),
        topic_source="letter_trigger",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        execute=_make_execute(uid, char_id, reason),
    )


def _check_trigger_conditions(uid: str, *, char_id: str, now_ts: float) -> str | None:
    """Return the first grounded reason that qualifies, otherwise None."""
    reason = _dream_reason(uid, char_id=char_id)
    if reason:
        return reason

    reason = _conversation_gap_reason(uid, char_id=char_id, now_ts=now_ts)
    if reason:
        return reason

    reason = _strong_episodic_reason(uid, char_id=char_id, now_ts=now_ts)
    if reason:
        return reason

    reason = _anniversary_eve_reason(datetime.fromtimestamp(now_ts).date())
    if reason:
        return reason

    return _hidden_state_reason(uid, char_id=char_id)


def _dream_reason(uid: str, *, char_id: str) -> str | None:
    try:
        from core.dream.dream_afterglow import _find_best_summary

        best, age_hours = _find_best_summary(uid, char_id=char_id)
        if (
            best
            and float(best.get("summary_weight") or 0) >= 0.8
            and age_hours < DREAM_TRIGGER_MAX_AGE_HOURS
        ):
            return f"最近那个很有分量的梦仍留在心里：{str(best.get('summary') or '')[:50]}"
    except Exception:
        pass
    return None


def _conversation_gap_reason(uid: str, *, char_id: str, now_ts: float) -> str | None:
    try:
        from core.memory import short_term

        timestamps = [
            float(item.get("timestamp") or 0)
            for item in short_term.load(uid, char_id=char_id)
            if isinstance(item, dict) and item.get("timestamp")
        ]
        if timestamps:
            gap_days = (now_ts - max(timestamps)) / 86400.0
            if gap_days >= 3:
                return f"已经有 {int(gap_days)} 天没有好好说话了"
    except Exception:
        pass
    return None


def _strong_episodic_reason(uid: str, *, char_id: str, now_ts: float) -> str | None:
    try:
        from core.memory.episodic_memory import _load_memories

        cutoff = now_ts - RECENT_EPISODIC_DAYS * 86400
        candidates = [
            item
            for item in _load_memories(uid, char_id=char_id)
            if float(item.get("strength") or 0) > 0.85
            and float(item.get("timestamp") or 0) >= cutoff
        ]
        if candidates:
            best = max(candidates, key=lambda item: float(item.get("strength") or 0))
            summary = best.get("narrative_summary") or best.get("summary") or ""
            return f"一直想把这件重要的事写下来：{str(summary)[:60]}"
    except Exception:
        pass
    return None


def _anniversary_eve_reason(today: date) -> str | None:
    try:
        from core.config_loader import get_config

        cfg = get_config()
        scheduler_cfg = cfg.get("scheduler", {})
        birthday = str(scheduler_cfg.get("owner_birthday") or "")
        if _is_day_before(today, birthday):
            return "明天是你的生日，想在日子到来前先写一封信"

        for item in cfg.get("anniversaries", []):
            if not isinstance(item, dict):
                continue
            try:
                month_day = f"{int(item.get('month')):02d}-{int(item.get('day')):02d}"
            except (TypeError, ValueError):
                continue
            if _is_day_before(today, month_day):
                key = str(item.get("key") or "一个重要纪念日")
                return f"明天是我们记得的日子：{key}"
    except Exception:
        pass
    return None


def _is_day_before(today: date, month_day: str) -> bool:
    try:
        month, day = (int(part) for part in month_day.split("-", 1))
        event = date(today.year, month, day)
        if event < today:
            event = date(today.year + 1, month, day)
        return event - today == timedelta(days=1)
    except Exception:
        return False


def _hidden_state_reason(uid: str, *, char_id: str) -> str | None:
    try:
        from core.memory.user_hidden_state_store import load_hidden_state

        state = load_hidden_state(uid, char_id=char_id)
        sensitivity_baseline = max(float(state.sensitivity.baseline.value), 1.0)
        touch_baseline = max(float(state.touch_need.baseline.value), 1.0)
        sensitivity_ratio = float(state.sensitivity.current.value) / sensitivity_baseline
        touch_ratio = float(state.touch_need.deficit.value) / touch_baseline
        if max(sensitivity_ratio, touch_ratio) > 1.5:
            return "有些感受比平时更满，想安静地写下来给你"
    except Exception:
        pass
    return None


def _make_execute(uid: str, char_id: str, reason: str):
    async def execute(*, dry_run: bool):
        return await _send_letter_if_worthy(uid, char_id, reason, dry_run=dry_run)

    return execute


async def _send_letter_if_worthy(
    uid: str,
    char_id: str,
    reason: str,
    *,
    dry_run: bool = False,
):
    """Generate, quality-check, deduplicate, and possibly send a letter."""
    global _last_letter_text

    from core.mail.letter_writer import QUALITY_THRESHOLD, evaluate_letter, generate_letter
    from core.scheduler.execution import write_execute_blocked, write_execute_dryrun

    letter = await generate_letter(uid, reason, char_id=char_id)
    if not letter:
        return _result(reason, dry_run=dry_run, sent=False)

    score = await evaluate_letter(letter)
    logger.info("[letter_writer] quality_score=%d threshold=%d", score, QUALITY_THRESHOLD)
    if score < QUALITY_THRESHOLD or _is_too_similar(letter, _last_letter_text):
        return _result(letter, dry_run=dry_run, sent=False)

    if dry_run:
        result = _result(letter, dry_run=True, sent=False)
        write_execute_dryrun(result)
        return result

    from core.mail.mail_sender import send_letter
    from core.scheduler.loop import _mark

    sent = await send_letter(_extract_subject(letter, reason), letter)
    if sent:
        _last_letter_text = letter
        _mark("letter_writer")
        try:
            from core.mail.letter_reference import append_sent_letter
            append_sent_letter(uid, char_id, letter)
        except Exception:
            pass
    result = _result(letter, dry_run=False, sent=sent)
    if not sent:
        write_execute_blocked(result)
    return result


def _result(text: str, *, dry_run: bool, sent: bool):
    from core.scheduler.execution import ExecuteResult

    return ExecuteResult(
        trigger_name="letter_writer",
        would_send_prompt=text,
        would_mark=["letter_writer"],
        dry_run=dry_run,
        sent=sent,
    )


def _is_too_similar(letter: str, previous: str) -> bool:
    if not letter or not previous:
        return False
    normalize = lambda text: re.sub(r"\s+", "", text)
    ratio = SequenceMatcher(None, normalize(letter), normalize(previous)).ratio()
    return ratio > SIMILARITY_THRESHOLD


def _extract_subject(letter: str, fallback_reason: str) -> str:
    first_line = letter.splitlines()[0].strip().lstrip("，。").strip()
    return first_line[:20] if len(first_line) >= 4 else fallback_reason[:20]


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("letter_writer", propose)


_register_proposers()
