"""Dream sessions API responsibilities; legacy names re-exported by dream.py."""

from admin.routers import dream_common

from core.dream import session_control

import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from admin.auth import require_scopes
from admin.routers._common import resolve_requested_char_id
from core.dream.dream_state import VALID_DREAM_MODES

router = APIRouter()
logger = logging.getLogger("admin.routers.dream")

_requested_dream_id = session_control._requested_dream_id


_set_wake_observation = session_control._set_wake_observation


@router.post("/dream/enter", summary="进入梦境")
async def dream_enter(body: dict = {}, _auth=Depends(require_scopes("activity"))):
    uid = dream_common._owner_uid()
    entry_reason = (body.get("entry_reason") or "").strip()
    dream_mode = (body.get("dream_mode") or "sandbox").strip()
    script_id = (body.get("script_id") or "").strip() or None

    if dream_mode not in VALID_DREAM_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"dream_mode={dream_mode!r} 非法，有效值：{sorted(VALID_DREAM_MODES)}",
        )

    from core.pipeline_registry import get as _get_pipeline
    from core.dream.dream_pipeline import enter_dream

    pl = _get_pipeline()
    if pl is None:
        raise HTTPException(status_code=503, detail="pipeline not initialized")
    char_id = resolve_requested_char_id(
        body.get("char_id"), fallback=dream_common._active_dream_char_id,
    )

    result = await enter_dream(
        uid, entry_reason=entry_reason, char_id=char_id,
        dream_mode=dream_mode, script_id=script_id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "cannot enter dream"))
    return result


@router.post("/dream/chat", summary="梦境对话（独立 pipeline）")
async def dream_chat(body: dict, _auth=Depends(require_scopes("activity"))):
    """
    Dream turn endpoint — routes to dream_pipeline, never to reality pipeline.

    conversation_lock(uid) serializes the full turn.
    Does NOT call notify_owner_turn, scheduler, or gating.
    """
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")

    uid = dream_common._owner_uid()

    from core.dream.dream_state import DreamStatus, read_state_checked
    current_state, state_ok = read_state_checked(uid)
    if (
        state_ok
        and current_state.get("status") == DreamStatus.DREAM_ACTIVE.value
        and current_state.get("dream_mode") == "rpg"
    ):
        raise HTTPException(
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
        raise HTTPException(status_code=409, detail=err)

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
            logger.debug("[dream_chat] pseudo_stream_push failed", exc_info=True)
        result["msg_id"] = _msg_id

    if scenario_reconcile_request:
        try:
            from core.dream.scenario_reconciler import schedule as _schedule_scenario_reconcile

            _schedule_scenario_reconcile(scenario_reconcile_request)
        except Exception:
            logger.debug("[dream_chat] scenario reconciler schedule failed", exc_info=True)

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
            logger.warning("[dream_chat] Reality continuation enqueue failed", exc_info=True)

    return result


@router.post("/dream/exit", summary="强退梦境（硬出口，不可被拒）")
async def dream_exit(_auth=Depends(require_scopes("activity"))):
    """
    Hard exit — unconditional, immediate, penetrates all state.
    Cannot be disabled by config or role behavior (invariant D).
    """
    uid = dream_common._owner_uid()

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
    try:
        return await session_control.wake(dream_common._owner_uid(), body)
    except session_control.DreamSessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/dream/resume", summary="挽留后留下（status → DREAM_ACTIVE）")
async def dream_resume(body: dict = Body(default={}), _auth=Depends(require_scopes("activity"))):
    """
    Resume after a soft retention: set status back to DREAM_ACTIVE so
    dream_turn() can continue processing messages.

    Only acts when status == DREAM_EXIT_REQUESTED; any other status is a no-op
    (idempotent, safe to call spuriously).
    """
    try:
        return await session_control.resume(dream_common._owner_uid(), body)
    except session_control.DreamSessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
