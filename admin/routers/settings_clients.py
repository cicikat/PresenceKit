"""Connection settings without local installation paths or credential exposure."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config, get_config_path, reload_config

router = APIRouter()


class ClientSettings(BaseModel):
    desktop: bool | None = None
    mobile: bool | None = None
    qq_host: str | None = Field(default=None, min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9._:-]+$")
    qq_port: int | None = Field(default=None, ge=1, le=65535)


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
    return {"clients": result, "qq_host": cfg.get("qq", {}).get("host", "127.0.0.1"),
            "qq_port": cfg.get("qq", {}).get("port", 3001),
            "standalone_mode": bool(cfg.get("standalone_mode", False)),
            "admin_port": cfg.get("admin", {}).get("port", 8080), "apply_mode": "hot_reload",
            "qq_apply_mode": "restart_required"}


@router.put("/settings/clients")
async def put_settings(body: ClientSettings, auth=Depends(require_scopes("admin"))):
    cfg = read_config_file(get_config_path())
    restart = False
    for name in ("desktop", "mobile"):
        value = getattr(body, name)
        if value is not None:
            cfg.setdefault("client_channels", {})[name] = value
    for key in ("host", "port"):
        value = getattr(body, "qq_" + key)
        if value is not None:
            restart |= cfg.get("qq", {}).get(key) != value
            cfg.setdefault("qq", {})[key] = value
    write_config_file(get_config_path(), cfg)
    reload_config()
    return {**await get_settings(auth), "restart_required": restart}
