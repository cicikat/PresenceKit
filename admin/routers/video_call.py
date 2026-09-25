"""Video-call camera observation. Frames are never persisted by this router."""
from __future__ import annotations

import base64
import binascii
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel

from admin.auth import require_scopes
from core import video_call

router = APIRouter()


def _camera_scope(session_header: str | None, auth) -> tuple[str, str, str]:
    from admin.routers.chat import _owner_media_scope, _session_grant
    label = getattr(auth, "label", "legacy-admin")
    grant = _session_grant(session_header, label)
    uid, char_id = (grant.owner_id, grant.char_id) if grant else _owner_media_scope()
    return uid, char_id, label


@router.get("/video-call/state", summary="视频电话视觉路由有效状态")
async def video_call_state(_auth=Depends(require_scopes("chat"))):
    from core.config_loader import get_config
    return video_call.snapshot(get_config())


@router.get("/observability/video-call", summary="视频电话视觉处理计数")
async def video_call_observability(_auth=Depends(require_scopes("state.read"))):
    from core.config_loader import get_config
    from core.video_call_invite import snapshot as invite_snapshot
    return {**video_call.snapshot(get_config()), "invites": invite_snapshot()}


class VideoCallInviteDecision(BaseModel):
    invite_id: str
    status: str


@router.post("/video-call/invite/respond", summary="响应角色来电")
async def respond_video_call_invite(
    body: VideoCallInviteDecision,
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    auth=Depends(require_scopes("chat")),
):
    from core.video_call_invite import respond
    uid, char_id, _label = _camera_scope(x_presence_session, auth)
    if not respond(uid, char_id, body.invite_id, body.status):
        raise HTTPException(status_code=409, detail="invite_unavailable")
    return {"accepted": True}


@router.post("/video-call/invite/hangup", summary="报告主人挂断已接通视频电话")
async def hangup_video_call_invite(
    body: VideoCallInviteDecision,
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    auth=Depends(require_scopes("chat")),
):
    from core.video_call_invite import hangup
    uid, char_id, _label = _camera_scope(x_presence_session, auth)
    if not hangup(uid, char_id, body.invite_id):
        raise HTTPException(status_code=409, detail="call_unavailable")
    return {"accepted": True}


@router.post("/video-call/close", summary="关闭当前视频电话摄像头观察")
async def close_camera(
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    auth=Depends(require_scopes("chat")),
):
    uid, char_id, label = _camera_scope(x_presence_session, auth)
    video_call.close_camera(uid, char_id, label)
    return {"closed": True}


@router.post("/video-call/camera/poll", summary="领取角色按需查看摄像头的新帧请求")
async def poll_camera(
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    auth=Depends(require_scopes("chat")),
):
    uid, char_id, label = _camera_scope(x_presence_session, auth)
    return video_call.poll_camera(uid, char_id, label)


class CameraResult(BaseModel):
    request_id: str
    frame_base64: str | None = None


@router.post("/video-call/camera/result", summary="回传按需采集的摄像头新帧")
async def camera_result(
    body: CameraResult,
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    auth=Depends(require_scopes("chat")),
):
    uid, char_id, label = _camera_scope(x_presence_session, auth)
    if body.frame_base64 and len(body.frame_base64) > video_call.MAX_FRAME_BYTES * 2:
        raise HTTPException(status_code=422, detail="frame_size_invalid")
    try:
        frame = base64.b64decode(body.frame_base64, validate=True) if body.frame_base64 else None
        accepted = video_call.accept_camera_frame(uid, char_id, label, body.request_id, frame)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid_camera_frame") from exc
    if not accepted:
        raise HTTPException(status_code=409, detail="camera_request_unavailable")
    return {"accepted": True}


@router.post("/video-call/observe", summary="观察当前摄像头 JPEG 帧")
async def observe_camera_frame(
    file: UploadFile = File(...),
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    auth=Depends(require_scopes("chat")),
):
    uid, char_id, label = _camera_scope(x_presence_session, auth)
    frame = await file.read(video_call.MAX_FRAME_BYTES + 1)
    try:
        return await video_call.observe(frame, uid=uid, char_id=char_id, token_label=label)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
