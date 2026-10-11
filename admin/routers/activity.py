"""Current activity comes only from an existing Reality session."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

from admin.auth import require_scopes
from admin.routers._common import active_char_id as _active_char_id
from admin.routers._common import resolve_requested_char_id
from core.config_loader import get_config

router = APIRouter()


def _timestamp(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _current_session(char_id: str):
    from core.activity import activity_store, store
    from core.activity.registry import ACTIVITY_REGISTRY

    uid = str(get_config().get("scheduler", {}).get("owner_id") or "")
    if not uid:
        return None, ""
    from core.dream.dream_state import read_state, DreamStatus
    dream = read_state(uid)
    if dream.get("status") in (DreamStatus.DREAM_ACTIVE.value, DreamStatus.DREAM_CLOSING.value):
        return None, "在做梦"
    for meta in ACTIVITY_REGISTRY:
        if meta.id == "dream_seed":
            continue
        session = (
            activity_store.find_active_session(char_id, uid)
            if meta.session_store == "reading_store"
            else store.find_active_session(char_id, uid, meta.id, persist_expiry=False)
        )
        if session is not None:
            return session, meta.label
    return None, ""


@router.get("/current", summary="获取当前活动状态")
async def get_activity_state(
    char_id: Optional[str] = Query(default=None, description="角色 id；缺省 = active char"),
    auth=Depends(require_scopes("state.read")),
):
    char_id = resolve_requested_char_id(char_id, fallback=_active_char_id)
    session, text = _current_session(char_id)
    return {
        "id": session.session_id if session else None,
        "char_id": char_id,
        "text": text,
        "arc": None,
        "started_at": _timestamp(session.created_at) if session else None,
        "next_switch_at": None,
        "thinking_about_eligible": False,
    }
