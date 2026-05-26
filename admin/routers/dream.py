"""
Dream session endpoints.

POST /dream/enter    — enter dream (build frozen snapshot, DREAM_ACTIVE)
POST /dream/chat     — dream turn (goes to dream_pipeline, never reality pipeline)
POST /dream/exit     — hard exit (force_exit_dream, unconditional)
GET  /dream/state    — read current dream state for uid
POST /dream/settings — update per-uid dream settings

Constraints:
- /dream/chat never calls notify_owner_turn, never triggers scheduler/gating.
- conversation_lock(uid) wraps the full dream_turn for serialization safety.
- Hard reject: DREAM_ACTIVE / DREAM_CLOSING prevents reality endpoints from
  processing turns (safety net implemented in chat.py and mobile.py).
"""

import logging

from fastapi import APIRouter, HTTPException

from core.config_loader import get_config

router = APIRouter()
logger = logging.getLogger(__name__)


def _owner_uid() -> str:
    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    if not uid:
        raise HTTPException(status_code=503, detail="owner_id 未配置")
    return uid


@router.post("/dream/enter", summary="进入梦境")
async def dream_enter(body: dict = {}):
    uid = _owner_uid()
    entry_reason = (body.get("entry_reason") or "").strip()

    from core.dream.dream_pipeline import enter_dream
    result = await enter_dream(uid, entry_reason=entry_reason)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "cannot enter dream"))
    return result


@router.post("/dream/chat", summary="梦境对话（独立 pipeline）")
async def dream_chat(body: dict):
    """
    Dream turn endpoint — routes to dream_pipeline, never to reality pipeline.

    conversation_lock(uid) serializes the full turn.
    Does NOT call notify_owner_turn, scheduler, or gating.
    """
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")

    uid = _owner_uid()

    from core.conversation_gate import conversation_lock
    from core.dream.dream_pipeline import dream_turn

    async with conversation_lock(uid):
        result = await dream_turn(uid, message)

    if err := result.get("error"):
        raise HTTPException(status_code=409, detail=err)

    return result


@router.post("/dream/exit", summary="强退梦境（硬出口，不可被拒）")
async def dream_exit():
    """
    Hard exit — unconditional, immediate, penetrates all state.
    Cannot be disabled by config or role behavior (invariant D).
    """
    uid = _owner_uid()

    from core.dream.dream_pipeline import force_exit_dream
    await force_exit_dream(uid)

    return {"ok": True, "exited": True}


@router.get("/dream/state", summary="读取梦境状态")
async def dream_state_get():
    uid = _owner_uid()
    from core.dream.dream_state import read_state
    return read_state(uid)


@router.post("/dream/settings", summary="更新梦境设置")
async def dream_settings_update(body: dict):
    uid = _owner_uid()
    from core.dream.dream_settings import load as _load, save as _save

    current = _load(uid)
    allowed_keys = {"enable_dream_lorebook", "amnesia", "keep_impression"}
    updated = {k: v for k, v in body.items() if k in allowed_keys}
    if not updated:
        raise HTTPException(status_code=422, detail=f"可设置字段：{sorted(allowed_keys)}")
    current.update(updated)
    _save(uid, current)
    return {"ok": True, "settings": current}
