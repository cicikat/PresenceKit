"""Admin-only QZone connection controls; credentials are write-only."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config_path, reload_config
from core import qzone_service as service
from integrations.qzone.transport import request
import asyncio
import aiohttp

router = APIRouter()


class CookieLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookie: str = Field(min_length=1, max_length=16000)


@router.get("/settings/qzone")
async def get_settings(auth=Depends(require_scopes("admin"))):
    cfg = service.settings().model_dump(exclude={"access_token"})
    return {**cfg, **service.snapshot(), "effective_watched_user_ids": service.watched_users(),
            "events": _event_state()}


@router.put("/settings/qzone")
async def put_settings(body: service.QzoneSettings, auth=Depends(require_scopes("admin"))):
    cfg = read_config_file(get_config_path())
    previous = service.settings()
    values = body.model_dump()
    # Blank password preserves the stored bridge token.
    if not values["access_token"]:
        values["access_token"] = (cfg.get("qzone") or {}).get("access_token", "")
    cfg["qzone"] = values
    write_config_file(get_config_path(), cfg)
    reload_config()
    service._state.update(last_code="not_checked", last_checked_at=0.0)
    from core.qzone_events import revision
    if revision(previous) != revision(body):
        from core.qzone_events import invalidate
        owner = str(service.get_config().get("scheduler", {}).get("owner_id") or "")
        for char_id in set(body.allowed_char_ids + previous.allowed_char_ids):
            if owner:
                invalidate(owner, char_id)
    return await get_settings(auth)


@router.post("/settings/qzone/probe")
async def probe(auth=Depends(require_scopes("admin"))):
    return await service.probe()


@router.post("/settings/qzone/login-cookie")
async def login_cookie(body: CookieLogin, auth=Depends(require_scopes("admin"))):
    cfg = service.settings()
    if not cfg.account_id:
        raise HTTPException(409, "请先保存预期 QQ 账号，防止绑定错误账号")
    from http.cookies import SimpleCookie
    cookies = SimpleCookie()
    cookies.load(body.cookie)
    value = cookies.get("uin") or cookies.get("p_uin")
    if value is None or value.value.lstrip("o").lstrip("0") != cfg.account_id.lstrip("0"):
        raise HTTPException(400, "Cookie 账号与预期账号不一致")
    result = await request(cfg.base_url, cfg.access_token, "login_cookie", {"cookie": body.cookie})
    if not result.get("ok"):
        return {"ok": False, "code": result.get("code", "login_failed")}
    return await service.probe(cfg)


@router.post("/settings/qzone/sync-napcat-cookie")
async def sync_napcat_cookie(auth=Depends(require_scopes("admin"))):
    cfg = service.settings()
    if not cfg.account_id:
        raise HTTPException(409, "请先保存预期 QQ 账号")
    try:
        cookie = await service.napcat_cookie(cfg.account_id)
    except (ValueError, aiohttp.ClientError, asyncio.TimeoutError):
        return {"ok": False, "code": "napcat_login_or_cookie_unavailable"}
    result = await request(cfg.base_url, cfg.access_token, "login_cookie", {"cookie": cookie})
    if not result.get("ok"):
        return {"ok": False, "code": result.get("code", "login_failed")}
    return await service.probe(cfg)


@router.get("/observability/qzone")
async def observe(auth=Depends(require_scopes("state.read"))):
    return {**service.snapshot(), "events": _event_state()}


def _event_state():
    from core.character_loader import _active_character_id
    from core.qzone_events import observe
    owner = str(service.get_config().get("scheduler", {}).get("owner_id") or "")
    char_id = _active_character_id()
    return observe(owner, char_id) if owner and char_id else {"last_code": "scope_unavailable"}
