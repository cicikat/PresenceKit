"""Reality-turn HTTP admission; Dream state and policy remain in core.dream."""
import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)

_DREAM_GUARD_UNCERTAIN_MSG = (
    "梦境状态暂时无法确认，为避免串写现实记忆，已暂停这次现实对话。"
)

def require_reality_turn(uid: str) -> None:
    """
    Safety net: hard reject reality turns when dream is active or unconfirmable.

    Fail-closed: if the dream state file exists but cannot be read or parsed,
    the reality turn is rejected rather than allowed through.
    FileNotFoundError is treated as inactive (normal startup / no dream session).
    """
    try:
        from core.dream.dream_state import get_reality_guard_status, DreamGuardStatus
        guard = get_reality_guard_status(uid)
    except Exception:
        logger.error("[dream_guard] guard check failed uid=%s — fail closed", uid, exc_info=True)
        raise HTTPException(status_code=409, detail=_DREAM_GUARD_UNCERTAIN_MSG)

    if guard == DreamGuardStatus.BLOCK_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail="还在梦里，先醒过来（dream active — reality turn rejected）",
        )
    if guard == DreamGuardStatus.BLOCK_UNCERTAIN:
        logger.error("[dream_guard] reality turn rejected — unconfirmable dream state uid=%s", uid)
        raise HTTPException(status_code=409, detail=_DREAM_GUARD_UNCERTAIN_MSG)
