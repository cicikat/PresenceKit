"""Letter writer proposer: send rare, event-driven email while the owner is quiet."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
import logging
import re
import time
import uuid

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

    from core.scheduler.loop import _active_char_id_or_none, _owner_id

    uid = str(ctx.get("uid") or _owner_id()).strip()
    char_id = str(ctx.get("char_id") or _active_char_id_or_none() or "").strip()
    if not uid or not char_id:
        return None

    now_ts = float(ctx.get("now_ts") or time.time())
    from core.mail import weekly_contract
    weekly_due = _in_weekly_window(now_ts) and weekly_contract.is_due(uid, char_id, now=now_ts)
    if not weekly_due:
        return None
    reason = _check_trigger_conditions(uid, char_id=char_id, now_ts=now_ts)
    if not reason and not weekly_due:
        return None
    reason = reason or "本周的信还没有寄出，想认真写给你"

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="letter_writer",
        urgency=urgency_in_tier(UrgencyTier.FILLER, 0.5),
        topic_source="letter_trigger",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        execute=_make_execute(uid, char_id, reason, weekly_due=weekly_due),
        weekly_delivery_due=weekly_due,
        char_id=char_id,
    )


def _in_weekly_window(now_ts: float) -> bool:
    from core.config_loader import get_config
    mail = get_config().get("mail", {})
    start = int(mail.get("weekly_window_start_day", 0))
    end = int(mail.get("weekly_window_end_day", 6))
    weekday = datetime.fromtimestamp(now_ts).weekday()
    return 0 <= start <= end <= 6 and start <= weekday <= end


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


def _make_execute(uid: str, char_id: str, reason: str, *, weekly_due: bool = False):
    async def execute(*, dry_run: bool):
        retry_count = 0
        if weekly_due and not dry_run:
            from core.mail import weekly_contract
            claimed = weekly_contract.claim(uid, char_id)
            if claimed is None:
                return _result(reason, dry_run=False, sent=False)
            retry_count = int(claimed.get("retry_count") or 0)
        return await _send_letter_if_worthy(
            uid, char_id, reason, dry_run=dry_run, execution_id=uuid.uuid4().hex,
            weekly_contract=weekly_due, retry_count=retry_count,
        )

    return execute


async def _send_letter_if_worthy(
    uid: str,
    char_id: str,
    reason: str,
    *,
    dry_run: bool = False,
    execution_id: str | None = None,
    weekly_contract: bool = False,
    retry_count: int = 0,
):
    """Generate, quality-check, deduplicate, and possibly send a letter."""
    global _last_letter_text

    execution_id = execution_id or uuid.uuid4().hex
    started = time.monotonic()
    from core.mail.execution_ledger import append as append_execution

    def record(stage: str, result: str, **metadata) -> None:
        append_execution(
            execution_id=execution_id, uid=uid, char_id=char_id, stage=stage, result=result,
            retry_count=retry_count, duration_ms=int((time.monotonic() - started) * 1000), **metadata,
        )

    def finish_weekly(*, sent: bool, failure_code: str = "", message_id: str = "") -> None:
        if not weekly_contract or dry_run:
            return
        from core.config_loader import get_config
        from core.mail import weekly_contract as contract
        cfg = get_config().get("mail", {})
        contract.finish(uid, char_id, sent=sent, failure_code=failure_code, message_id=message_id,
                        max_generation_attempts=int(cfg.get("weekly_max_generation_attempts", 3)),
                        smtp_max_retries=int(cfg.get("weekly_smtp_max_retries", 3)),
                        retry_base_seconds=int(cfg.get("weekly_smtp_retry_base_seconds", 900)))

    record("selected", "accepted")

    from core.mail.letter_writer import QUALITY_THRESHOLD, evaluate_letter, generate_letter
    from core.scheduler.execution import write_execute_blocked, write_execute_dryrun

    try:
        letter = await generate_letter(uid, reason, char_id=char_id)
    except Exception as exc:
        record("generation_failed", "failed", failure_code="generation_error", exception_type=type(exc).__name__)
        finish_weekly(sent=False, failure_code="generation_error")
        if not dry_run and not weekly_contract:
            from core.scheduler.loop import _record_attempt_failure
            _record_attempt_failure("letter_writer", char_id=char_id)
        return _result(reason, dry_run=dry_run, sent=False)
    if not letter:
        record("generation_failed", "failed", failure_code="empty_content")
        finish_weekly(sent=False, failure_code="empty_content")
        if not dry_run and not weekly_contract:
            from core.scheduler.loop import _record_attempt_failure
            _record_attempt_failure("letter_writer", char_id=char_id)
        return _result(reason, dry_run=dry_run, sent=False)

    record("generated", "ok")
    try:
        score = await evaluate_letter(letter)
    except Exception as exc:
        record("quality_rejected", "failed", failure_code="generation_error", exception_type=type(exc).__name__)
        score = 0
    logger.info("[letter_writer] quality_score=%d threshold=%d", score, QUALITY_THRESHOLD)
    if score < QUALITY_THRESHOLD or _is_too_similar(letter, _last_letter_text):
        record("quality_rejected", "rejected", failure_code="quality_rejected")
        finish_weekly(sent=False, failure_code="quality_rejected")
        # A4: 质量门/相似度拒发不再让下个 tick 立即重跑一遍完整生成+评分流程（RC4）。
        if not dry_run and not weekly_contract:
            from core.scheduler.loop import _record_attempt_failure
            _record_attempt_failure("letter_writer", char_id=char_id)
        return _result(letter, dry_run=dry_run, sent=False)

    if dry_run:
        record("quality_passed", "dry_run")
        result = _result(letter, dry_run=True, sent=False)
        write_execute_dryrun(result)
        return result

    from core.mail.mail_sender import send_letter_detailed
    from core.scheduler.loop import _mark, _record_attempt_failure, _clear_attempt_backoff

    record("quality_passed", "ok")
    record("send_attempted", "started")
    send_result = await send_letter_detailed(_extract_subject(letter, reason), letter)
    if send_result.sent:
        record("sent", "ok", smtp_status_code=send_result.smtp_status_code)
        finish_weekly(sent=True, message_id=send_result.message_id)
        _last_letter_text = letter
        _mark("letter_writer", char_id=char_id)
        _clear_attempt_backoff("letter_writer", char_id=char_id)
        try:
            from core.mail.letter_reference import append_sent_letter
            append_sent_letter(uid, char_id, letter)
        except Exception:
            pass
    else:
        record("smtp_failed", "failed", failure_code=send_result.failure_code,
               exception_type=send_result.exception_type, smtp_status_code=send_result.smtp_status_code)
        finish_weekly(sent=False, failure_code=send_result.failure_code)
        if not weekly_contract:
            _record_attempt_failure("letter_writer", char_id=char_id)
    result = _result(letter, dry_run=False, sent=send_result.sent)
    if not send_result.sent:
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
