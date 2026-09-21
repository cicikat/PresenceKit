"""Listening ledger reads and first-party player host (tickets 260 D/E)."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from admin.auth import require_scopes
from core.audio_music_contract import AUDIO_BLOB_BUDGET_BYTES, MAX_INPUT_BYTES

router = APIRouter()


def _uid(uid: str) -> str:
    value = str(uid or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="uid required")
    return value


def _owner_or_config(uid: str) -> str:
    from core.config_loader import get_config

    owner = str(uid or "").strip() or str((get_config().get("scheduler") or {}).get("owner_id") or "")
    if not owner:
        raise HTTPException(status_code=422, detail="uid 未提供且 owner_id 未配置")
    return owner


@router.get("/observability/listening", summary="读取听歌账本元数据（不含注释/历史正文）")
async def listening_observability(
    uid: str = Query(""),
    _auth=Depends(require_scopes("state.read")),
):
    from core.listening_store import metadata_snapshot

    snapshot = metadata_snapshot(_owner_or_config(uid))
    snapshot["notes_omitted"] = True
    snapshot["history_bodies_omitted"] = True
    return snapshot


@router.get("/listening/history", summary="读取听歌 occurrence 与计数")
async def listening_history(
    uid: str,
    char_id: str = "",
    limit: int = Query(50, ge=1, le=200),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.listening_store import history_snapshot

    return history_snapshot(_uid(uid), char_id=char_id or None, limit=limit)


@router.get("/listening/notes", summary="读取角色歌曲注释")
async def listening_notes(
    uid: str,
    char_id: str,
    track_id: str = "",
    _auth=Depends(require_scopes("memory.read")),
):
    from core.listening_store import list_notes, read_note

    owner = _uid(uid)
    if not str(char_id or "").strip():
        raise HTTPException(status_code=422, detail="char_id required")
    if track_id:
        note = read_note(owner, char_id, track_id)
        return {"char_id": char_id, "notes": [] if note is None else [note]}
    return {"char_id": char_id, "notes": list_notes(owner, char_id)}


class HostBindBody(BaseModel):
    uid: str = ""
    host_id: str = Field(..., min_length=1, max_length=80)


class PlayerCommandBody(BaseModel):
    uid: str = ""
    command_id: str
    action: str
    generation: int
    session_id: str = ""
    expected_revision: int | None = None
    args: dict = Field(default_factory=dict)


class PlayerEventBody(BaseModel):
    uid: str = ""
    event: dict


@router.get("/player/state", summary="读取自有播放器状态")
async def player_state(uid: str = Query(""), _auth=Depends(require_scopes("state.read"))):
    from core.player_adapter import capabilities, get_state, music_control_enabled

    owner = _owner_or_config(uid)
    snapshot = get_state(owner)
    snapshot["music_control_enabled"] = music_control_enabled()
    snapshot["capabilities"] = capabilities()
    return snapshot


@router.post("/player/host/bind", summary="绑定自有播放宿主")
async def player_bind(body: HostBindBody, _auth=Depends(require_scopes("admin"))):
    from core.player_adapter import register_host

    return register_host(_owner_or_config(body.uid), host_id=body.host_id)


@router.post("/player/host/disconnect", summary="标记播放宿主离线")
async def player_disconnect(uid: str = Query(""), _auth=Depends(require_scopes("admin"))):
    from core.player_adapter import mark_host_offline

    return mark_host_offline(_owner_or_config(uid))


@router.post("/player/command", summary="向自有播放器投递命令")
async def player_command(body: PlayerCommandBody, _auth=Depends(require_scopes("admin"))):
    from core.player_adapter import dispatch_command

    payload = body.model_dump()
    uid = payload.pop("uid")
    return dispatch_command(_owner_or_config(uid), payload)


@router.post("/player/event", summary="提交真实播放事件")
async def player_event(body: PlayerEventBody, _auth=Depends(require_scopes("admin"))):
    from core.player_adapter import ingest_host_event

    return ingest_host_event(_owner_or_config(body.uid), body.event)


@router.get("/player/library", summary="读取受控曲库元数据")
async def player_library(uid: str = Query(""), _auth=Depends(require_scopes("admin"))):
    from core.listening_store import list_tracks

    tracks = list_tracks(_owner_or_config(uid))
    return {
        "tracks": [
            {
                "track_id": row["track_id"],
                "title": row["title"],
                "author": row.get("author") or "",
                "duration_s": row.get("duration_s"),
                "audio_access": row.get("audio_access"),
                "analysis_status": row.get("analysis_status"),
                "has_blob": str(row.get("audio_ref") or "").startswith("blob:"),
            }
            for row in tracks
        ]
    }


@router.post("/player/tracks", summary="上传受控音频到自有播放器")
async def player_upload_track(
    uid: str = Form(""),
    title: str = Form(""),
    author: str = Form(""),
    file: UploadFile = File(...),
    _auth=Depends(require_scopes("admin")),
):
    from core.listening_store import register_audio_blob, upsert_track

    owner = _owner_or_config(uid)
    data = await file.read(AUDIO_BLOB_BUDGET_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise HTTPException(status_code=413, detail="audio too large")
    filename = file.filename or "track.wav"
    try:
        ref = register_audio_blob(owner, data, filename=filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = title.strip() or filename.rsplit(".", 1)[0]
    source_id = ref.split(":", 1)[1]
    track = upsert_track(
        owner,
        provider="local",
        source_id=source_id,
        title=name,
        author=author,
        audio_ref=ref,
        audio_access="backend_readable",
        analysis_status="unavailable",
    )
    return {"track": track}


@router.get("/player/audio/{track_id}", summary="读取受控音频 blob")
async def player_audio(track_id: str, uid: str = Query(""), _auth=Depends(require_scopes("admin"))):
    from core.listening_store import get_track, resolve_audio_blob

    owner = _owner_or_config(uid)
    track = get_track(owner, track_id)
    if not track:
        raise HTTPException(status_code=404, detail="unknown_track")
    path = resolve_audio_blob(owner, str(track.get("audio_ref") or ""))
    if path is None:
        raise HTTPException(status_code=404, detail="audio_unavailable")
    media = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
        ".opus": "audio/ogg",
        ".aac": "audio/aac",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=path.name)
