"""Read-only control-center projections for the authenticated admin panel."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes

router = APIRouter()


def _scope() -> tuple[str, str]:
    from core.character_context import selected_character_id_or_none
    from core.owner_config import configured_owner_id

    uid = str(configured_owner_id() or "")
    char_id = str(selected_character_id_or_none() or "")
    if not uid or not char_id:
        raise HTTPException(status_code=409, detail="owner or active character is not configured")
    return uid, char_id


@router.get(
    "/admin/control-center/effective-state",
    summary="读取控制中心全局配置与运行时生效状态",
)
@router.get("/admin/effective-state", include_in_schema=False)
async def get_effective_state(auth=Depends(require_scopes("state.read"))):
    from core.control_center.effective_state import build_global_effective_state

    uid, char_id = _scope()
    return build_global_effective_state(uid, char_id)
