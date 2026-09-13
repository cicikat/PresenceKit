from fastapi import APIRouter, Depends, HTTPException, Query
from admin.auth import require_scopes

router = APIRouter()


@router.get('/observability/context-continuity', summary='读取资料已读与主动工具结果接续状态')
async def context_continuity_observability(uid: str, char_id: str, _auth=Depends(require_scopes('state.read'))):
    _scope(uid, char_id)
    from core.context_continuity import observability
    return observability(uid, char_id)


def _scope(uid: str, char_id: str):
    from core.memory.scope import MemoryScope
    try:
        return MemoryScope.reality_scope(uid, char_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail={"code": "invalid_scope"}) from None


@router.get("/observability/character-library", summary="读取角色资料库健康摘要")
async def character_library_observability(
    uid: str, char_id: str,
    _auth=Depends(require_scopes("state.read")),
):
    _scope(uid, char_id)
    from core.character_document_library import observability
    return observability(uid, char_id)


@router.delete("/character-library/{document_id}", summary="撤回角色资料库条目")
async def delete_character_library_document(
    document_id: str, uid: str = Query(...), char_id: str = Query(...),
    _auth=Depends(require_scopes("admin")),
):
    _scope(uid, char_id)
    from core.character_document_library import delete
    if not delete(uid, char_id, document_id):
        raise HTTPException(status_code=404, detail="document not found")
    return {"status": "deleted", "document_id": document_id}
