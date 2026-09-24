"""Video-call camera observation. Frames are never persisted by this router."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile

from admin.auth import require_scopes
from core import video_call

router = APIRouter()


@router.get("/video-call/state", summary="视频电话视觉路由有效状态")
async def video_call_state(_auth=Depends(require_scopes("chat"))):
    from core.config_loader import get_config
    return video_call.snapshot(get_config())


@router.get("/observability/video-call", summary="视频电话视觉处理计数")
async def video_call_observability(_auth=Depends(require_scopes("state.read"))):
    from core.config_loader import get_config
    return video_call.snapshot(get_config())


@router.post("/video-call/observe", summary="观察当前摄像头 JPEG 帧")
async def observe_camera_frame(
    file: UploadFile = File(...),
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    auth=Depends(require_scopes("chat")),
):
    from admin.routers.chat import _owner_media_scope, _session_grant

    label = getattr(auth, "label", "legacy-admin")
    grant = _session_grant(x_presence_session, label)
    uid, char_id = (grant.owner_id, grant.char_id) if grant else _owner_media_scope()
    frame = await file.read(video_call.MAX_FRAME_BYTES + 1)
    try:
        return await video_call.observe(frame, uid=uid, char_id=char_id, token_label=label)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
