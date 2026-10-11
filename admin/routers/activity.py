"""Current activity comes only from an existing Reality session."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException

from admin.auth import require_scopes
from admin.routers._common import active_char_id as _active_char_id
from admin.routers._common import resolve_requested_char_id
from core.config_loader import get_config

router = APIRouter()


@router.get("/records", summary="Reality活动来源与分页记录")
async def get_activity_records(
    char_id: Optional[str] = None, source_ref: str = "", offset: int = 0,
    limit: int = 20, text_offset: int = 0, expected_revision: str = "",
    keyword: str = "", since: str = "", until: str = "",
    auth=Depends(require_scopes("state.read")),
):
    from core.activity import records
    char_id = resolve_requested_char_id(char_id, fallback=_active_char_id)
    uid = str(get_config().get("scheduler", {}).get("owner_id") or "")
    if not uid:
        return {"items": [], "tasks": []}
    try:
        if source_ref:
            return records.read(uid, char_id, source_ref, offset=offset, limit=limit,
                                text_offset=text_offset, expected_revision=expected_revision,
                                keyword=keyword, since=since, until=until)
        if keyword or since or until:
            return records.search(uid, char_id, keyword=keyword, since=since, until=until,
                                  offset=offset, limit=limit)
        return records.list_sessions(uid, char_id, offset=offset, limit=limit) | {"tasks": records.tasks(uid, char_id)}
    except LookupError:
        raise HTTPException(404, "source unavailable")
    except ValueError:
        raise HTTPException(409, "invalid query or changed source revision")


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
