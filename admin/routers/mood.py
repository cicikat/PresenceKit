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
    state = mood_state.load(char_id=resolved)
    return {**{k: v for k, v in state.items() if k not in ("affect_events", "receipts", "withdrawn_event_ids")},
            "char_id": resolved, "prompt_eligible": bool(state.get("has_signal")),
            "prompt_layer": "expression_afterglow",
            "affect_prompt_eligible": bool(state["affect"]["enabled"] and state["affect"]["has_signal"]),
            "affect_prompt_layer": "affective_state", "event_count": len(state["affect_events"]),
            "events_endpoint": "/mood/events"}


@router.get("/events", summary="情绪事件与重评来源（只读）")
async def get_affect_events(
    char_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=48),
    auth=Depends(require_scopes("memory.read")),
):
    resolved = resolve_requested_char_id(char_id, fallback=_active_char_id)
    state = mood_state.load(char_id=resolved)
    return {"char_id": resolved, "version": state["version"], "status": state["appraisal_status"],
            "items": list(reversed(state["affect_events"]))[:limit], "total": len(state["affect_events"]),
            "limit": limit, "max_events": 48, "read_only": True}
