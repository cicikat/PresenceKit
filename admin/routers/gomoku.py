"""
五子棋 Activity HTTP API (P0)

POST /activity/gomoku/start   — 开局
GET  /activity/gomoku/state   — 获取当前棋局
POST /activity/gomoku/move    — 落子
POST /activity/gomoku/close   — 关闭棋局

设计约束（见 docs/gomoku-activity.md）：
- Reality-side Activity，不接 trigger / stimulus / Dream / Scenario。
- 规则、胜负、合法性由代码判断，不由 LLM 判断。
- 每步棋只写 activity session，不写 short_term / event_log / user_hidden_state。
"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from admin.auth import verify_token
from core.activity import gomoku as gomoku_engine
from core.config_loader import get_config
from core.sandbox import get_paths as _get_paths

router = APIRouter()
logger = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


# ── 公用助手 ──────────────────────────────────────────────────────────────────

def _active_char_id() -> str:
    try:
        raw = json.loads(_get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        cid = (raw.get("active_character") or "").strip()
    except Exception:
        raise HTTPException(status_code=503, detail="active character unavailable")
    if not cid:
        raise HTTPException(status_code=503, detail="active_character missing")
    from core.asset_registry import get_registry
    try:
        get_registry().resolve(cid, "character")
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown character id: {cid!r}")
    return cid


def _default_uid() -> str:
    try:
        return str(get_config().get("default_user_id", "owner"))
    except Exception:
        return "owner"


def _validate_session_id(session_id: str) -> str:
    if not session_id or not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=422, detail=f"无效的 session_id: {session_id!r}")
    return session_id


# ── Endpoints ─────────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    board_size: int = 15
    uid: str = ""


@router.post("/gomoku/start", summary="开始一局五子棋")
async def start_gomoku(body: StartRequest, auth=Depends(verify_token)):
    char_id = _active_char_id()
    uid = body.uid.strip() or _default_uid()
    if body.board_size != 15:
        raise HTTPException(status_code=422, detail="P0 只支持 board_size=15")
    try:
        session = gomoku_engine.start_game(uid, char_id, body.board_size)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    state = session.state
    return {
        "session_id": session.session_id,
        "board_size": state["board_size"],
        "board": state["board"],
        "current_turn": state["current_turn"],
        "status": state["status"],
    }


@router.get("/gomoku/state", summary="获取当前棋局状态")
async def get_gomoku_state(uid: str = Query(default=""), auth=Depends(verify_token)):
    char_id = _active_char_id()
    resolved_uid = uid.strip() or _default_uid()
    session = gomoku_engine.get_active_session(resolved_uid, char_id)
    if session is None:
        return {"active": False}
    state = session.state
    return {"active": True, "session_id": session.session_id, **state}


class MoveRequest(BaseModel):
    session_id: str
    x: int
    y: int
    uid: str = ""


@router.post("/gomoku/move", summary="落子")
async def gomoku_move(body: MoveRequest, auth=Depends(verify_token)):
    char_id = _active_char_id()
    uid = body.uid.strip() or _default_uid()
    _validate_session_id(body.session_id)
    try:
        result = gomoku_engine.make_move(uid, char_id, body.session_id, body.x, body.y)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return result


class CloseRequest(BaseModel):
    session_id: str
    uid: str = ""


@router.post("/gomoku/close", summary="关闭棋局")
async def close_gomoku(body: CloseRequest, auth=Depends(verify_token)):
    char_id = _active_char_id()
    uid = body.uid.strip() or _default_uid()
    _validate_session_id(body.session_id)
    session = gomoku_engine.close_game(uid, char_id, body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {body.session_id!r} 不存在")
    return {
        "session_id": session.session_id,
        "status": "closed",
        "closed_at": session.updated_at,
    }
