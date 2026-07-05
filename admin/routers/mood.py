"""
情绪状态路由
"""
from fastapi import APIRouter, Depends

from admin.auth import require_scopes
from admin.routers._common import active_char_id as _active_char_id
from core.memory import mood_state

router = APIRouter()


@router.get("/state", summary="获取情绪状态")
async def get_mood_state(auth=Depends(require_scopes("state.read"))):
    return mood_state.load(char_id=_active_char_id())
