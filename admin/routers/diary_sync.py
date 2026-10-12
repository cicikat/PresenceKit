"""Owner-controlled dated diary mirror sync endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes
from core import diary_mirror

router = APIRouter()
_ALLOWED_KEYS = frozenset({"generation", "entries"})


def _request_body(body: object) -> tuple[object, object]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    if set(body) - _ALLOWED_KEYS:
        raise HTTPException(status_code=422, detail="unknown diary sync field")
    return body.get("generation"), body.get("entries")


def _sync_error(exc: diary_mirror.DiarySyncError) -> HTTPException:
    if exc.code in {"batch_limit", "batch_too_large", "entry_too_large"}:
        return HTTPException(status_code=413, detail=exc.code)
    if exc.code in {"conflict", "mirror_write_failed"}:
        return HTTPException(status_code=409, detail=exc.code)
    return HTTPException(status_code=422, detail=exc.code)


@router.post("/integrations/diary/sync", summary="Apply a bounded dated diary mirror batch")
async def sync_diary(body: dict, char_id: str | None = None, _auth=Depends(require_scopes("diary.sync"))):
    generation, entries = _request_body(body)
    try:
        return await diary_mirror.apply_batch(generation=generation, entries=entries, char_id=char_id)
    except diary_mirror.DiarySyncError as exc:
        raise _sync_error(exc) from exc


@router.get("/integrations/diary/sync/status", summary="Read redacted diary mirror sync status")
async def diary_sync_status(char_id: str | None = None, _auth=Depends(require_scopes("diary.sync"))):
    return diary_mirror.status(char_id=char_id)
