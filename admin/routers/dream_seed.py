"""Dream Seed activity HTTP API."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from admin.auth import verify_token
from core.activity import dream_seed
from core.config_loader import get_config
from core.sandbox import get_paths

router = APIRouter()

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_MESSAGE_LEN = 1000


def _active_char_id() -> str:
    try:
        raw = json.loads(get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        char_id = str(raw.get("active_character") or "").strip()
    except Exception:
        raise HTTPException(status_code=503, detail="active character unavailable")
    if not char_id:
        raise HTTPException(status_code=503, detail="active_character missing")
    from core.asset_registry import get_registry
    try:
        get_registry().resolve(char_id, "character")
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown character id: {char_id!r}")
    return char_id


def _default_uid() -> str:
    return str(get_config().get("scheduler", {}).get("owner_id")
               or get_config().get("default_user_id")
               or "owner")


def _session_id(value: str) -> str:
    if not value or not _SESSION_ID_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail=f"无效的 session_id: {value!r}")
    return value


class StartRequest(BaseModel):
    uid: str = ""


class ChatRequest(BaseModel):
    session_id: str
    message: str
    uid: str = ""


class CloseRequest(BaseModel):
    session_id: str
    uid: str = ""


@router.post("/dream_seed/start", summary="开始梦境预构")
async def start(body: StartRequest, _auth=Depends(verify_token)):
    uid = body.uid.strip() or _default_uid()
    session = dream_seed.start_session(uid, char_id=_active_char_id())
    return {"session_id": session.session_id, "status": session.status}


@router.get("/dream_seed/state", summary="读取梦境预构状态")
async def state(uid: str = Query(default=""), _auth=Depends(verify_token)):
    resolved_uid = uid.strip() or _default_uid()
    char_id = _active_char_id()
    from core.activity import store
    session = store.find_active_session(char_id, resolved_uid, dream_seed.ACTIVITY_TYPE)
    seed = dream_seed.load_seed(resolved_uid, char_id=char_id)
    return {
        "active": session is not None,
        "session_id": session.session_id if session else None,
        "has_seed": bool(seed),
        "seed_preview": (seed or "")[:40],
    }


@router.post("/dream_seed/chat", summary="梦境预构活动内对话")
async def chat(body: ChatRequest, _auth=Depends(verify_token)):
    uid = body.uid.strip() or _default_uid()
    char_id = _active_char_id()
    session_id = _session_id(body.session_id)
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")
    if len(message) > _MAX_MESSAGE_LEN:
        raise HTTPException(status_code=422, detail=f"message 超出 {_MAX_MESSAGE_LEN} 字限制")
    session = dream_seed.get_session(uid, session_id, char_id=char_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session 不存在")
    if session.status != "active":
        raise HTTPException(status_code=409, detail="session 已关闭")

    dream_seed.append_turn(uid, session_id, "user", message, char_id=char_id)
    reply = await dream_seed.generate_reply(uid, session_id, message, char_id=char_id)
    if reply:
        dream_seed.append_turn(uid, session_id, "assistant", reply, char_id=char_id)
    return {"session_id": session_id, "reply": reply}


@router.post("/dream_seed/close", summary="关闭梦境预构并提炼种子")
async def close(body: CloseRequest, _auth=Depends(verify_token)):
    uid = body.uid.strip() or _default_uid()
    seed = await dream_seed.close_session(
        uid,
        _session_id(body.session_id),
        char_id=_active_char_id(),
    )
    return {"success": bool(seed), "seed_text": seed or ""}
