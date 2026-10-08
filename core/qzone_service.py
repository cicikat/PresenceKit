"""QZone configuration, account binding and redacted operational state."""
from __future__ import annotations

import asyncio
import time
import uuid

import aiohttp

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.config_loader import get_config
from integrations.qzone.transport import request, validate_url, WRITE_ACTIONS


class QzoneSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    write_enabled: bool = False
    events_enabled: bool = False
    replies_enabled: bool = True
    autonomy_interactions_enabled: bool = False
    watched_user_ids: list[str] = Field(default_factory=list, max_length=10)
    poll_interval_seconds: int = Field(default=120, ge=60, le=3600)
    base_url: str = "http://127.0.0.1:5700"
    access_token: str = Field(default="", max_length=4096)
    account_id: str = Field(default="", max_length=20, pattern=r"^\d*$")
    allowed_char_ids: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("base_url")
    @classmethod
    def url(cls, value):
        return validate_url(value)

    @field_validator("allowed_char_ids")
    @classmethod
    def characters(cls, values):
        from core.data_paths import safe_user_id
        return list(dict.fromkeys(safe_user_id(value) for value in values))

    @field_validator("watched_user_ids")
    @classmethod
    def watched(cls, values):
        import re
        if any(not re.fullmatch(r"[0-9]{1,20}", value) for value in values):
            raise ValueError("关注用户必须是 QQ 数字 ID")
        return list(dict.fromkeys(values))


def watched_users(cfg: QzoneSettings | None = None) -> list[str]:
    import re
    cfg = cfg or settings()
    owner = str(get_config().get("scheduler", {}).get("owner_id") or "")
    return cfg.watched_user_ids or ([owner] if re.fullmatch(r"[0-9]{1,20}", owner) else [])


def autonomy_tool_allowed(name: str, uid: str, char_id: str) -> bool:
    cfg = settings()
    if name in {"qzone_get_feeds", "qzone_get_posts", "qzone_get_comments"}:
        return cfg.events_enabled and allowed(uid, char_id)
    return bool(name in {"qzone_comment", "qzone_set_like"}
                and cfg.autonomy_interactions_enabled and allowed(uid, char_id, write=True))


_state = {"last_code": "not_checked", "last_checked_at": 0.0, "calls": 0, "failures": 0}
_write_lock = asyncio.Lock()


def settings() -> QzoneSettings:
    return QzoneSettings.model_validate(get_config().get("qzone") or {})


def allowed(uid: str | None, char_id: str | None, *, write=False) -> bool:
    try:
        cfg = settings()
        owner = str(get_config().get("scheduler", {}).get("owner_id") or "")
        return bool(cfg.enabled and cfg.account_id and owner and str(uid or "") == owner
                    and char_id in cfg.allowed_char_ids and (not write or cfg.write_enabled))
    except (ValueError, TypeError):
        return False


def snapshot() -> dict:
    cfg = settings()
    effective = "disabled" if not cfg.enabled else "binding_missing" if not cfg.account_id or not cfg.allowed_char_ids else _state["last_code"]
    return {"enabled": cfg.enabled, "write_enabled": cfg.write_enabled,
            "events_enabled": cfg.events_enabled,
            "watched_user_count": len(watched_users(cfg)),
            "autonomy_interactions_enabled": cfg.autonomy_interactions_enabled,
            "effective_state": effective, "apply_mode": "hot_reload",
            "credential_configured": bool(cfg.access_token), **_state}


async def probe(cfg: QzoneSettings | None = None) -> dict:
    cfg = cfg or settings()
    login = await request(cfg.base_url, cfg.access_token, "get_login_info", {})
    account = str((login.get("data") or {}).get("user_id") or "") if login.get("ok") else ""
    if not login.get("ok"):
        code = login.get("code", "bridge_rejected")
    elif cfg.account_id and account != cfg.account_id:
        code = "account_mismatch"
    else:
        cookie = await request(cfg.base_url, cfg.access_token, "check_cookie", {"probe": True})
        code = "ready" if cookie.get("ok") and (cookie.get("data") or {}).get("valid") is True else "login_expired"
    _state.update(last_code=code, last_checked_at=time.time())
    return {"ok": code == "ready", "code": code, "account_id": account}


async def call(action: str, params: dict, *, uid: str, char_id: str) -> dict:
    write = action in WRITE_ACTIONS
    if not allowed(uid, char_id, write=write):
        return {"ok": False, "code": "qzone_not_authorized"}
    cfg = settings()
    # Bind every operation to the configured login, so replacing a bridge cannot
    # silently make another QQ account publish on the character's behalf.
    async def perform():
        login = await request(cfg.base_url, cfg.access_token, "get_login_info", {})
        if not login.get("ok"):
            return login
        if str((login.get("data") or {}).get("user_id") or "") != cfg.account_id:
            return {"ok": False, "code": "account_mismatch"}
        if not allowed(uid, char_id, write=write) or settings().model_dump() != cfg.model_dump():
            return {"ok": False, "code": "qzone_settings_changed"}
        return await request(cfg.base_url, cfg.access_token, action, params)
    if write:
        async with _write_lock:
            result = await perform()
    else:
        result = await perform()
    _state["calls"] += 1
    _state["failures"] += int(not result.get("ok"))
    _state["last_code"] = "ready" if result.get("ok") else result.get("code", "failed")
    _state["last_checked_at"] = time.time()
    return result


async def napcat_cookie(expected_account: str) -> str:
    """Read only the configured NapCat login and QZone cookie; never export it."""
    qq = get_config().get("qq") or {}
    url = f"ws://{qq.get('host', '127.0.0.1')}:{int(qq.get('port', 3001))}"
    async def fetch():
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.ws_connect(url) as ws:
                async def one(action, params):
                    echo = uuid.uuid4().hex
                    await ws.send_json({"action": action, "params": params, "echo": echo})
                    while True:
                        body = await ws.receive_json()
                        if body.get("echo") == echo:
                            if body.get("status") != "ok" or body.get("retcode") != 0:
                                raise ValueError("napcat_rejected")
                            return body.get("data") or {}
                login = await one("get_login_info", {})
                if str(login.get("user_id") or "") != expected_account:
                    raise ValueError("account_mismatch")
                result = await one("get_cookies", {"domain": "qzone.qq.com"})
                cookie = str(result.get("cookies") or "")
                if "p_skey=" not in cookie:
                    raise ValueError("napcat_cookie_missing")
                return cookie
    return await asyncio.wait_for(fetch(), timeout=20)
