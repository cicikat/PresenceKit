"""Admin controls for character-scoped Self Capability P0."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes

router = APIRouter()


def _scope() -> tuple[str, str]:
    from core.character_context import selected_character_id_or_none
    from core.owner_config import configured_owner_id

    uid, char_id = configured_owner_id(), selected_character_id_or_none()
    if not uid or not char_id:
        raise HTTPException(status_code=409, detail="owner or active character is not configured")
    return str(uid), str(char_id)


def _result(result):
    if not result.ok:
        raise HTTPException(status_code=409 if result.code == "revision_conflict" else 422, detail={"code": result.code, "revision": result.revision})
    return {"ok": True, "code": result.code, "revision": result.revision, "value": result.value}


@router.get("/admin/self-management")
async def get_self_management(auth=Depends(require_scopes("state.read"))):
    from core.self_management.service import view
    uid, char_id = _scope()
    return view(uid, char_id)


@router.get("/admin/self-management/policy")
async def get_self_management_policy(auth=Depends(require_scopes("state.read"))):
    from core.self_management.registry import policy_matrix
    return {"policy": policy_matrix()}


@router.post("/admin/self-management/grants")
async def set_grant(body: dict, auth=Depends(require_scopes("admin"))):
    from core.self_management.service import user_grant
    uid, char_id = _scope()
    return _result(user_grant(uid, char_id, capability_id=str(body.get("capability_id") or ""), allowed=body.get("allowed"), mutable_by_agent=body.get("mutable_by_agent"), constraints=body.get("constraints"), reason=str(body.get("reason") or "")))


@router.post("/admin/self-management/locks")
async def set_capability_lock(body: dict, auth=Depends(require_scopes("admin"))):
    from core.self_management.service import set_lock
    uid, char_id = _scope()
    return _result(set_lock(uid, char_id, capability_id=str(body.get("capability_id") or ""), locked=body.get("locked"), reason=str(body.get("reason") or "")))


@router.post("/admin/self-management/restore")
async def restore_self_management(body: dict, auth=Depends(require_scopes("admin"))):
    from core.self_management.service import restore_user_setting
    uid, char_id = _scope()
    return _result(restore_user_setting(uid, char_id, capability_id=str(body.get("capability_id") or ""), reason=str(body.get("reason") or "")))


@router.post("/admin/self-management/undo")
async def undo_self_management(body: dict, auth=Depends(require_scopes("admin"))):
    from core.self_management.service import undo_latest_agent_change
    uid, char_id = _scope()
    return _result(undo_latest_agent_change(uid, char_id, capability_id=str(body.get("capability_id") or ""), reason=str(body.get("reason") or "")))
