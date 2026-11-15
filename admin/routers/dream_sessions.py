"""Dream sessions API responsibilities; legacy names re-exported by dream.py."""

import logging
import json
import re
import re as _re
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from admin.auth import require_scopes
from admin.routers._common import requested_char_id, resolve_requested_char_id
from core.config_loader import get_config
from core.sandbox import get_paths

router = APIRouter()
logger = logging.getLogger("admin.routers.dream")

def _requested_dream_id(body: dict | None) -> str | None:
    """Read the optional session identity used by WAKE/resume transitions."""
    from admin.routers import dream as _runtime
    if body in (None, {}):
        return None
    if not isinstance(body, dict):
        raise _runtime.HTTPException(status_code=422, detail="dream request body invalid")
    raw = body.get("dream_id")
    if raw is None:
        return None
    return _runtime._validated_dream_id(str(raw))


def _set_wake_observation(
    state: dict,
    *,
    dream_id: str,
    result: str,
    confirmation: str,
    retention_offered: bool,
) -> dict:
    """Keep bounded, text-free WAKE choice metadata in the durable state."""
    from admin.routers import dream as _runtime
    state["last_wake_observation"] = {
        "dream_id": dream_id,
        "result": result,
        "confirmation": confirmation,
        "retention_offered": bool(retention_offered),
        "ts": _runtime.time.time(),
    }
    return state


@router.post("/dream/enter", summary="进入梦境")
async def dream_enter(body: dict = {}, _auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    uid = _runtime._owner_uid()
    entry_reason = (body.get("entry_reason") or "").strip()
    dream_mode = (body.get("dream_mode") or "sandbox").strip()
    script_id = (body.get("script_id") or "").strip() or None

    if dream_mode not in _runtime._VALID_DREAM_MODE:
        raise _runtime.HTTPException(
            status_code=422,
            detail=f"dream_mode={dream_mode!r} 非法，有效值：{sorted(_runtime._VALID_DREAM_MODE)}",
        )

    from core.pipeline_registry import get as _get_pipeline
    from core.dream.dream_pipeline import enter_dream

    pl = _get_pipeline()
    if pl is None:
        raise _runtime.HTTPException(status_code=503, detail="pipeline not initialized")
    char_id = _runtime.resolve_requested_char_id(
        body.get("char_id"), fallback=_runtime._active_dream_char_id,
    )

    result = await enter_dream(
        uid, entry_reason=entry_reason, char_id=char_id,
        dream_mode=dream_mode, script_id=script_id,
    )
    if not result.get("ok"):
        raise _runtime.HTTPException(status_code=409, detail=result.get("error", "cannot enter dream"))
    return result


@router.post("/dream/chat", summary="梦境对话（独立 pipeline）")
async def dream_chat(body: dict, _auth=Depends(require_scopes("activity"))):
    """
    Dream turn endpoint — routes to dream_pipeline, never to reality pipeline.

    conversation_lock(uid) serializes the full turn.
    Does NOT call notify_owner_turn, scheduler, or gating.
    """
    from admin.routers import dream as _runtime
    message = (body.get("message") or "").strip()
    if not message:
        raise _runtime.HTTPException(status_code=422, detail="message 不能为空")

    uid = _runtime._owner_uid()

    from core.dream.dream_state import DreamStatus, read_state_checked
    current_state, state_ok = read_state_checked(uid)
    if (
        state_ok
        and current_state.get("status") == DreamStatus.DREAM_ACTIVE.value
        and current_state.get("dream_mode") == "rpg"
    ):
        raise _runtime.HTTPException(
            status_code=409,
            detail={
                "code": "RPG_ENDPOINT_REQUIRED",
                "message": "RPG Dream input must use the RPG round endpoint.",
                "retryable": False,
            },
        )

    from core.conversation_gate import conversation_lock
    from core.dream.dream_pipeline import dream_turn

    async with conversation_lock(uid):
        result = await dream_turn(uid, message)

    scenario_reconcile_request = result.pop("scenario_reconcile_request", None)

    if err := result.get("error"):
        raise _runtime.HTTPException(status_code=409, detail=err)

    # Brief 84: pseudo-stream typewriter replay for the dream reply. Generation
    # already finished above (dream_turn is fully isolated, zero WS side effects
    # by construction); the animation itself doesn't need conversation_lock.
    # fail-open: pseudo_stream_push never raises, msg_id lets the client dedup
    # against this HTTP response the same way owner chat's stream path does.
    reply = result.get("reply") or ""
    _char_id = ""
    if reply:
        import uuid as _uuid

        from channels import ui_push as _ui_push
        from core.pipeline_registry import get as _get_pipeline

        _msg_id = _uuid.uuid4().hex
        _pl = _get_pipeline()
        _char_id = getattr(_pl, "_active_character_id", None) or ""
        try:
            await _ui_push.pseudo_stream_push(
                reply, msg_id=_msg_id, char_id=_char_id, profile="dream",
            )
        except Exception:
            _runtime.logger.debug("[dream_chat] pseudo_stream_push failed", exc_info=True)
        result["msg_id"] = _msg_id

    if scenario_reconcile_request:
        try:
            from core.dream.scenario_reconciler import schedule as _schedule_scenario_reconcile

            _schedule_scenario_reconcile(scenario_reconcile_request)
        except Exception:
            _runtime.logger.debug("[dream_chat] scenario reconciler schedule failed", exc_info=True)

    # Brief 170: enqueue Reality continuation only after the final Dream reply
    # has been handed to the visible channel.  The worker itself is durable,
    # gate-aware, and never runs Dream content through the Reality prompt.
    if reply and result.get("continuation_eligible") and result.get("dream_id"):
        try:
            from core.dream.reality_continuation import enqueue as _enqueue_continuation

            _enqueue_continuation(
                uid,
                str(result["dream_id"]),
                char_id=_char_id,
            )
        except Exception:
            _runtime.logger.warning("[dream_chat] Reality continuation enqueue failed", exc_info=True)

    return result


@router.post("/dream/exit", summary="强退梦境（硬出口，不可被拒）")
async def dream_exit(_auth=Depends(require_scopes("activity"))):
    """
    Hard exit — unconditional, immediate, penetrates all state.
    Cannot be disabled by config or role behavior (invariant D).
    """
    from admin.routers import dream as _runtime
    uid = _runtime._owner_uid()

    from core.dream.dream_pipeline import force_exit_dream
    return await force_exit_dream(uid)


@router.post("/dream/wake", summary="软挽留闸门（满足门控时角色挽留一次；否则直接硬退）")
async def dream_wake(body: dict = Body(default={}), _auth=Depends(require_scopes("activity"))):
    """
    Soft retention gate called when user taps the WAKE button.

    - If status != DREAM_ACTIVE, OR retention already offered this dream, OR gate
      threshold not met → falls through to force_exit_dream immediately.
    - If gate passes → sets status=DREAM_EXIT_REQUESTED, generates one retention
      sentence, returns {"retained": True, "retention_text": "...", "dream_id": "..."}.
    - LLM failure in _generate_retention_line → falls back to immediate hard exit
      (fail-open: user is never blocked from leaving).

    Invariant D preserved: /dream/exit is untouched and always succeeds.
    """
    from admin.routers import dream as _runtime
    uid = _runtime._owner_uid()

    from core.dream.dream_state import DreamStatus, read_state_checked, write_state
    from core.dream.dream_pipeline import (
        _should_retain, _generate_retention_line, force_exit_dream,
    )

    state, state_ok = read_state_checked(uid)
    if not state_ok:
        raise _runtime.HTTPException(status_code=503, detail="dream_state_unavailable")
    status = state.get("status")
    if state.get("dream_mode") == "rpg":
        close_result = await force_exit_dream(
            uid,
            exit_mechanism="user_hard_exit",
            exit_initiator="user",
            exit_reason="rpg_wake_hard_exit",
        )
        return {"retained": False, **close_result}
    dream_id = str(state.get("dream_id") or "").strip()
    requested_dream_id = _runtime._requested_dream_id(body)

    # A supplied identity is never allowed to act on another live session.
    if requested_dream_id and requested_dream_id != dream_id:
        # A closed session may be queried idempotently with its last id.  A
        # live session (including a later new dream) must never accept an old
        # confirmation token.
        closed_last_id = str(state.get("last_dream_id") or "").strip()
        if status not in {
            DreamStatus.REALITY_AFTERGLOW.value,
            DreamStatus.REALITY_CHAT.value,
            DreamStatus.DREAM_ENTRANCE_AVAILABLE.value,
        } or requested_dream_id != closed_last_id:
            raise _runtime.HTTPException(status_code=409, detail="dream_session_mismatch")

    # A retention response is a live confirmation state, not a closed dream.
    # The second WAKE (or the leave choice) must carry the same session id and
    # use the single close chokepoint with an observable user-confirmed reason.
    if status == DreamStatus.DREAM_EXIT_REQUESTED.value:
        if not dream_id or requested_dream_id != dream_id:
            raise _runtime.HTTPException(status_code=409, detail="dream_session_mismatch")
        _runtime._set_wake_observation(
            state,
            dream_id=dream_id,
            result="confirmed_wake",
            confirmation="confirmed_wake",
            retention_offered=True,
        )
        write_state(uid, state)
        close_result = await force_exit_dream(
            uid,
            exit_mechanism="user_hard_exit",
            exit_initiator="user",
            exit_reason="user_wake_confirmed_after_retention",
        )
        return {"retained": False, **close_result}

    # REALITY/entrance/locked states are already outside the retention gate.
    # DREAM_CLOSING is retried only through force_exit_dream so an archive
    # failure can recover, while completed states return their first metadata.
    if status != DreamStatus.DREAM_ACTIVE.value:
        close_result = await force_exit_dream(uid)
        return {"retained": False, **close_result}

    # Already offered retention this dream → hard exit (no repeated nagging)
    if state.get("retention_offered_dream_id") == dream_id:
        _runtime._set_wake_observation(
            state,
            dream_id=dream_id,
            result="closed",
            confirmation="no_retention",
            retention_offered=False,
        )
        write_state(uid, state)
        close_result = await force_exit_dream(
            uid,
            exit_mechanism="user_hard_exit",
            exit_initiator="user",
            exit_reason="user_wake_no_retention",
        )
        return {"retained": False, **close_result}

    # Gate check: immersion + emotional threshold
    if not _should_retain(state):
        _runtime._set_wake_observation(
            state,
            dream_id=dream_id,
            result="closed",
            confirmation="no_retention",
            retention_offered=False,
        )
        write_state(uid, state)
        close_result = await force_exit_dream(
            uid,
            exit_mechanism="user_hard_exit",
            exit_initiator="user",
            exit_reason="user_wake_no_retention",
        )
        return {"retained": False, **close_result}

    # Transition to EXIT_REQUESTED and mark retention offered
    from core.dream.dream_flow import append_status_shift
    state = append_status_shift(state, "exit_requested")
    state["status"] = DreamStatus.DREAM_EXIT_REQUESTED.value
    state["retention_offered_dream_id"] = dream_id
    write_state(uid, state)

    # Generate retention line — fail-open: LLM failure → hard exit
    retention_text = await _generate_retention_line(uid, state)
    if not retention_text:
        _runtime.logger.warning("[dream_wake] retention LLM failed uid=%s, falling back to hard exit", uid)
        _runtime._set_wake_observation(
            state,
            dream_id=dream_id,
            result="closed",
            confirmation="no_retention",
            retention_offered=False,
        )
        write_state(uid, state)
        close_result = await force_exit_dream(
            uid,
            exit_mechanism="user_hard_exit",
            exit_initiator="user",
            exit_reason="user_wake_no_retention",
        )
        return {"retained": False, **close_result}

    state = append_status_shift(state, "retained")
    _runtime._set_wake_observation(
        state,
        dream_id=dream_id,
        result="retained",
        confirmation="pending",
        retention_offered=True,
    )
    write_state(uid, state)
    return {"retained": True, "retention_text": retention_text, "dream_id": dream_id}


@router.post("/dream/resume", summary="挽留后留下（status → DREAM_ACTIVE）")
async def dream_resume(body: dict = Body(default={}), _auth=Depends(require_scopes("activity"))):
    """
    Resume after a soft retention: set status back to DREAM_ACTIVE so
    dream_turn() can continue processing messages.

    Only acts when status == DREAM_EXIT_REQUESTED; any other status is a no-op
    (idempotent, safe to call spuriously).
    """
    from admin.routers import dream as _runtime
    uid = _runtime._owner_uid()

    from core.dream.dream_state import DreamStatus, read_state_checked, write_state

    state, state_ok = read_state_checked(uid)
    if not state_ok:
        raise _runtime.HTTPException(status_code=503, detail="dream_state_unavailable")
    requested_dream_id = _runtime._requested_dream_id(body)
    current_dream_id = str(state.get("dream_id") or "").strip()
    if requested_dream_id and requested_dream_id != current_dream_id:
        raise _runtime.HTTPException(status_code=409, detail="dream_session_mismatch")
    if state.get("status") == DreamStatus.DREAM_EXIT_REQUESTED.value:
        if not current_dream_id or requested_dream_id != current_dream_id:
            raise _runtime.HTTPException(status_code=409, detail="dream_session_mismatch")
        from core.dream.dream_flow import append_status_shift

        state = append_status_shift(state, "resumed")
        state["status"] = DreamStatus.DREAM_ACTIVE.value
        observation = state.get("last_wake_observation")
        if isinstance(observation, dict) and observation.get("dream_id") == current_dream_id:
            state["last_wake_observation"] = {
                **observation,
                "result": "retained_and_resumed",
                "confirmation": "stayed",
                "resumed_at": _runtime.time.time(),
            }
        write_state(uid, state)
        _runtime.logger.info("[dream_resume] resumed uid=%s dream_id=%s", uid, current_dream_id)
        return {"ok": True, "resumed": True, "dream_id": current_dream_id}

    return {
        "ok": False,
        "resumed": False,
        "dream_id": str(state.get("last_dream_id") or "") or None,
        "error": "dream_not_waiting_for_confirmation",
    }
