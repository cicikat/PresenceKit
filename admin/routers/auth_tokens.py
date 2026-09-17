"""
Token 管理 API（SEC-AUTH-2 P3 + Brief 22 DX）。除 whoami 外全部 admin scope。

GET    /auth/tokens                — 列表（label/scopes/created_at/expires_at/disabled/hash 前 8 位；无明文）
POST   /auth/tokens                — 创建，body {label, profile 或 scopes, expires_at?}；返回明文仅此一次
POST   /auth/tokens/{label}/rotate — 轮换，scope 不变；返回新明文仅此一次，旧值立即失效
PATCH  /auth/tokens/{label}        — 启用/停用，body {disabled: true|false}
DELETE /auth/tokens/{label}        — 吊销（物理删除）
GET    /auth/whoami                — 当前 token 的 {label, scopes}；任意有效 token 可调（零 scope 依赖）
GET    /auth/profiles              — profile → scopes 常量表（供管理面板 Create 下拉）
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes, TokenInfo
from admin import token_registry
from admin.scopes import PROFILES

router = APIRouter()


def _hash_prefix(h: str) -> str:
    return h.split(":", 1)[-1][:8]


def _to_dict(record: token_registry.TokenRecord) -> dict:
    return {
        "label": record.label,
        "scopes": sorted(record.scopes),
        "profiles": sorted(record.profiles),
        "expires_at": record.expires_at,
        "disabled": record.disabled,
        "hash_prefix": _hash_prefix(record.hash),
        "created_at": record.created_at,
    }


@router.get("/auth/tokens", summary="列出所有 token（不含明文）")
async def list_tokens(_auth=Depends(require_scopes("admin"))):
    return {"tokens": [_to_dict(r) for r in token_registry.list_records()]}


class TokenCreate(BaseModel):
    label: str
    profile: Optional[str] = None
    scopes: Optional[list[str]] = None
    expires_at: Optional[str] = None


@router.post("/auth/tokens", summary="创建新 token（明文仅此一次返回）")
async def create_token(body: TokenCreate, _auth=Depends(require_scopes("admin"))):
    if not body.profile and not body.scopes:
        raise HTTPException(status_code=422, detail="必须提供 profile 或 scopes 之一")
    if body.profile and body.scopes:
        raise HTTPException(status_code=422, detail="profile 与 scopes 二选一，不可同时提供")
    raw_scopes = [f"profile:{body.profile}"] if body.profile else list(body.scopes)
    try:
        token = token_registry.create_token(body.label, scopes=raw_scopes, expires_at=body.expires_at)
    except token_registry.TokenLabelError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    from admin.audit import log_event
    log_event("token_created", label=body.label)
    return {"label": body.label, "token": token}


@router.post("/auth/tokens/{label}/rotate", summary="轮换 token（旧值立即失效）")
async def rotate_token(label: str, _auth=Depends(require_scopes("admin"))):
    try:
        token = token_registry.rotate_token(label)
    except token_registry.TokenLabelError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"token {label!r} 不存在")
    from admin.audit import log_event
    log_event("token_rotated", label=label)
    return {"label": label, "token": token}


@router.delete("/auth/tokens/{label}", summary="吊销 token（物理删除）")
async def delete_token(label: str, _auth=Depends(require_scopes("admin"))):
    try:
        ok = token_registry.delete_token(label)
    except token_registry.TokenLabelError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"token {label!r} 不存在")
    from admin.audit import log_event
    log_event("token_deleted", label=label)
    return {"label": label, "deleted": True}


class TokenPatch(BaseModel):
    disabled: bool


@router.patch("/auth/tokens/{label}", summary="停用/启用 token")
async def patch_token(label: str, body: TokenPatch, _auth=Depends(require_scopes("admin"))):
    try:
        ok = token_registry.set_disabled(label, body.disabled)
    except token_registry.TokenLabelError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"token {label!r} 不存在")
    from admin.audit import log_event
    log_event("token_disabled" if body.disabled else "token_enabled", label=label)
    return {"label": label, "disabled": body.disabled}


@router.get("/auth/whoami", summary="当前 token 的身份（任意有效 token 可调）")
async def whoami(info: TokenInfo = Depends(require_scopes())):
    from core.session_scope import SESSION_SCOPE_VERSION

    return {
        "label": info.label,
        "scopes": sorted(info.scopes),
        "capabilities": {"session_scope": SESSION_SCOPE_VERSION},
    }


@router.get("/auth/profiles", summary="profile → scopes 常量表")
async def list_profiles(_auth=Depends(require_scopes("admin"))):
    return {"profiles": {name: sorted(scopes) for name, scopes in PROFILES.items()}}
