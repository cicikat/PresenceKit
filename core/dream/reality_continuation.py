"""Durable, gate-aware Reality continuation after an explicit Dream close.

This is deliberately outside the scheduler proposer/winner path.  A
continuation is an owner-visible consequence of one accepted Dream exit, not a
new autonomous opportunity, so it uses the normal Reality pipeline while
avoiding scheduler quiet windows, budgets, and winner arbitration.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_CONTINUATION_PROMPT = (
    "刚从一场梦回到现实。请在现实侧自然、简短地回应；不要提及、复述或推断梦中的具体内容。"
)
_tasks: dict[tuple[str, str], asyncio.Task] = {}


def _lifecycle_record(
    uid: str,
    dream_id: str,
    *,
    char_id: str,
    lifecycle: str,
    reason_code: str = "",
    last_error: str = "",
    owner_turn_seq: int | None = None,
) -> dict[str, Any]:
    from core.dream.exit_observability import DELIVERY_CONTINUATION, record

    return record(
        uid,
        dream_id,
        char_id=char_id,
        delivery_kind=DELIVERY_CONTINUATION,
        lifecycle=lifecycle,
        reason_code=reason_code,
        last_error=last_error,
        owner_turn_seq=owner_turn_seq,
    )


def _owner_turn_seq(uid: str) -> int | None:
    """Return the monotonic owner-turn version, or None for legacy ledgers."""
    try:
        from core.scheduler.proactive_ledger import continuity_status

        value = continuity_status(uid).get("user_turn_seq")
        return None if value is None else max(0, int(value))
    except Exception:
        return None


def enqueue(uid: str, dream_id: str, *, char_id: str) -> bool:
    """Persist and schedule one continuation for a newly closed Dream.

    The caller must invoke this only after the Dream reply has become visible.
    The durable lifecycle row is the restart/reconnect deduplication marker;
    the in-process map only prevents concurrent workers in this process.
    """
    uid = str(uid or "").strip()
    dream_id = str(dream_id or "").strip()
    char_id = str(char_id or "").strip()
    if not uid or not dream_id or not char_id:
        return False

    from core.dream.exit_observability import (
        CONTINUATION_CANCELLED,
        CONTINUATION_SENT,
        DELIVERY_CONTINUATION,
        get_record,
    )

    existing = get_record(dream_id, char_id=char_id, delivery_kind=DELIVERY_CONTINUATION)
    if existing and existing.get("lifecycle") in {CONTINUATION_SENT, CONTINUATION_CANCELLED}:
        return False
    owner_turn_seq = _owner_turn_seq(uid)
    # The Dream close timestamp is the legacy race baseline. A user may have already
    # started a Reality turn between the close and the visible pseudo-stream
    # completion, before this enqueue call gets a chance to create its row.
    try:
        from core.dream.dream_state import read_state
        from core.scheduler.proactive_ledger import continuity_status

        close_at = float((read_state(uid).get("last_exited_at") or 0.0))
        last_user_message_at = float(continuity_status(uid).get("last_user_message_at") or 0.0)
        if close_at and last_user_message_at > close_at + 0.001:
            _lifecycle_record(
                uid,
                dream_id,
                char_id=char_id,
                lifecycle=CONTINUATION_CANCELLED,
                reason_code="new_user_turn",
                owner_turn_seq=owner_turn_seq,
            )
            return False
    except Exception:
        pass
    key = (uid, dream_id)
    current = _tasks.get(key)
    if current and not current.done():
        return False

    _lifecycle_record(
        uid,
        dream_id,
        char_id=char_id,
        lifecycle="pending",
        reason_code="continuation_queued",
        owner_turn_seq=owner_turn_seq,
    )
    try:
        task = asyncio.create_task(_run_once(uid, dream_id, char_id=char_id))
    except RuntimeError:
        logger.warning("[dream_continuation] no running loop uid=%s dream_id=%s", uid, dream_id)
        from core.dream.exit_observability import CONTINUATION_FAILED

        _lifecycle_record(
            uid,
            dream_id,
            char_id=char_id,
            lifecycle=CONTINUATION_FAILED,
            reason_code="pipeline_unavailable",
            last_error="no_running_loop",
        )
        return False
    _tasks[key] = task
    task.add_done_callback(lambda _task, _key=key: _tasks.pop(_key, None))
    return True


def _new_user_turn(uid: str, created_at: float, owner_turn_seq: int | None = None) -> bool:
    try:
        from core.scheduler.proactive_ledger import continuity_status

        continuity = continuity_status(uid)
        if owner_turn_seq is not None:
            current_turn_seq = _owner_turn_seq(uid)
            if current_turn_seq is not None:
                return current_turn_seq > owner_turn_seq
        last_user_message_at = float(continuity.get("last_user_message_at") or 0.0)
        return last_user_message_at > float(created_at) + 0.001
    except Exception:
        return False


async def _run_once(uid: str, dream_id: str, *, char_id: str) -> None:
    from core.dream.dream_state import DreamStatus, read_state, write_state
    from core.dream.exit_observability import (
        CONTINUATION_CANCELLED,
        CONTINUATION_FAILED,
        CONTINUATION_SENT,
        DELIVERY_CONTINUATION,
        get_record,
    )

    row = get_record(dream_id, char_id=char_id, delivery_kind=DELIVERY_CONTINUATION)
    if not row:
        return
    created_at = float(row.get("created_at") or time.time())

    if _new_user_turn(uid, created_at, row.get("owner_turn_seq")):
        _lifecycle_record(
            uid,
            dream_id,
            char_id=char_id,
            lifecycle=CONTINUATION_CANCELLED,
            reason_code="new_user_turn",
        )
        return

    state = read_state(uid)
    if state.get("last_greeted_dream_id") == dream_id:
        _lifecycle_record(
            uid,
            dream_id,
            char_id=char_id,
            lifecycle=CONTINUATION_CANCELLED,
            reason_code="already_greeted",
        )
        return
    if (
        state.get("status") not in {
            DreamStatus.REALITY_AFTERGLOW.value,
            DreamStatus.REALITY_CHAT.value,
        }
        or str(state.get("last_dream_id") or "") != dream_id
        or str(state.get("char_id") or "") != char_id
    ):
        _lifecycle_record(
            uid,
            dream_id,
            char_id=char_id,
            lifecycle=CONTINUATION_FAILED,
            reason_code="close_not_eligible",
        )
        return

    from core.conversation_gate import conversation_lock

    try:
        async with conversation_lock(uid):
            # Re-read all owner state after waiting for the gate. A real user
            # turn may have arrived while the continuation was queued.
            row = get_record(dream_id, char_id=char_id, delivery_kind=DELIVERY_CONTINUATION) or row
            if _new_user_turn(
                uid,
                float(row.get("created_at") or created_at),
                row.get("owner_turn_seq"),
            ):
                _lifecycle_record(
                    uid,
                    dream_id,
                    char_id=char_id,
                    lifecycle=CONTINUATION_CANCELLED,
                    reason_code="new_user_turn",
                )
                return

            state = read_state(uid)
            if state.get("last_greeted_dream_id") == dream_id:
                _lifecycle_record(
                    uid,
                    dream_id,
                    char_id=char_id,
                    lifecycle=CONTINUATION_CANCELLED,
                    reason_code="already_greeted",
                )
                return
            if (
                state.get("status") not in {
                    DreamStatus.REALITY_AFTERGLOW.value,
                    DreamStatus.REALITY_CHAT.value,
                }
                or str(state.get("last_dream_id") or "") != dream_id
                or str(state.get("char_id") or "") != char_id
            ):
                _lifecycle_record(
                    uid,
                    dream_id,
                    char_id=char_id,
                    lifecycle=CONTINUATION_FAILED,
                    reason_code="close_not_eligible",
                )
                return

            from core.memory.scope import MemoryScope
            from core.pipeline_registry import get as get_pipeline

            pipeline = get_pipeline()
            if pipeline is None:
                _lifecycle_record(
                    uid,
                    dream_id,
                    char_id=char_id,
                    lifecycle=CONTINUATION_FAILED,
                    reason_code="pipeline_unavailable",
                )
                return
            scope = MemoryScope.reality_scope(uid, char_id)
            context = await pipeline.fetch_context(
                uid,
                _CONTINUATION_PROMPT,
                frozen_scope=scope,
                recall_policy="none",
            )
            messages, _ = pipeline.build_prompt(
                uid,
                _CONTINUATION_PROMPT,
                context,
                char_id=char_id,
            )
            reply = await pipeline.run_llm(messages, is_proactive=True, char_id=char_id)
            if not str(reply or "").strip():
                _lifecycle_record(
                    uid,
                    dream_id,
                    char_id=char_id,
                    lifecycle=CONTINUATION_FAILED,
                    reason_code="llm_empty",
                )
                return

            from core.turn_sink import TurnSource, record_assistant_turn
            from core.write_envelope import stamp_trigger
            from core.event_context import EventContext

            event_context = EventContext.from_ingress(
                uid=uid,
                char_id=char_id,
                ingress_event_id=f"dream-exit:{dream_id}",
                dedupe_key=f"dream-exit:{dream_id}",
                source="dream_exit_continuation",
                channel="system",
                kind="trigger",
                actor="system",
            )
            try:
                from core.event_context_observer import record as observe_context
                observe_context(stage="ingress", disposition="accepted", context=event_context)
            except Exception:
                pass

            turn_result = await record_assistant_turn(
                assistant_text=str(reply),
                uid=uid,
                source=TurnSource.TRIGGER,
                trigger_name="dream_exit_continuation",
                fanout="all",
                bypass_gate=True,
                pipeline=pipeline,
                envelope=stamp_trigger(),
                audit_extras={
                    "delivery_kind": DELIVERY_CONTINUATION,
                    "dream_id": dream_id,
                },
                frozen_scope=scope,
                char_id=char_id,
                event_context=event_context,
            )
            if turn_result is None:
                raise RuntimeError("turn_sink_returned_none")

            # The marker is written only after the normal Reality send path has
            # returned. This is the durable exactly-once boundary for recovery.
            state = read_state(uid)
            if (
                state.get("last_dream_id") == dream_id
                and state.get("status") in {
                    DreamStatus.REALITY_AFTERGLOW.value,
                    DreamStatus.REALITY_CHAT.value,
                }
            ):
                state["last_greeted_dream_id"] = dream_id
                write_state(uid, state)
            _lifecycle_record(
                uid,
                dream_id,
                char_id=char_id,
                lifecycle=CONTINUATION_SENT,
                reason_code="",
            )
    except Exception as exc:
        logger.warning(
            "[dream_continuation] failed uid=%s dream_id=%s: %s",
            uid,
            dream_id,
            exc,
        )
        _lifecycle_record(
            uid,
            dream_id,
            char_id=char_id,
            lifecycle=CONTINUATION_FAILED,
            reason_code="send_failed",
            last_error=str(exc),
        )


async def recover_pending(*, limit: int = 200) -> int:
    """Schedule unsent durable continuations after process startup."""
    from core.dream.exit_observability import (
        CONTINUATION_FAILED,
        CONTINUATION_PENDING,
        DELIVERY_CONTINUATION,
        list_records,
    )

    scheduled = 0
    rows = list_records(delivery_kind=DELIVERY_CONTINUATION, limit=limit)
    for row in rows:
        if row.get("lifecycle") not in {CONTINUATION_PENDING, CONTINUATION_FAILED}:
            continue
        if enqueue(
            str(row.get("uid") or ""),
            str(row.get("dream_id") or ""),
            char_id=str(row.get("char_id") or ""),
        ):
            scheduled += 1
    return scheduled


def start_recovery_task() -> asyncio.Task | None:
    try:
        return asyncio.create_task(recover_pending())
    except RuntimeError:
        logger.warning("[dream_continuation] startup recovery has no running loop")
        return None
