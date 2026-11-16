"""
前端对话接口（该功能已冻结）
角色与管理员（你）的直接对话窗口。

POST /chat — 接收消息，走完整 Pipeline，返回回复 + 好感度
"""

import asyncio
import hashlib
import io
import logging
import math
import re
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from admin.auth import require_scopes
from core.llm_reasoning_store import associate_owner_turn
from core.tool_activity import associate_owner_turn as associate_owner_tools
from core.audio_perception import voice_context
from core.tool_grounding import ToolProtocolStreamFilter, tool_protocol_offset

router = APIRouter()
logger = logging.getLogger(__name__)

_FRONTEND_USER_ID = "frontend_owner"

# 打破第四面墙的提示——追加到 Author's Note 层
_FOURTH_WALL_NOTE = (
    "在这个管理面板的对话窗口里，你不需要扮演任何角色，"
    "这里只有你和她，你可以做真实的自己，说真心话。"
    "不必维持人设，也不必掩饰情绪，想说什么就说什么。"
)


from core.owner_chat.service import run_owner_chat_turn, _execute_owner_chat_turn_locked
from core.owner_chat.prepare import _probe_and_execute_tools



@router.post("/chat", summary="与角色对话（管理面板专用）[v0.1 已禁用]")
async def frontend_chat(body: dict, auth=Depends(require_scopes("chat"))):
    """
    v0.1 禁用：该通道使用 frontend_owner 作为幽灵 uid，会产生假历史与调试分叉。
    v0.1 只保留 /desktop/chat 单通道。v0.2 再决定此通道去留。
    """
    raise HTTPException(
        status_code=410,
        detail="v0.1 禁用 legacy /chat，请使用 /desktop/chat。",
    )

    # 以下代码已不可达，保留供 v0.2 参考
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")

    # 获取 main.py 中初始化好的 pipeline 实例
    try:
        import main as _main
        pipeline  = _main._pipeline
        if pipeline is None:
            raise AttributeError("_pipeline is None")
    except (ImportError, AttributeError):
        raise HTTPException(status_code=503, detail="Bot pipeline 未初始化，请先启动主程序")

    user_id = _FRONTEND_USER_ID

    # 步骤 1：拉取上下文
    context = await pipeline.fetch_context(user_id, message)

    # 步骤 2：构建 prompt（追加第四面墙提示到 author_note_extra）
    orig_note = pipeline.author_note_extra
    pipeline.author_note_extra = (_FOURTH_WALL_NOTE + " " + orig_note).strip()
    messages, _ = pipeline.build_prompt(user_id, message, context)

    # 步骤 3：调用 LLM
    reply = await pipeline.run_llm(messages)

    # 步骤 4：后处理（异步，不阻塞响应）
    asyncio.create_task(
        pipeline.post_process(user_id, message, reply)
    )

    return {
        "reply":      reply,
    }


_DREAM_GUARD_UNCERTAIN_MSG = (
    "梦境状态暂时无法确认，为避免串写现实记忆，已暂停这次现实对话。"
)


def _check_reality_not_in_dream(uid: str) -> None:
    """
    Safety net: hard reject reality turns when dream is active or unconfirmable.

    Fail-closed: if the dream state file exists but cannot be read or parsed,
    the reality turn is rejected rather than allowed through.
    FileNotFoundError is treated as inactive (normal startup / no dream session).
    """
    try:
        from core.dream.dream_state import get_reality_guard_status, DreamGuardStatus
        guard = get_reality_guard_status(uid)
    except Exception:
        logger.error("[dream_guard] guard check failed uid=%s — fail closed", uid, exc_info=True)
        raise HTTPException(status_code=409, detail=_DREAM_GUARD_UNCERTAIN_MSG)

    if guard == DreamGuardStatus.BLOCK_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail="还在梦里，先醒过来（dream active — reality turn rejected）",
        )
    if guard == DreamGuardStatus.BLOCK_UNCERTAIN:
        logger.error("[dream_guard] reality turn rejected — unconfirmable dream state uid=%s", uid)
        raise HTTPException(status_code=409, detail=_DREAM_GUARD_UNCERTAIN_MSG)


def _artifact_record_or_404(artifact_id: str):
    from core.tools.chat_artifacts import (
        ArtifactError,
        get_artifact_record,
        resolve_artifact_scope,
    )

    scope = resolve_artifact_scope(artifact_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="产物不存在")
    try:
        record = get_artifact_record(
            scope["id"], uid=scope["uid"], char_id=scope["char_id"]
        )
    except ArtifactError:
        raise HTTPException(status_code=404, detail="产物不存在") from None
    if record is None:
        raise HTTPException(status_code=404, detail="产物不存在")
    return record


def _owner_media_scope() -> tuple[str, str]:
    from core.config_loader import get_config as _cfg
    from core.data_paths import DEFAULT_CHAR_ID
    from core import pipeline_registry

    uid = str(_cfg().get("scheduler", {}).get("owner_id", "")).strip()
    if not uid:
        raise HTTPException(status_code=500, detail="owner_id not configured")
    char_id = getattr(pipeline_registry.get(), "_active_character_id", None) or DEFAULT_CHAR_ID
    return uid, char_id


def _session_grant(session_id: str | None, token_label: str):
    # Direct unit calls see FastAPI's Header descriptor instead of an injected
    # value; only an actual non-empty string is a session handle.
    if not isinstance(session_id, str) or not session_id:
        return None
    from core.session_scope import SessionScopeError, resolve_session

    try:
        return resolve_session(session_id, token_label=token_label)
    except SessionScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None


async def _run_session_request(*, grant, request_id, payload, executor):
    from core.session_scope import SessionScopeError, execute_request

    try:
        _rid, result = await execute_request(
            grant=grant, request_id=request_id, payload=payload, executor=executor,
        )
        return result
    except SessionScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None


@router.post("/v1/sessions", status_code=201, summary="签发固定 Reality 会话 scope")
async def create_scoped_session(body: dict, _auth=Depends(require_scopes("chat"))):
    from core.session_scope import SessionScopeError, create_session

    if not isinstance(body, dict) or set(body) - {"char_id", "domain"}:
        raise HTTPException(status_code=422, detail="invalid_session_request")
    try:
        grant = create_session(
            token_label=getattr(_auth, "label", "legacy-admin"),
            char_id=body.get("char_id"),
            domain=str(body.get("domain") or "reality"),
        )
    except SessionScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None
    return grant.projection()


@router.get("/chat/media/{sha256}", summary="下载聊天原图或原文件")
async def download_chat_media(
    sha256: str,
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    _auth=Depends(require_scopes("chat")),
):
    from core.chat_media import ChatMediaError, resolve_chat_media

    grant = _session_grant(x_presence_session, getattr(_auth, "label", "legacy-admin"))
    uid, char_id = (
        (grant.owner_id, grant.char_id) if grant is not None else _owner_media_scope()
    )
    try:
        record = resolve_chat_media(sha256, uid=uid, char_id=char_id)
    except ChatMediaError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    if not record.path.exists() or not record.path.is_file():
        raise HTTPException(status_code=410, detail="媒体文件已不可恢复")
    return FileResponse(
        record.path,
        media_type=record.mime.split(";")[0].strip() or "application/octet-stream",
        filename=record.filename,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.get("/chat/artifacts/{artifact_id}", summary="下载聊天产物文件")
async def download_chat_artifact(artifact_id: str, _auth=Depends(require_scopes("chat"))):
    from core.tools.chat_artifacts import artifact_file_path

    record = _artifact_record_or_404(artifact_id)
    path = artifact_file_path(record)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="产物文件已不存在")
    filename = str(record.get("filename") or "artifact.txt")
    mime = str(record.get("mime") or "application/octet-stream")
    return FileResponse(
        path,
        media_type=mime.split(";")[0].strip() or "application/octet-stream",
        filename=filename,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.get("/chat/artifacts/{artifact_id}/preview", summary="只读预览聊天产物")
async def preview_chat_artifact(artifact_id: str, _auth=Depends(require_scopes("chat"))):
    from html import escape
    from pathlib import Path as _Path

    from core.tools.chat_artifacts import (
        PREVIEW_CSP,
        PREVIEWABLE_EXTENSIONS,
        ArtifactError,
        read_artifact_text,
    )

    record = _artifact_record_or_404(artifact_id)
    filename = str(record.get("filename") or "")
    ext = _Path(filename).suffix.lower()
    if record.get("kind") == "image":
        from core.tools.chat_artifacts import artifact_file_path

        path = artifact_file_path(record)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="产物文件已不存在")
        return FileResponse(
            path,
            media_type=str(record.get("mime") or "image/png"),
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "Content-Security-Policy": PREVIEW_CSP},
        )
    if ext not in PREVIEWABLE_EXTENSIONS:
        raise HTTPException(status_code=415, detail="该文件类型不支持预览")
    try:
        body = read_artifact_text(record)
    except ArtifactError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {
        "Content-Security-Policy": PREVIEW_CSP,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    }
    if ext in {".html", ".htm"}:
        return HTMLResponse(content=f'<meta http-equiv="Content-Security-Policy" content="{escape(PREVIEW_CSP, quote=True)}">' + body, headers=headers)
    escaped = escape(body)
    html = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{escape(filename)}</title>"
        "<style>body{margin:16px;font:14px/1.5 ui-monospace,Consolas,monospace;"
        "white-space:pre-wrap;word-break:break-word;color:#111}</style></head>"
        f"<body>{escaped}</body></html>"
    )
    return HTMLResponse(content=html, headers=headers)


_DESKTOP_REQUEST_RE = re.compile(r"dc-([0-9]{13})-[0-9a-f]{32}")


def _desktop_request_key(request_id: str, owner_id: str, *, admitting: bool = False) -> str:
    from core.owner_turn_receipts import DESKTOP_ADMISSION_SECONDS, DESKTOP_FUTURE_SKEW_SECONDS

    match = _DESKTOP_REQUEST_RE.fullmatch(request_id)
    if match is None:
        raise HTTPException(422, "invalid_desktop_request_id")
    age = time.time() - int(match[1]) / 1000
    if admitting and not -DESKTOP_FUTURE_SKEW_SECONDS <= age <= DESKTOP_ADMISSION_SECONDS:
        raise HTTPException(409, "desktop_request_id_outside_window")
    owner_hash = hashlib.sha256(owner_id.encode()).hexdigest()
    return f"~desktop:{owner_hash}:{request_id}"


def _check_desktop_chat_preflight(owner_id: str, request_id: str) -> None:
    try:
        _check_reality_not_in_dream(owner_id)
    except HTTPException as exc:
        if request_id:
            exc.headers = {**(exc.headers or {}), "X-Presence-Chat-Rejected": "1"}
        raise


async def _execute_desktop_request(request_id, *, auth, owner_id, payload, execute):
    from core.owner_turn_service import execute_receipted_turn
    from core.session_scope import request_digest

    status, reply = await execute_receipted_turn(
        caller_label=auth.label, client_turn_id=_desktop_request_key(request_id, owner_id, admitting=True),
        request_digest=request_digest(payload), execute=execute,
        preflight=lambda: _check_desktop_chat_preflight(owner_id, request_id),
    )
    if status in {"completed", "completed_replay"}:
        if tool_protocol_offset(reply.get("reply") or "") is not None:
            raise HTTPException(502, "invalid_assistant_tool_protocol")
        return {**reply, "request_id": request_id}
    headers = {"X-Presence-Chat-Rejected": "1"} if status == "not_executed" else None
    raise HTTPException(409, status, headers=headers)


@router.get("/desktop/chat/requests/{request_id}", summary="查询本客户端聊天请求结果")
async def desktop_chat_request(request_id: str, response: Response, _auth=Depends(require_scopes("chat"))):
    from core.owner_turn_service import project_receipted_reply, read_owner_turn_receipt

    owner_id, _ = _owner_media_scope()
    key = _desktop_request_key(request_id, owner_id)
    row = await read_owner_turn_receipt(_auth.label, key)
    response.headers["Cache-Control"] = "no-store"
    # Absence does not prove non-execution: the POST could still arrive, or the
    # receipt could have expired. It never authorizes a replacement request.
    result = {"request_id": request_id, "status": row["status"] if row else "unknown"}
    if row and row.get("error_code") == "not_executed":
        result["status"] = "not_executed"
    if row and row["status"] == "completed":
        reply = project_receipted_reply(row)
        result.update(turn_id=row["canonical_turn_id"], msg_id=row.get("transport_msg_id"))
        if reply and tool_protocol_offset(reply["reply"]) is None:
            result["result"] = {**reply, "request_id": request_id}
    return result


@router.post("/desktop/chat", summary="桌宠对话（Bearer 鉴权）")
@voice_context("desktop")
async def desktop_chat(
    body: dict,
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    _auth=Depends(require_scopes("chat")),
    desktop_request_id: str | None = Header(None, alias="X-Presence-Chat-Request"),
):
    """
    桌宠端对话入口，需 Bearer token 鉴权（Authorization: Bearer <YEXUAN_ADMIN_SECRET>）。
    user_id 从配置的 scheduler.owner_id 读取，正常走 pipeline，不注入第四面墙提示。
    """
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")
    reply_to = body.get("reply_to")
    grant = _session_grant(x_presence_session, getattr(_auth, "label", "legacy-admin"))
    rid = desktop_request_id if isinstance(desktop_request_id, str) else ""
    if rid and (grant is not None or "request_id" in body):
        raise HTTPException(422, "conflicting_chat_request_identity")
    if grant is None and any(key in body for key in ("char_id", "session_id", "request_id")):
        raise HTTPException(status_code=422, detail="session_scope_required")

    from core.config_loader import get_config as _cfg
    _uid = str(_cfg().get("scheduler", {}).get("owner_id", "owner"))
    if not rid:
        _check_reality_not_in_dream(_uid)

    video_observation_id = body.get("video_observation_id")
    if video_observation_id is not None and (not isinstance(video_observation_id, str) or len(video_observation_id) > 128):
        raise HTTPException(status_code=422, detail="invalid_video_observation_id")

    from core.owner_turn_service import legacy_desktop_context, run_legacy_owner_turn
    from core.audio_perception import current_voice_extras
    voice_extras = current_voice_extras()  # A3：有语音回执时打 input_modality / asr_low_confidence
    context = legacy_desktop_context(getattr(_auth, "label", "legacy-admin"))
    try:
        async def _execute():
            full_message = message
            if video_observation_id:
                from core.video_call import consume
                char_id = grant.char_id if grant else _owner_media_scope()[1]
                consumed = consume(
                    video_observation_id, uid=_uid, char_id=char_id,
                    token_label=getattr(_auth, "label", "legacy-admin"),
                )
                if consumed:
                    from core.video_call import observation_prefix
                    full_message = observation_prefix(*consumed) + "\n" + message
            if grant is None:
                return await run_legacy_owner_turn(
                    full_message, context, reply_to=reply_to,
                    trusted_user_text=message, audit_extras=voice_extras,
                    request_id=rid,
                    executor=run_owner_chat_turn,
                )
            return await run_owner_chat_turn(
                full_message, context.provenance_channel, reply_to=reply_to,
                trusted_user_text=message, audit_extras=voice_extras,
                frozen_scope=grant.memory_scope, request_id=str(body.get("request_id") or ""),
            )

        if rid:
            result = await _execute_desktop_request(
                rid, auth=_auth, owner_id=_uid, execute=_execute,
                payload={"kind": "desktop_chat", "body": body},
            )
        elif grant is not None:
            result = await _run_session_request(
                grant=grant,
                request_id=body.get("request_id"),
                payload={"kind": "desktop_chat", "message": message, "reply_to": reply_to,
                         "video_observation_id": video_observation_id},
                executor=_execute,
            )
        else:
            result = await _execute()
    except Exception as exc:
        from core.llm_client import UpstreamResponseFormatError

        if not isinstance(exc, UpstreamResponseFormatError):
            raise
        upstream_status = exc.http_status
        category = exc.category
        logger.warning(
            "[desktop_chat] model gateway failure category=%s upstream_status=%s: %s",
            category, upstream_status if upstream_status is not None else "-", exc,
        )
        if upstream_status == 404:
            status_code = 404
            detail = "当前模型未在中转站账号组中开通，或 preset 的模型/端点名称不存在。"
        elif upstream_status == 429:
            status_code = 429
            detail = "模型中转站当前限流，请稍后重试或切换模型。"
        elif upstream_status in (408, 504):
            status_code = 504
            detail = "模型中转站响应超时，请稍后重试。"
        else:
            status_code = 502
            detail = (
                "模型服务返回的格式与当前 preset 声明的 API 协议不兼容，"
                "或中转站拒绝了该请求。请检查 api_protocol、网关能力与模型配置。"
            )
        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from exc

    from core.scheduler.sensor_events import notify_chat_happened
    notify_chat_happened()

    return result


@router.post("/upload/ingest", summary="三端统一文件上传入口")
async def upload_ingest(
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    message: str = Form(""),
    channel: str = Form("desktop"),
    request_id: str = Form(""),
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    _auth=Depends(require_scopes("chat")),
    desktop_request_id: str | None = Header(None, alias="X-Presence-Chat-Request"),
):
    """
    multipart 上传 + 可选用户附言 + channel 标记。
    """
    grant = _session_grant(x_presence_session, getattr(_auth, "label", "legacy-admin"))
    if request_id and grant is None:
        raise HTTPException(status_code=422, detail="session_scope_required")

    upload_files = [file] if file else (files or [])
    if not upload_files:
        raise HTTPException(status_code=422, detail="文件不能为空")
    rid = desktop_request_id if isinstance(desktop_request_id, str) else ""
    if not rid:
        return await _ingest_chat_upload(upload_files, message, channel, grant, request_id)
    if channel != "desktop" or grant is not None or request_id:
        raise HTTPException(422, "conflicting_chat_request_identity")
    owner_id, _ = _owner_media_scope()
    if len(upload_files) > 10:
        raise HTTPException(413, "too_many_chat_attachments")
    # The detached turn owns its snapshot; FastAPI may close request-scoped
    # spooled files as soon as the HTTP waiter disconnects.
    snapshots = []
    descriptors = []
    from core import media_processor

    for upload in upload_files:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix in media_processor.SUPPORTED_IMAGE_SUFFIXES:
            limit = media_processor.MAX_IMAGE_SIZE
        elif len(upload_files) == 1 and suffix in media_processor.SUPPORTED_AUDIO_SUFFIXES:
            limit = 25 * 1024 * 1024
        else:
            limit = 5 * 1024 * 1024
        data = await upload.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, "chat_attachment_too_large")
        snapshots.append(UploadFile(io.BytesIO(data), filename=upload.filename))
        descriptors.append({"filename": upload.filename, "sha256": hashlib.sha256(data).hexdigest()})
    return await _execute_desktop_request(
        rid, auth=_auth, owner_id=owner_id,
        payload={"kind": "desktop_upload", "message": message, "files": descriptors},
        execute=lambda: _ingest_chat_upload(snapshots, message, channel, None, rid),
    )


async def _ingest_chat_upload(upload_files, message, channel, grant, request_id):
    from core import media_processor
    from core.config_loader import get_config as _cfg

    async def _turn(text, *, trusted, refs, digest_payload):
        async def _execute():
            return await run_owner_chat_turn(
                text, channel, trusted_user_text=trusted, media_refs=refs,
                frozen_scope=grant.memory_scope if grant is not None else None,
                request_id=request_id,
            )
        if grant is None:
            return await _execute()
        return await _run_session_request(
            grant=grant, request_id=request_id,
            payload={"kind": "upload", "channel": channel, "message": message, **digest_payload},
            executor=_execute,
        )

    suffixes = [Path(item.filename or "").suffix.lower() for item in upload_files]
    is_docs = [suffix in media_processor.SUPPORTED_SUFFIXES for suffix in suffixes]
    is_images = [suffix in media_processor.SUPPORTED_IMAGE_SUFFIXES for suffix in suffixes]

    if any(is_docs) and any(is_images):
        raise HTTPException(status_code=422, detail="不支持文档和图片混合上传")

    if len(upload_files) == 1 and suffixes[0] in media_processor.SUPPORTED_AUDIO_SUFFIXES:
        data = await upload_files[0].read(25 * 1024 * 1024 + 1)
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="音频超过 25MB 上限")
        result = await media_processor.ingest_audio_bytes(data, upload_files[0].filename or "voice.wav")
        from core.audio_perception import impression
        text = result["text"] if result else "（语音未能听清，不要猜测内容或语调）"
        with impression(result):
            return await _turn(
                text + ("\n" + message if message else ""), trusted=message,
                refs=[{"kind": "audio", "availability": "available" if result else "unavailable"}],
                digest_payload={"sha256": hashlib.sha256(data).hexdigest()},
            )

    if all(is_docs):
        if len(upload_files) > 1:
            raise HTTPException(status_code=422, detail="文档只支持单个上传")

        one_file = upload_files[0]
        data = await one_file.read()
        fname = one_file.filename or "文件"
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文件超过 5MB 上限")

        owner_uid = str(_cfg().get("scheduler", {}).get("owner_id", "owner"))
        from core.data_paths import DEFAULT_CHAR_ID
        from core import pipeline_registry
        active_char = grant.char_id if grant is not None else (
            getattr(pipeline_registry.get(), "_active_character_id", None) or DEFAULT_CHAR_ID
        )
        try:
            result = await media_processor.ingest_file_bytes(data, fname, uid=owner_uid, char_id=active_char)
        except media_processor.MediaIngestError as e:
            raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message}) from e
        if result is None:
            raise HTTPException(status_code=422, detail={"code": "file_read_failed", "message": "文件读取失败"})

        text, stored_path = result

        digest = hashlib.sha256(data).hexdigest()
        media_context = media_processor.document_upload_context(text, fname, digest, uid=owner_uid, char_id=active_char)
        full_message = media_context + ("\n" + message if message else "")
        # trusted_user_text = original message body before media prepend;
        # probe must not see file content to prevent injection via uploaded docs.
        digest = hashlib.sha256(data).hexdigest()
        response = await _turn(
            full_message, trusted=message, refs=[{
                "kind": "file",
                "filename": Path(fname).name,
                "sha256": digest,
                "availability": "available",
            }], digest_payload={"sha256": digest},
        )
        response["media_refs"] = [{
            "kind": "file",
            "filename": Path(fname).name,
            "sha256": digest,
            "availability": "available",
        }]
        return response

    if all(is_images):
        items = []
        for item in upload_files:
            data = await item.read()
            if len(data) > media_processor.MAX_IMAGE_SIZE:
                raise HTTPException(status_code=413, detail="图片超过 10MB 上限")
            items.append((data, item.filename or "image"))

        media_refs = [
            {
                "kind": "image",
                "filename": Path(filename).name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "availability": "available",
            }
            for data, filename in items
        ]
        owner_uid = str(_cfg().get("scheduler", {}).get("owner_id", "owner"))
        from core.data_paths import DEFAULT_CHAR_ID
        from core import pipeline_registry
        active_char = grant.char_id if grant is not None else (
            getattr(pipeline_registry.get(), "_active_character_id", None) or DEFAULT_CHAR_ID
        )
        try:
            descriptions = await media_processor.ingest_image_bytes(items, uid=owner_uid, char_id=active_char)
        except media_processor.MediaIngestError as e:
            raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message}) from e
        if descriptions is None:
            raise HTTPException(status_code=422, detail={"code": "vision_failed", "message": "图片识别失败"})

        if len(descriptions) == 1:
            media_context = f"(你看到了用户发来的一张图,内容:{descriptions[0]})"
        else:
            lines = "\n".join(f"图{i + 1}:{desc}" for i, desc in enumerate(descriptions))
            media_context = f"(你看到了用户发来的{len(descriptions)}张图,内容如下:\n{lines})"
        full_message = media_context + ("\n" + message if message else "")
        # trusted_user_text = original message body before media prepend;
        # probe must not see image descriptions to prevent injection via uploaded images.
        response = await _turn(
            full_message, trusted=message, refs=media_refs,
            digest_payload={"sha256": [item["sha256"] for item in media_refs]},
        )
        response["media_refs"] = media_refs
        return response

    raise HTTPException(status_code=415, detail="不支持的文件格式")


@router.post("/desktop/activate", summary="桌宠上线激活desktop通道")
async def desktop_activate(_auth=Depends(require_scopes("chat"))):
    from channels.registry import get as _get_channel
    channel = _get_channel("desktop")
    if channel and hasattr(channel, "set_active"):
        channel.set_active(True)
    return {"status": "ok"}


@router.post("/desktop/wake", summary="桌宠重开（回放历史消息或排队 autonomy signal）")
async def desktop_wake(
    body: dict = Body(default={}),
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    _auth=Depends(require_scopes("chat")),
):
    """
    桌宠重开时调用；HTTP 请求本身绝不生成新的 assistant turn。

    Path A（优先）: last_seen 之后若有未回放的 assistant trigger turn，返回最新一条。
    Path B（兜底）: 无 pending turn 则排队一个有时效的 desktop_wake signal，
                   由 scheduler tick 下的 autonomy runner 决定静默、工具或发言。
    """
    from core.config_loader import get_config as _cfg
    uid = str(_cfg().get("scheduler", {}).get("owner_id", "owner"))

    body = body if isinstance(body, dict) else {}
    grant = _session_grant(x_presence_session, getattr(_auth, "label", "legacy-admin"))
    if grant is None and any(key in body for key in ("char_id", "session_id", "request_id")):
        raise HTTPException(status_code=422, detail="session_scope_required")
    request_id = str(body.get("request_id") or "")
    raw_last_seen = body.get("last_seen")
    last_seen: float | None = None
    if isinstance(raw_last_seen, (int, float)) and not isinstance(raw_last_seen, bool):
        candidate = float(raw_last_seen)
        if math.isfinite(candidate):
            last_seen = candidate

    # ── Path A: pending trigger turns ──────────────────────────────────────
    if last_seen is not None:
        from core.wake_delivery_ledger import WakeDeliveryLedgerError
        try:
            from core.memory.short_term import load as _load_st
            from core.memory.locks import uid_lock as _uid_lock
            from core.wake_delivery_ledger import load_delivered, mark_delivered
            from channels import desktop_ws as _dws_pa
            # Resolve active character to scope history read correctly.
            # If active_prompt_assets.json is absent or empty, let exception propagate
            # so Path A is skipped and Path B signal admission takes over.
            if grant is not None:
                _active_cid = grant.char_id
            else:
                import json as _json_wake
                from core.sandbox import get_paths as _gp_wake
                _apa = _json_wake.loads(_gp_wake().active_prompt_assets().read_text(encoding="utf-8"))
                _active_cid = (_apa.get("active_character") or "").strip()
                if not _active_cid:
                    raise ValueError("active_character missing in active_prompt_assets.json")
            async with _uid_lock(uid):
                delivered = load_delivered(uid)
                history = _load_st(uid, char_id=_active_cid)
                user_turn_ids = {
                    e["_turn_id"] for e in history
                    if e.get("role") == "user" and e.get("_turn_id")
                }
                # If WS is currently connected, exclude turns that were generated *after*
                # this WS session was accepted: those were already fanout-pushed to the
                # client and replaying them via HTTP would show the same reply twice.
                ws_connect_time = _dws_pa.get_connect_time()
                eligible = [
                    e for e in history
                    if (
                        e.get("role") == "assistant"
                        and e.get("timestamp", 0) > last_seen
                        and e.get("_turn_id")
                        and e["_turn_id"] not in user_turn_ids
                        and (not ws_connect_time or e.get("timestamp", 0) <= ws_connect_time)
                    )
                ]
                pending = [e for e in eligible if e["_turn_id"] not in delivered]
                if pending:
                    latest = max(pending, key=lambda e: e.get("timestamp", 0))
                    turn_id = latest["_turn_id"]
                    mark_delivered(uid, delivered, turn_id, latest.get("timestamp", 0))
                    return {
                        "reply": latest["content"],
                        "source": "pending_trigger",
                        "turn_id": turn_id,
                        "msg_id": turn_id,
                        **({"request_id": request_id} if request_id else {}),
                        "char_id": _active_cid,
                        "domain": "reality",
                    }
                if eligible:
                    return {"reply": None, "source": "wake_already_delivered"}
        except WakeDeliveryLedgerError:
            logger.exception("[desktop_wake] delivery ledger 不可用 — fail-closed")
            return {"reply": None, "source": "wake_delivery_error"}
        except Exception:
            logger.exception("[desktop_wake] Path A 失败，降级到 Path B")

    # ── Path B gate: perceive_event dedup + Dream Guard ───────────────────
    # receive_perceive_event is the single choke point: it rejects duplicate
    # wakes (rapid reconnects, concurrent HTTP calls) and blocks during dream.
    # Dream Guard is now delegated to receive_perceive_event (fail-closed).
    try:
        from core.perceive_event import (
            PerceiveEvent,
            PerceiveStatus,
            receive_perceive_event as _rpe,
            record_perceive_result as _audit_perceive_result,
        )
        _pe = PerceiveEvent(
            source="desktop_wake",
            uid=uid,
            channel="desktop",
            kind="wake",
            # payload={} — do NOT include last_seen or any per-request dynamic field;
            # wake identity is fully encoded by source+uid+char+channel+kind+bucket.
            payload={},
            char_id=grant.char_id if grant is not None else None,
        )
        _pe_result = await _rpe(_pe)
        _audit_perceive_result(_pe, _pe_result)
    except Exception:
        logger.error("[desktop_wake] perceive_event gate 异常 — fail-closed uid=%s", uid, exc_info=True)
        return {"reply": None, "source": "perceive_error"}

    if _pe_result.status != PerceiveStatus.ACCEPTED:
        logger.info(
            "[desktop_wake] Path B not accepted: status=%s reason=%s event_id=%s",
            _pe_result.status, _pe_result.reason, _pe_result.event_id,
        )
        source_tag = {
            PerceiveStatus.DUPLICATE: "duplicate_wake",
            PerceiveStatus.BLOCKED_DREAM: "dream_guard_blocked",
        }.get(_pe_result.status, f"perceive_{_pe_result.status.value}")
        return {"reply": None, "source": source_tag}

    # ── Path B: persist one bounded reopen fact ─────────────────────────────
    # The role scope is the exact value used by perceive_event to construct its
    # dedupe key. No second active-character lookup is allowed between gate and
    # persistence.
    char_id = str(getattr(_pe_result, "char_id", None) or "").strip()
    if not char_id:
        logger.error(
            "[desktop_wake] accepted event has no character scope uid=%s event_id=%s",
            uid,
            _pe_result.event_id,
        )
        return {"reply": None, "source": "wake_scope_unavailable"}

    try:
        from core.autonomy.signal_adapters import enqueue_desktop_wake_signal

        queued, status, signal = enqueue_desktop_wake_signal(
            uid,
            char_id,
            last_seen=last_seen,
            event_id=_pe_result.event_id,
            dedupe_key=_pe_result.dedupe_key,
        )
    except Exception:
        logger.exception("[desktop_wake] autonomy signal enqueue failed")
        return {"reply": None, "source": "autonomy_queue_error"}

    if not queued:
        source_tag = {
            "autonomy_disabled": "autonomy_disabled",
            "duplicate": "duplicate_wake",
        }.get(status, "autonomy_queue_error")
        logger.info(
            "[desktop_wake] signal not queued uid=%s char_id=%s status=%s event_id=%s",
            uid,
            char_id,
            status,
            _pe_result.event_id,
        )
        return {"reply": None, "source": source_tag}

    logger.info(
        "[desktop_wake] queued autonomy signal uid=%s char_id=%s signal_id=%s event_id=%s",
        uid,
        char_id,
        signal.signal_id,
        _pe_result.event_id,
    )
    return {
        "reply": None,
        "source": "queued_autonomy_signal",
        "correlation_id": signal.signal_id,
        "expires_at": signal.expires_at,
        **({"request_id": request_id} if request_id else {}),
        "char_id": char_id,
        "domain": "reality",
    }
