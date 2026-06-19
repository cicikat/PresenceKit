"""
五子棋 Activity HTTP API (P0 + P0-companion-chat + P3-pending)

POST /activity/gomoku/start    — 开局
GET  /activity/gomoku/state    — 获取当前棋局
POST /activity/gomoku/move     — 落子
POST /activity/gomoku/close    — 关闭棋局
POST /activity/gomoku/chat     — 活动内对话（P0）
POST /activity/gomoku/ai_move  — 执行待处理 AI 落子（pending mode）

设计约束（见 docs/gomoku-activity.md）：
- Reality-side Activity，不接 trigger / stimulus / Dream / Scenario。
- 规则、胜负、合法性由代码判断，不由 LLM 判断。
- 每步棋只写 activity session，不写 short_term / event_log / user_hidden_state。
- /chat 只写 activity transcript，不写主记忆。
- /ai_move 读取 transcript 最近 control.ai_style_tilt 轻微影响本次风格，不写主记忆。
"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from admin.auth import verify_token
from core.activity import activity_summary as _activity_summary
from core.activity import gomoku as gomoku_engine
from core.activity import gomoku_companion
from core.activity import store as gomoku_store
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
    opponent: str = "human"
    ai_style: str = "balanced"
    ai_response_mode: str = "auto"


@router.post("/gomoku/start", summary="开始一局五子棋")
async def start_gomoku(body: StartRequest, auth=Depends(verify_token)):
    char_id = _active_char_id()
    uid = body.uid.strip() or _default_uid()
    logger.info(
        "[gomoku] start request uid=%s char_id=%s opponent=%r ai_style=%r",
        uid,
        char_id,
        body.opponent,
        body.ai_style,
    )
    if body.board_size != 15:
        raise HTTPException(status_code=422, detail="P0 只支持 board_size=15")
    try:
        session = gomoku_engine.start_game(
            uid, char_id, body.board_size, body.opponent, body.ai_style, body.ai_response_mode
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    state = session.state
    logger.info(
        "[gomoku] start created session_id=%s opponent=%r ai_player=%r ai_style=%r ai_response_mode=%r",
        session.session_id,
        state.get("opponent"),
        state.get("ai_player"),
        state.get("ai_style"),
        state.get("ai_response_mode"),
    )
    return {
        "session_id": session.session_id,
        "board_size": state["board_size"],
        "board": state["board"],
        "current_turn": state["current_turn"],
        "status": state["status"],
        "opponent": state["opponent"],
        "ai_player": state["ai_player"],
        "ai_style": state["ai_style"],
        "ai_response_mode": state.get("ai_response_mode", "auto"),
        "pending_ai_turn": state.get("pending_ai_turn", False),
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
    session, summary = gomoku_engine.close_game(uid, char_id, body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {body.session_id!r} 不存在")
    if summary is not None:
        await _activity_summary.generate_and_reflow(uid, char_id, "gomoku", session.session_id)
    resp: dict = {
        "session_id": session.session_id,
        "status": "closed",
        "closed_at": session.updated_at,
    }
    if summary is not None:
        resp["activity_summary"] = summary
    return resp


class AiMoveRequest(BaseModel):
    session_id: str
    uid: str = ""


@router.post("/gomoku/ai_move", summary="执行待处理的 AI 落子（pending mode）")
async def gomoku_ai_move(body: AiMoveRequest, auth=Depends(verify_token)):
    """
    执行 pending mode 下待处理的 AI 落子。

    只在 ai_response_mode="pending" 且用户落子后 pending_ai_turn=True 时有效。
    读取最近 transcript 中的 ai_style_tilt control 轻微影响本次 AI 风格（不永久覆盖）。
    LLM 不参与落子，规则引擎负责胜负判定。

    错误：
    - 404: session 不存在
    - 409: session 已关闭 / 棋局已结束 / 非 AI 对手 / pending_ai_turn=False / 非 AI 轮次
    """
    char_id = _active_char_id()
    uid = body.uid.strip() or _default_uid()
    _validate_session_id(body.session_id)

    session = gomoku_store.load_session(char_id, uid, "gomoku", body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {body.session_id!r} 不存在")

    style_tilt = gomoku_companion.get_recent_ai_style_tilt(char_id, uid, body.session_id)

    try:
        result = gomoku_engine.apply_ai_move(uid, char_id, body.session_id, style_tilt)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    logger.info(
        "[gomoku] ai_move session=%s style_tilt=%r result_status=%r",
        body.session_id,
        style_tilt,
        result.get("status"),
    )
    return result


_CHAT_MAX_MESSAGE_LEN = 1000


class ChatRequest(BaseModel):
    session_id: str
    message: str
    uid: str = ""


@router.post("/gomoku/chat", summary="活动内对话（P0）")
async def gomoku_chat(body: ChatRequest, auth=Depends(verify_token)):
    """
    活动内对话接口（P0）。

    只写 activity transcript，不写 short_term / event_log / user_hidden_state。
    不修改棋盘状态（board / move_history / winner / status）。
    只有 active session 允许聊天。
    """
    char_id = _active_char_id()
    uid = body.uid.strip() or _default_uid()
    _validate_session_id(body.session_id)

    msg = body.message.strip() if body.message else ""
    if not msg:
        raise HTTPException(status_code=422, detail="message 不能为空")
    if len(body.message) > _CHAT_MAX_MESSAGE_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"message 超出 {_CHAT_MAX_MESSAGE_LEN} 字限制",
        )

    session = gomoku_store.load_session(char_id, uid, "gomoku", body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {body.session_id!r} 不存在")
    if session.status != "active":
        raise HTTPException(status_code=409, detail=f"session {body.session_id!r} 已关闭，不允许聊天")

    reply, control, grounding = await gomoku_companion.generate_reply(
        char_id=char_id,
        uid=uid,
        session_id=body.session_id,
        state=session.state,
        user_message=msg,
    )

    return {
        "session_id": body.session_id,
        "reply": reply,
        "control": control,
        "grounding": grounding,
    }
