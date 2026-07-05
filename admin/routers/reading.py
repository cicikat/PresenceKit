"""
一起看书 Activity HTTP API (P0)

POST /activity/reading/start                  — 上传 PDF，创建阅读 session
GET  /activity/reading/state                  — 获取当前 active session
GET  /activity/reading/page                   — 读取某一页
POST /activity/reading/turn_page              — 翻页（direction 或指定 page）
POST /activity/reading/close                  — 关闭 session
GET  /activity/reading/library                — 列出书库（读 manifest，含 title/category）
POST /activity/reading/library/add            — 上传 PDF 到书库（写 manifest，uuid4 book_id）
POST /activity/reading/library/delete         — 删除书库中的书（删 manifest + 磁盘文件）
POST /activity/reading/library/rename         — 修改书的显示名称（只改 manifest title）
POST /activity/reading/library/categorize     — 设置书的分类（只改 manifest category）
POST /activity/reading/start_from_library     — 从书库开始阅读（按 manifest book_id 查文件）

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
import shutil
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from admin.auth import require_scopes
from admin.routers._common import active_char_id as _active_char_id
from core.activity import activity_store
from core.activity import activity_summary as _activity_summary
from core.activity import reading_companion
from core.activity.registry import get_activity_meta as _get_activity_meta
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

# 安全文件名：保留 Unicode 文字（含中文），只清除路径分隔符和系统非法字符
_UNSAFE_CHAR_RE = re.compile(r'[\\/:\*\?"<>\|\x00-\x1f]')
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


# ── 书库 manifest 助手 ─────────────────────────────────────────────────────────

def _load_manifest() -> dict:
    path = _get_paths().reading_library_manifest()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"books": []}


def _save_manifest(manifest: dict) -> None:
    path = _get_paths().reading_library_manifest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _migrate_manifest_if_needed() -> dict:
    """首次使用时扫描 books/ 目录自动生成 manifest.json；沿用 make_file_id 作为 book_id 以兼容已有 insights。"""
    paths = _get_paths()
    if paths.reading_library_manifest().exists():
        return _load_manifest()

    books_dir = paths.reading_library_books_dir()
    books_dir.mkdir(parents=True, exist_ok=True)
    books = []
    for p in sorted(books_dir.iterdir()):
        if p.is_file() and p.suffix.lower() == ".pdf":
            books.append({
                "book_id": make_file_id(p.name),
                "title": p.stem,
                "category": "未分类",
                "filename": p.name,
                "added_at": now_iso(),
                "total_pages": None,
            })
    manifest = {"books": books}
    _save_manifest(manifest)
    logger.info("[reading] manifest 初始化，共 %d 本书", len(books))
    return manifest


def _find_book(manifest: dict, book_id: str) -> dict | None:
    return next((b for b in manifest["books"] if b["book_id"] == book_id), None)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/reading/start", summary="上传 PDF 并开始阅读 session")
async def start_reading(
    file: UploadFile = File(...),
    start_page: int = Form(default=1),
    uid: str = Form(default=""),
    auth=Depends(require_scopes("activity")),
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
    auth=Depends(require_scopes("activity")),
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
    auth=Depends(require_scopes("activity")),
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
    auth=Depends(require_scopes("activity")),
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


class ChatRequest(BaseModel):
    session_id: str
    message: str


_CHAT_MAX_MESSAGE_LEN = 1000


@router.post("/reading/close", summary="关闭阅读 session")
async def close_reading(
    body: CloseRequest,
    auth=Depends(require_scopes("activity")),
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
    threshold = _get_activity_meta("reading").memory_policy.summary_threshold
    if threshold is not None and session.current_page > threshold:
        summary_text = await _activity_summary.generate_and_reflow(
            session.uid, char_id, "reading", body.session_id
        )
        if summary_text:
            try:
                book_id = session.file_id
                insights_dir = _get_paths().reading_library_insights_dir(book_id=book_id)
                insights_dir.mkdir(parents=True, exist_ok=True)
                insight_file = insights_dir / f"{body.session_id}.txt"
                insight_file.write_text(summary_text, encoding="utf-8")
                logger.info("[reading] insight saved: book=%s session=%s", book_id, body.session_id)
            except Exception:
                logger.exception("[reading] failed to save insight")
    return {
        "status": "closed",
        "session_id": body.session_id,
        "filename": session.filename,
        "total_pages": session.total_pages,
        "last_page": session.current_page,
        "closed_at": now,
    }


@router.get("/reading/library", summary="列出书库中的书")
async def list_library(auth=Depends(require_scopes("activity"))):
    """从 manifest.json 返回书库列表（含 title / category / total_pages）。"""
    manifest = _migrate_manifest_if_needed()
    books_dir = _get_paths().reading_library_books_dir()
    result = []
    for book in manifest["books"]:
        path = books_dir / book["filename"]
        result.append({**book, "size_bytes": path.stat().st_size if path.exists() else 0})
    return {"books": result}


@router.post("/reading/library/add", summary="上传 PDF 到书库")
async def add_to_library(
    file: UploadFile = File(...),
    auth=Depends(require_scopes("activity")),
):
    """把上传的 PDF 保存到 data/library/books/，并更新 manifest.json。同名文件直接覆盖。"""
    safe_name = _sanitize_filename(file.filename or "upload.pdf")
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="书库仅支持 PDF 文件")

    content = await file.read()
    try:
        pdf_info, _ = extract_pages(content, safe_name)
    except PDFOCRRequired as e:
        raise HTTPException(status_code=422, detail=str(e))
    except PDFFileTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e))
    except PDFReadError as e:
        raise HTTPException(status_code=422, detail=str(e))

    books_dir = _get_paths().reading_library_books_dir()
    books_dir.mkdir(parents=True, exist_ok=True)
    (books_dir / safe_name).write_bytes(content)

    manifest = _migrate_manifest_if_needed()
    # Preserve book_id on re-upload (match by filename)
    match = next((b for b in manifest["books"] if b["filename"] == safe_name), None)
    if match:
        match["total_pages"] = pdf_info.total_pages
        book_id = match["book_id"]
    else:
        book_id = str(uuid.uuid4())
        manifest["books"].append({
            "book_id": book_id,
            "title": Path(safe_name).stem,
            "category": "未分类",
            "filename": safe_name,
            "added_at": now_iso(),
            "total_pages": pdf_info.total_pages,
        })
    _save_manifest(manifest)
    book = _find_book(manifest, book_id)
    logger.info("[reading] add_to_library: file=%r book_id=%s", safe_name, book_id)
    return {**book, "size_bytes": len(content)}


class StartFromLibraryRequest(BaseModel):
    book_id: str
    start_page: int = 1
    uid: str = ""


@router.post("/reading/start_from_library", summary="从书库开始阅读")
async def start_reading_from_library(
    body: StartFromLibraryRequest,
    auth=Depends(require_scopes("activity")),
):
    """从 data/library/books/ 读取文件内容，不需要上传，其余逻辑同 /reading/start。"""
    char_id = _active_char_id()
    resolved_uid = body.uid.strip() or _default_uid()

    manifest = _migrate_manifest_if_needed()
    book_entry = _find_book(manifest, body.book_id)
    if book_entry is None:
        raise HTTPException(status_code=404, detail=f"书库中找不到 book_id={body.book_id!r}")

    books_dir = _get_paths().reading_library_books_dir()
    target = books_dir / book_entry["filename"]
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"书文件不存在: {book_entry['filename']!r}")

    content = target.read_bytes()
    safe_name = target.name
    try:
        pdf_info, pages = extract_pages(content, safe_name)
    except PDFOCRRequired as e:
        raise HTTPException(status_code=422, detail=str(e))
    except PDFFileTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e))
    except PDFReadError as e:
        raise HTTPException(status_code=422, detail=str(e))

    total = pdf_info.total_pages
    start_page = max(1, min(body.start_page, total))

    now = now_iso()
    session = ReadingSession(
        session_id=new_session_id(),
        uid=resolved_uid,
        char_id=char_id,
        file_id=body.book_id,
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
        "[reading] start_from_library: uid=%s char=%s file=%r pages=%d session=%s",
        resolved_uid, char_id, safe_name, total, session.session_id,
    )
    return session.to_dict()


class DeleteBookRequest(BaseModel):
    book_id: str
    with_insights: bool = False


class RenameBookRequest(BaseModel):
    book_id: str
    title: str


class CategorizeBookRequest(BaseModel):
    book_id: str
    category: str


@router.post("/reading/library/delete", summary="从书库删除一本书")
async def delete_from_library(
    body: DeleteBookRequest,
    auth=Depends(require_scopes("activity")),
):
    manifest = _migrate_manifest_if_needed()
    book = _find_book(manifest, body.book_id)
    if book is None:
        raise HTTPException(status_code=404, detail=f"书库中找不到 book_id={body.book_id!r}")

    filename = book["filename"]
    manifest["books"] = [b for b in manifest["books"] if b["book_id"] != body.book_id]
    _save_manifest(manifest)

    file_path = _get_paths().reading_library_books_dir() / filename
    if file_path.exists():
        file_path.unlink()

    if body.with_insights:
        insights_dir = _get_paths().reading_library_insights_dir(book_id=body.book_id)
        if insights_dir.exists():
            shutil.rmtree(insights_dir)

    logger.info("[reading] delete_from_library: book_id=%s file=%r", body.book_id, filename)
    return {"deleted": True, "book_id": body.book_id}


@router.post("/reading/library/rename", summary="修改书的显示名称")
async def rename_book(
    body: RenameBookRequest,
    auth=Depends(require_scopes("activity")),
):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title 不能为空")
    manifest = _migrate_manifest_if_needed()
    book = _find_book(manifest, body.book_id)
    if book is None:
        raise HTTPException(status_code=404, detail=f"书库中找不到 book_id={body.book_id!r}")
    book["title"] = title
    _save_manifest(manifest)
    return book


@router.post("/reading/library/categorize", summary="设置书的分类")
async def categorize_book(
    body: CategorizeBookRequest,
    auth=Depends(require_scopes("activity")),
):
    category = body.category.strip() or "未分类"
    manifest = _migrate_manifest_if_needed()
    book = _find_book(manifest, body.book_id)
    if book is None:
        raise HTTPException(status_code=404, detail=f"书库中找不到 book_id={body.book_id!r}")
    book["category"] = category
    _save_manifest(manifest)
    return book


@router.post("/reading/chat", summary="活动内对话（陪伴聊天）")
async def reading_chat(
    body: ChatRequest,
    auth=Depends(require_scopes("activity")),
):
    """
    活动内对话接口。

    只写 activity transcript，不写 short_term / event_log / user_hidden_state。
    不修改阅读进度（current_page / status）。
    只有 active session 允许聊天。
    page_text 截断后注入 LLM，完整文本不进记忆。
    """
    char_id = _active_char_id()
    _validate_session_id(body.session_id)

    msg = body.message.strip() if body.message else ""
    if not msg:
        raise HTTPException(status_code=422, detail="message 不能为空")
    if len(body.message) > _CHAT_MAX_MESSAGE_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"message 超出 {_CHAT_MAX_MESSAGE_LEN} 字限制",
        )

    session = _require_session(char_id, body.session_id)
    if session.status != "active":
        raise HTTPException(status_code=409, detail=f"session {body.session_id!r} 已关闭，不允许聊天")

    # Load current page text for grounding (may be None if missing)
    page_text = activity_store.load_page(char_id, session.uid, body.session_id, session.current_page)

    reply, control, grounding = await reading_companion.generate_reply(
        char_id=char_id,
        uid=session.uid,
        session_id=body.session_id,
        current_page=session.current_page,
        total_pages=session.total_pages,
        filename=session.filename,
        page_text=page_text,
        user_message=msg,
    )

    return {
        "session_id": body.session_id,
        "reply": reply,
        "control": control,
        "grounding": grounding,
    }
