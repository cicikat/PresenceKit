"""Listening / music ledger reads (ticket 260 D)."""
from fastapi import APIRouter, Depends, HTTPException, Query

from admin.auth import require_scopes

router = APIRouter()


def _uid(uid: str) -> str:
    value = str(uid or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="uid required")
    return value


@router.get("/observability/listening", summary="读取听歌账本元数据（不含注释/历史正文）")
async def listening_observability(
    uid: str = Query(""),
    _auth=Depends(require_scopes("state.read")),
):
    from core.config_loader import get_config
    from core.listening_store import metadata_snapshot

    owner = uid.strip() or str((get_config().get("scheduler") or {}).get("owner_id") or "")
    if not owner:
        raise HTTPException(status_code=422, detail="uid 未提供且 owner_id 未配置")
    snapshot = metadata_snapshot(owner)
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
