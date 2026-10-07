"""Admin control and redacted operational state for the WeChat channel."""
from fastapi import APIRouter, Depends

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.wechat_service import WechatSettings, get_service, load_settings
from core.config_loader import get_config_path

router = APIRouter()
CONFIG_FILE = get_config_path()


def operational_state(*, enabled=None):
    service = get_service()
    if service:
        return service.snapshot()
    if enabled is None:
        try:
            enabled = load_settings().enabled
        except (ValueError, TypeError):
            return {"effective_state": "invalid_configuration", "connected": False,
                    "runtime_started": False, "proactive_effective": False,
                    "last_error": "invalid_configuration", "counters": {}, "queue_depth": 0}
    return {"effective_state": "runtime_not_started" if enabled else "disabled",
            "runtime_started": False, "connected": False, "proactive_effective": False,
            "last_error": "", "counters": {}, "queue_depth": 0}


@router.get("/settings/wechat")
async def get_settings(auth=Depends(require_scopes("admin"))):
    import os
    return {**load_settings().model_dump(), **operational_state(),
            "credential_configured": bool(os.environ.get("WECHAT_TRANSPORT_TOKEN")),
            "apply_mode": "hot_reload"}


@router.put("/settings/wechat")
async def put_settings(body: WechatSettings, auth=Depends(require_scopes("admin"))):
    config = read_config_file(CONFIG_FILE)
    config["wechat"] = body.model_dump()
    write_config_file(CONFIG_FILE, config)
    from core.config_loader import reload_config
    reload_config()
    service = get_service()
    if service:
        await service.apply()
    return {**await get_settings(auth), "reload_status": "reloaded"}


@router.get("/observability/wechat")
async def observe(auth=Depends(require_scopes("state.read"))):
    return operational_state()
