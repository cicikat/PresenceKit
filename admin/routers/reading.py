"""
一起看书 Activity HTTP API (P0)

POST /activity/reading/start      — 上传 PDF，创建阅读 session
GET  /activity/reading/state      — 获取当前 active session
GET  /activity/reading/page       — 读取某一页
POST /activity/reading/turn_page  — 翻页（direction 或指定 page）
POST /activity/reading/close      — 关闭 session

设计约束（见 docs/reading-activity.md）：
- Reality-side Activity，不接 trigger / stimulus / Dream / Scenario。
- 页面内容不写 short_term / event_log / user_hidden_state。
- P0 仅支持文本型 PDF；扫描版返回明确的 422 错误，不静默返回空文本。
- 文件名经过路径安全过滤，不信任上传者提供的原始 filename。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from admin.auth import verify_token
from core.activity import activity_store
from core.activity.pdf_reader import (
    PDFFileTooLarge,
    PDFOCRRequired,
    PDFReadError,
    extract_pages,
)
from core.activity.reading_session import (
    ReadingSession,
    make_file_id,
    new_session_id,
    now_iso,
)
from core.config_loader import get_config
from core.sandbox import get_paths as _get_paths

router = APIRouter()
logger = logging.getLogger(__name__)

# 安全文件名：只保留字母 / 数字 / 下划线 / 横线 / 点，去掉其余字符
_UNSAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9._\-]")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_FILENAME_LEN = 128


# ── 公用助手 ──────────────────────────────────────────────────────────────────

def _sanitize_filename(raw: str) -> str:
    """提取文件名部分并替换不安全字符。"""
    name = Path(raw).name          # 去掉目录前缀（跨平台）
    name = _UNSAFE_CHAR_RE.sub("_", name)
    if len(name) > _MAX_FILENAME_LEN:
        name = name[:_MAX_FILENAME_LEN]
    return name or "upload.pdf"


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
        raise HTTPException(
            status_code=422,
            detail=f"无效的 session_id: {session_id!r}",
        )
    return session_id


def _require_session(char_id: str, session_id: str) -> ReadingSession:
    """按 session_id 查找 session，若不存在或已关闭则抛 HTTPException。"""
    session = activity_store.load_session_by_id(char_id, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id!r} 不存在")
    return session


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/reading/start", summary="上传 PDF 并开始阅读 session")
async def start_reading(
    file: UploadFile = File(...),
    start_page: int = Form(default=1),
    uid: str = Form(default=""),
    auth=Depends(verify_token),
):
    char_id = _active_char_id()
    resolved_uid = uid.strip() or _default_uid()
    safe_name = _sanitize_filename(file.filename or "upload.pdf")

    content = await file.read()
    try:
        pdf_info, pages = extract_pages(content, safe_name)
    except PDFOCRRequired as e:
        raise HTTPException(status_code=422, detail=str(e))
    except PDFFileTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e))
    except PDFReadError as e:
        raise HTTPException(status_code=422, detail=str(e))

    total = pdf_info.total_pages
    if start_page < 1 or start_page > total:
        raise HTTPException(
            status_code=422,
            detail=f"start_page {start_page} 超出范围 [1, {total}]",
        )

    now = now_iso()
    session = ReadingSession(
        session_id=new_session_id(),
        uid=resolved_uid,
        char_id=char_id,
        file_id=make_file_id(safe_name),
        filename=safe_name,
        total_pages=total,
        current_page=start_page,
        created_at=now,
        updated_at=now,
        status="active",
    )

    activity_store.save_session(session)
    activity_store.save_pages(char_id, resolved_uid, session.session_id, pages)
    logger.info(
        "[reading] start: uid=%s char=%s file=%r pages=%d session=%s",
        resolved_uid, char_id, safe_name, total, session.session_id,
    )
    return session.to_dict()


@router.get("/reading/state", summary="获取当前 active 阅读 session")
async def get_reading_state(
    uid: str = Query(default=""),
    auth=Depends(verify_token),
):
    char_id = _active_char_id()
    resolved_uid = uid.strip() or _default_uid()
    session = activity_store.find_active_session(char_id, resolved_uid)
    if session is None:
        return {"active": False}
    return {"active": True, **session.to_dict()}


@router.get("/reading/page", summary="读取某一页内容")
async def get_page(
    session_id: str = Query(...),
    page: int = Query(...),
    auth=Depends(verify_token),
):
    char_id = _active_char_id()
    _validate_session_id(session_id)
    session = _require_session(char_id, session_id)
    if session.status != "active":
        raise HTTPException(status_code=409, detail="session 已关闭")
    if page < 1 or page > session.total_pages:
        raise HTTPException(
            status_code=422,
            detail=f"page {page} 超出范围 [1, {session.total_pages}]",
        )
    text = activity_store.load_page(char_id, session.uid, session_id, page)
    if text is None:
        raise HTTPException(status_code=500, detail=f"第 {page} 页文本文件缺失")
    return {
        "page": page,
        "total_pages": session.total_pages,
        "text": text,
        "text_length": len(text),
    }


class TurnPageRequest(BaseModel):
    session_id: str
    direction: Optional[Literal["next", "prev"]] = None
    page: Optional[int] = None


@router.post("/reading/turn_page", summary="翻页")
async def turn_page(
    body: TurnPageRequest,
    auth=Depends(verify_token),
):
    char_id = _active_char_id()
    _validate_session_id(body.session_id)
    session = _require_session(char_id, body.session_id)
    if session.status != "active":
        raise HTTPException(status_code=409, detail="session 已关闭")

    current = session.current_page
    if body.page is not None:
        target = body.page
    elif body.direction == "next":
        target = current + 1
    elif body.direction == "prev":
        target = current - 1
    else:
        raise HTTPException(status_code=422, detail="需提供 direction 或 page")

    if target < 1 or target > session.total_pages:
        raise HTTPException(
            status_code=422,
            detail=f"page {target} 超出范围 [1, {session.total_pages}]",
        )

    session.current_page = target
    session.updated_at = now_iso()
    activity_store.save_session(session)

    text = activity_store.load_page(char_id, session.uid, body.session_id, target)
    if text is None:
        raise HTTPException(status_code=500, detail=f"第 {target} 页文本文件缺失")
    return {
        "page": target,
        "total_pages": session.total_pages,
        "text": text,
        "text_length": len(text),
    }


class CloseRequest(BaseModel):
    session_id: str
    brief_summary: Optional[str] = None


@router.post("/reading/close", summary="关闭阅读 session")
async def close_reading(
    body: CloseRequest,
    auth=Depends(verify_token),
):
    char_id = _active_char_id()
    _validate_session_id(body.session_id)
    session = _require_session(char_id, body.session_id)

    if session.status == "closed":
        return {
            "status": "closed",
            "session_id": body.session_id,
            "filename": session.filename,
            "total_pages": session.total_pages,
            "last_page": session.current_page,
            "closed_at": session.updated_at,
        }

    now = now_iso()
    session.status = "closed"
    session.updated_at = now
    activity_store.save_session(session)
    logger.info(
        "[reading] close: session=%s last_page=%d", body.session_id, session.current_page
    )
    return {
        "status": "closed",
        "session_id": body.session_id,
        "filename": session.filename,
        "total_pages": session.total_pages,
        "last_page": session.current_page,
        "closed_at": now,
    }
