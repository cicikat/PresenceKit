"""
花园状态路由
"""

import json

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes
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
async def get_garden_state(auth=Depends(require_scopes("state.read"))):
    return garden_manager.get_state(char_id=_active_char_id())


@router.post("/water", summary="浇水（复用被动浇水工具的 force_water 逻辑）")
async def water_garden_endpoint(auth=Depends(require_scopes("chat"))):
    return garden_manager.force_water(char_id=_active_char_id())
