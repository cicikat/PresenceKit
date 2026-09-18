"""
情绪状态路由
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from admin.auth import require_scopes
from admin.routers._common import active_char_id as _active_char_id
from admin.routers._common import resolve_requested_char_id
from core.memory import mood_state

router = APIRouter()


@router.get("/state", summary="获取情绪状态")
async def get_mood_state(
    char_id: Optional[str] = Query(default=None, description="角色 id；缺省 = active char"),
    auth=Depends(require_scopes("state.read")),
):
    resolved = resolve_requested_char_id(char_id, fallback=_active_char_id)
    return mood_state.load(char_id=resolved)
