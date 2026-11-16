"""Dream wake/resume state transitions, independent of HTTP/router code."""

import logging
import re
import time

logger = logging.getLogger("admin.routers.dream")
_SAFE_DREAM_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,160}$")


class DreamSessionError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validated_dream_id(dream_id: str) -> str:
    value = str(dream_id or "").strip()
    if not _SAFE_DREAM_ID_RE.fullmatch(value):
        raise DreamSessionError(422, "dream_id 不合法")
    return value


def _requested_dream_id(body: dict | None) -> str | None:
    """Read the optional session identity used by WAKE/resume transitions."""
    if body in (None, {}):
        return None
    if not isinstance(body, dict):
        raise DreamSessionError(status_code=422, detail="dream request body invalid")
    raw = body.get("dream_id")
    if raw is None:
        return None
    return validated_dream_id(str(raw))


def _set_wake_observation(
    state: dict,
    *,
    dream_id: str,
    result: str,
    confirmation: str,
    retention_offered: bool,
) -> dict:
    """Keep bounded, text-free WAKE choice metadata in the durable state."""
    state["last_wake_observation"] = {
        "dream_id": dream_id,
        "result": result,
        "confirmation": confirmation,
        "retention_offered": bool(retention_offered),
        "ts": time.time(),
    }
    return state


async def wake(uid: str, body: dict | None = None):
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

    from core.dream.dream_state import DreamStatus, read_state_checked, write_state
    from core.dream.dream_pipeline import (
        _should_retain, _generate_retention_line, force_exit_dream,
    )

    state, state_ok = read_state_checked(uid)
    if not state_ok:
        raise DreamSessionError(status_code=503, detail="dream_state_unavailable")
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
    requested_dream_id = _requested_dream_id(body)

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
            raise DreamSessionError(status_code=409, detail="dream_session_mismatch")

    # A retention response is a live confirmation state, not a closed dream.
    # The second WAKE (or the leave choice) must carry the same session id and
    # use the single close chokepoint with an observable user-confirmed reason.
    if status == DreamStatus.DREAM_EXIT_REQUESTED.value:
        if not dream_id or requested_dream_id != dream_id:
            raise DreamSessionError(status_code=409, detail="dream_session_mismatch")
        _set_wake_observation(
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
        _set_wake_observation(
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
        _set_wake_observation(
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
        logger.warning("[dream_wake] retention LLM failed uid=%s, falling back to hard exit", uid)
        _set_wake_observation(
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
    _set_wake_observation(
        state,
        dream_id=dream_id,
        result="retained",
        confirmation="pending",
        retention_offered=True,
    )
    write_state(uid, state)
    return {"retained": True, "retention_text": retention_text, "dream_id": dream_id}


async def resume(uid: str, body: dict | None = None):
    """
    Resume after a soft retention: set status back to DREAM_ACTIVE so
    dream_turn() can continue processing messages.

    Only acts when status == DREAM_EXIT_REQUESTED; any other status is a no-op
    (idempotent, safe to call spuriously).
    """

    from core.dream.dream_state import DreamStatus, read_state_checked, write_state

    state, state_ok = read_state_checked(uid)
    if not state_ok:
        raise DreamSessionError(status_code=503, detail="dream_state_unavailable")
    requested_dream_id = _requested_dream_id(body)
    current_dream_id = str(state.get("dream_id") or "").strip()
    if requested_dream_id and requested_dream_id != current_dream_id:
        raise DreamSessionError(status_code=409, detail="dream_session_mismatch")
    if state.get("status") == DreamStatus.DREAM_EXIT_REQUESTED.value:
        if not current_dream_id or requested_dream_id != current_dream_id:
            raise DreamSessionError(status_code=409, detail="dream_session_mismatch")
        from core.dream.dream_flow import append_status_shift

        state = append_status_shift(state, "resumed")
        state["status"] = DreamStatus.DREAM_ACTIVE.value
        observation = state.get("last_wake_observation")
        if isinstance(observation, dict) and observation.get("dream_id") == current_dream_id:
            state["last_wake_observation"] = {
                **observation,
                "result": "retained_and_resumed",
                "confirmation": "stayed",
                "resumed_at": time.time(),
            }
        write_state(uid, state)
        logger.info("[dream_resume] resumed uid=%s dream_id=%s", uid, current_dream_id)
        return {"ok": True, "resumed": True, "dream_id": current_dream_id}

    return {
        "ok": False,
        "resumed": False,
        "dream_id": str(state.get("last_dream_id") or "") or None,
        "error": "dream_not_waiting_for_confirmation",
    }
