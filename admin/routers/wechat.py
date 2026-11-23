"""Admin control and redacted operational state for the WeChat channel."""
from fastapi import APIRouter, Depends

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.wechat_service import WechatSettings, get_service, load_settings
from core.config_loader import get_config_path
from core.wechat_service import operational_state

router = APIRouter()
CONFIG_FILE = get_config_path()




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
