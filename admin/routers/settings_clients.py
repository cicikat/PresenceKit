"""Connection settings without local installation paths or credential exposure."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Literal
from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config, get_config_path, reload_config

router = APIRouter()


class ClientSettings(BaseModel):
    desktop: bool | None = None
    mobile: bool | None = None
    mobile_realtime: bool | None = None
    qq_host: str | None = Field(default=None, min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9._:-]+$")
    qq_port: int | None = Field(default=None, ge=1, le=65535)
    qq_outbound_mode: Literal['reply_only', 'muted', 'all'] | None = None


@router.get("/settings/clients")
async def get_settings(auth=Depends(require_scopes("admin"))):
    from channels import registry, desktop_ws
    cfg = get_config()
    from core.client_channels import enabled
    result = {}
    for name in ("desktop", "mobile"):
        channel = registry.get(name)
        connected = desktop_ws.is_connected() if name == "desktop" else bool(channel and channel.is_active)
        result[name] = {"enabled": enabled(name), "effective_state": "disabled" if not enabled(name) else "connected" if connected else "waiting_for_client"}
    from core.qq_service import operational_state
    result['qq'] = {'enabled': bool(cfg.get('qq', {}).get('enabled', False)), **operational_state()}
    from core.mobile_realtime import enabled as realtime_enabled
    return {"clients": result, "mobile_realtime": {"enabled": bool(cfg.get("mobile_realtime", {}).get("enabled", True)),
            "effective": realtime_enabled(), "mode": "final_only", "default": True},
            "qq_host": cfg.get("qq", {}).get("host", "127.0.0.1"),
            "qq_port": cfg.get("qq", {}).get("port", 3001),
            "standalone_mode": bool(cfg.get("standalone_mode", False)),
            "admin_port": cfg.get("admin", {}).get("port", 8080), "apply_mode": "hot_reload",
            "qq_apply_mode": "hot_reload"}


@router.put("/settings/clients")
async def put_settings(body: ClientSettings, auth=Depends(require_scopes("admin"))):
    cfg = read_config_file(get_config_path())
    if body.mobile_realtime is not None:
        cfg.setdefault("mobile_realtime", {})["enabled"] = body.mobile_realtime
    for name in ("desktop", "mobile"):
        value = getattr(body, name)
        if value is not None:
            cfg.setdefault("client_channels", {})[name] = value
    for key in ("host", "port", "outbound_mode"):
        value = getattr(body, "qq_" + key)
        if value is not None:
            cfg.setdefault("qq", {})[key] = value
    write_config_file(get_config_path(), cfg)
    reload_config()
    from core.qq_service import get_service
    service = get_service()
    if service:
        await service.apply()
    return {**await get_settings(auth), "restart_required": False}
