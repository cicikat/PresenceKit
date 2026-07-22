from fastapi import APIRouter, Depends, Query
from admin.auth import require_scopes

router = APIRouter()


@router.get("/observability/api-calls", summary="读取外部 API 调用总账")
async def api_calls(caller: str = "", provider: str = "", limit: int = Query(100, ge=1, le=500), _auth=Depends(require_scopes("state.read"))):
    from core.api_call_log import query
    entries, grouped = query(caller=caller, provider=provider, limit=limit)
    return {"entries": entries, "count": len(entries), "by_provider": grouped}
