"""
花园状态路由
"""

import json

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from admin.auth import require_scopes
from admin.routers._common import resolve_requested_char_id
from core.garden import manager as garden_manager
from core.sandbox import get_paths as _get_paths

router = APIRouter()


def _active_char_id() -> str:
    try:
        raw = json.loads(_get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        cid = (raw.get("active_character") or "").strip()
    except Exception:
        raise HTTPException(status_code=503, detail="active character unavailable")

    if not cid:
        raise HTTPException(status_code=503, detail="active_character missing")

    from core.asset_registry import get_registry
    try:
        get_registry().resolve(cid, "character")
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown character id: {cid!r}")

    return cid


@router.get("/state", summary="获取花园状态")
async def get_garden_state(
    char_id: Optional[str] = Query(default=None, description="角色 id；缺省 = active char"),
    auth=Depends(require_scopes("state.read")),
):
    resolved = resolve_requested_char_id(char_id, fallback=_active_char_id)
    return garden_manager.get_state(char_id=resolved)
