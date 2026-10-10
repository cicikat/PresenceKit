"""Admin-only, previewed character lifecycle operations."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool

from admin.auth import require_scopes
from core import character_cleanup as cleanup

router = APIRouter()
_operation_lock = asyncio.Lock()


class DeleteRequest(BaseModel):
    include_data: StrictBool = True
    fingerprint: str
    confirm_id: str


def _error(exc):
    if isinstance(exc, cleanup.CleanupConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, (ValueError, TypeError)):
        return HTTPException(422, "身份或清理记录无效")
    return HTTPException(409, "清理未完成，请检查数据权限和清理记录后重试")


@router.get('/character-cleanup')
async def inventory(auth=Depends(require_scopes('admin'))):
    try:
        return {'characters': cleanup.candidates(), 'history': cleanup.history()}
    except (ValueError, OSError) as exc:
        raise _error(exc) from exc


@router.get('/character-cleanup/{char_id}/preview')
async def preview(char_id: str, include_data: bool = True, auth=Depends(require_scopes('admin'))):
    try:
        return cleanup.preview(char_id, include_data)
    except (ValueError, OSError) as exc:
        raise _error(exc) from exc


@router.delete('/character-cleanup/{char_id}')
async def delete(char_id: str, body: DeleteRequest, auth=Depends(require_scopes('admin'))):
    if body.confirm_id != char_id:
        raise HTTPException(422, '确认 ID 与所选角色不一致')
    async with _operation_lock:
        try:
            result = cleanup.remove(char_id, body.include_data, body.fingerprint)
        except (ValueError, OSError) as exc:
            raise _error(exc) from exc
    from admin import audit
    audit.log_event('character_cleanup_deleted', label=getattr(auth, 'label', None), path='/character-cleanup')
    return result


@router.post('/character-cleanup/restore/{operation}')
async def restore(operation: str, auth=Depends(require_scopes('admin'))):
    async with _operation_lock:
        try:
            result = cleanup.restore(operation)
        except (ValueError, OSError, KeyError) as exc:
            raise _error(exc) from exc
    from admin import audit
    audit.log_event('character_cleanup_restored', label=getattr(auth, 'label', None), path='/character-cleanup/restore')
    return result
