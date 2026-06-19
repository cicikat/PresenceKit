"""
国际象棋 Activity HTTP API (P0)

POST /activity/chess/start        — 开局（可选自定义 FEN）
GET  /activity/chess/state        — 当前 active 棋局状态
POST /activity/chess/move         — 落子（UCI 或 SAN）
GET  /activity/chess/legal_moves  — 当前合法走法列表（UCI）
POST /activity/chess/close        — 关闭棋局

设计约束（见 docs/chess-activity.md）：
- Reality-side Activity，不接 trigger / stimulus / Dream / Scenario。
- 规则、合法性、胜负由 python-chess 判断，不由 LLM 判断。
- 每步棋只写 activity session，不写 short_term / event_log / user_hidden_state。
- P0 无 AI 对手，不接 Stockfish，不接外部 API。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from admin.auth import verify_token
from core.activity import activity_summary as _activity_summary
from core.activity import chess as chess_activity
from core.activity import chess_companion
from core.activity import store as activity_store
from core.activity.registry import get_activity_meta as _get_activity_meta
from core.config_loader import get_config
from core.sandbox import get_paths as _get_paths

router = APIRouter()
logger = logging.getLogger(__name__)

_ACTIVITY_TYPE = "chess"
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


# ── Request/Response 模型 ─────────────────────────────────────────────────────

class StartRequest(BaseModel):
    uid: str = ""
    fen: Optional[str] = None
    include_legal_moves: bool = False
    opponent: str = "human"
    ai_style: str = "balanced"


class MoveRequest(BaseModel):
    session_id: str
    move: str
    uid: str = ""
    include_legal_moves: bool = False


class CloseRequest(BaseModel):
    session_id: str
    uid: str = ""


class ChatRequest(BaseModel):
    session_id: str
    message: str
    uid: str = ""


_CHAT_MAX_MESSAGE_LEN = 1000


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/chess/start", summary="开局 — 创建 chess session")
async def start_chess(body: StartRequest, auth=Depends(verify_token)):
    char_id = _active_char_id()
    resolved_uid = body.uid.strip() or _default_uid()

    try:
        initial_state = chess_activity.make_initial_state(body.fen, body.opponent, body.ai_style)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    session = activity_store.create_session(
        uid=resolved_uid,
        char_id=char_id,
        activity_type=_ACTIVITY_TYPE,
        initial_state=initial_state,
    )
    logger.info(
        "[chess] start: uid=%s char=%s session=%s fen=%r",
        resolved_uid, char_id, session.session_id, initial_state["fen"],
    )

    resp: dict = {
        "session_id": session.session_id,
        "fen": initial_state["fen"],
        "turn": initial_state["turn"],
        "status": initial_state["status"],
        "opponent": initial_state["opponent"],
        "ai_player": initial_state["ai_player"],
        "ai_style": initial_state["ai_style"],
        "pending_ai_turn": initial_state["pending_ai_turn"],
    }
    if body.include_legal_moves:
        resp["legal_moves"] = chess_activity.legal_moves_uci(initial_state)
    return resp


@router.get("/chess/state", summary="获取当前 active chess 棋局状态")
async def get_chess_state(
    uid: str = Query(default=""),
    auth=Depends(verify_token),
):
    char_id = _active_char_id()
    resolved_uid = uid.strip() or _default_uid()
    session = activity_store.find_active_session(char_id, resolved_uid, _ACTIVITY_TYPE)
    if session is None:
        return {"active": False}
    return {"active": True, "session_id": session.session_id, **session.state}


@router.post("/chess/move", summary="落子（UCI 或 SAN）")
async def make_move(body: MoveRequest, auth=Depends(verify_token)):
    char_id = _active_char_id()
    resolved_uid = body.uid.strip() or _default_uid()
    _validate_session_id(body.session_id)

    session = activity_store.load_session(char_id, resolved_uid, _ACTIVITY_TYPE, body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {body.session_id!r} 不存在")
    if session.status == "closed":
        raise HTTPException(status_code=409, detail="session 已关闭")

    if session.state.get("status") != "active":
        raise HTTPException(
            status_code=409,
            detail=f"棋局已结束: status={session.state.get('status')!r}",
        )

    try:
        new_state = chess_activity.apply_move(session.state, body.move)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    updated = activity_store.update_state(
        char_id=char_id,
        uid=resolved_uid,
        activity_type=_ACTIVITY_TYPE,
        session_id=body.session_id,
        state=new_state,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="state 保存失败")

    resp: dict = {
        "session_id": body.session_id,
        "fen": new_state["fen"],
        "turn": new_state["turn"],
        "status": new_state["status"],
        "result": new_state["result"],
        "termination": new_state["termination"],
        "last_move": new_state["last_move"],
        "opponent": new_state.get("opponent", "human"),
        "ai_player": new_state.get("ai_player"),
        "pending_ai_turn": new_state.get("pending_ai_turn", False),
    }
    if body.include_legal_moves and new_state["status"] == "active":
        resp["legal_moves"] = chess_activity.legal_moves_uci(new_state)
    return resp


@router.get("/chess/legal_moves", summary="获取当前合法走法列表（UCI）")
async def get_legal_moves(
    session_id: str = Query(...),
    uid: str = Query(default=""),
    auth=Depends(verify_token),
):
    char_id = _active_char_id()
    resolved_uid = uid.strip() or _default_uid()
    _validate_session_id(session_id)

    session = activity_store.load_session(char_id, resolved_uid, _ACTIVITY_TYPE, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id!r} 不存在")
    if session.status == "closed":
        raise HTTPException(status_code=409, detail="session 已关闭")

    moves = chess_activity.legal_moves_uci(session.state)
    return {"session_id": session_id, "legal_moves": moves, "count": len(moves)}


@router.post("/chess/close", summary="关闭棋局 session")
async def close_chess(body: CloseRequest, auth=Depends(verify_token)):
    char_id = _active_char_id()
    resolved_uid = body.uid.strip() or _default_uid()
    _validate_session_id(body.session_id)

    session = activity_store.load_session(char_id, resolved_uid, _ACTIVITY_TYPE, body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {body.session_id!r} 不存在")

    if session.status == "closed":
        return {
            "status": "closed",
            "session_id": body.session_id,
            "closed_at": session.updated_at,
        }

    closed = activity_store.close_session(
        char_id=char_id,
        uid=resolved_uid,
        activity_type=_ACTIVITY_TYPE,
        session_id=body.session_id,
    )
    logger.info("[chess] close: session=%s", body.session_id)
    if closed:
        threshold = _get_activity_meta("chess").memory_policy.summary_threshold
        if threshold is not None and len(closed.state.get("move_history", [])) > threshold:
            await _activity_summary.generate_and_reflow(
                resolved_uid, char_id, "chess", body.session_id
            )
    return {
        "status": "closed",
        "session_id": body.session_id,
        "closed_at": closed.updated_at if closed else None,
    }


class AiMoveRequest(BaseModel):
    session_id: str
    uid: str = ""


@router.post("/chess/ai_move", summary="执行待处理的 AI 落子")
async def chess_ai_move(body: AiMoveRequest, auth=Depends(verify_token)):
    """
    执行 AI 落子（当 pending_ai_turn=True 时有效）。
    规则引擎负责胜负判定，不调用 LLM。
    """
    char_id = _active_char_id()
    resolved_uid = body.uid.strip() or _default_uid()
    _validate_session_id(body.session_id)

    session = activity_store.load_session(char_id, resolved_uid, _ACTIVITY_TYPE, body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {body.session_id!r} 不存在")
    if session.status == "closed":
        raise HTTPException(status_code=409, detail="session 已关闭")

    try:
        new_state = chess_activity.apply_ai_move(session.state)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    updated = activity_store.update_state(
        char_id=char_id,
        uid=resolved_uid,
        activity_type=_ACTIVITY_TYPE,
        session_id=body.session_id,
        state=new_state,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="state 保存失败")

    logger.info(
        "[chess] ai_move session=%s status=%r last=%r",
        body.session_id,
        new_state["status"],
        new_state.get("last_move", {}).get("san") if new_state.get("last_move") else None,
    )
    return {
        "session_id": body.session_id,
        "fen": new_state["fen"],
        "turn": new_state["turn"],
        "status": new_state["status"],
        "result": new_state["result"],
        "termination": new_state["termination"],
        "last_move": new_state["last_move"],
        "opponent": new_state.get("opponent", "yexuan_ai"),
        "ai_player": new_state.get("ai_player"),
        "pending_ai_turn": new_state.get("pending_ai_turn", False),
    }


@router.post("/chess/chat", summary="活动内对话（陪伴聊天）")
async def chess_chat(body: ChatRequest, auth=Depends(verify_token)):
    """
    活动内对话接口。

    只写 activity transcript，不写 short_term / event_log / user_hidden_state。
    不修改棋盘状态（fen / move_history / result / status）。
    只有 active session 允许聊天。
    """
    char_id = _active_char_id()
    resolved_uid = body.uid.strip() or _default_uid()
    _validate_session_id(body.session_id)

    msg = body.message.strip() if body.message else ""
    if not msg:
        raise HTTPException(status_code=422, detail="message 不能为空")
    if len(body.message) > _CHAT_MAX_MESSAGE_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"message 超出 {_CHAT_MAX_MESSAGE_LEN} 字限制",
        )

    session = activity_store.load_session(char_id, resolved_uid, _ACTIVITY_TYPE, body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {body.session_id!r} 不存在")
    if session.status != "active":
        raise HTTPException(status_code=409, detail=f"session {body.session_id!r} 已关闭，不允许聊天")

    reply, control, grounding = await chess_companion.generate_reply(
        char_id=char_id,
        uid=resolved_uid,
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
