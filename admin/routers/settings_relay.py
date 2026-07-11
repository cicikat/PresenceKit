"""
中继唤醒配置接口
GET /settings/relay  — 读取 relay_base_url / relay_topic / relay_token（token 打码）
PUT /settings/relay  — 修改并热重载
"""

from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from core.config_loader import get_config

router = APIRouter()
CONFIG_FILE = Path("config.yaml")


def _mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "***"
    return token[:4] + "***" + token[-4:]


@router.get("/settings/relay", summary="获取中继唤醒配置（token 打码）")
async def get_relay_settings(auth=Depends(require_scopes("admin"))):
    cfg = get_config()
    return {
        "relay_base_url": cfg.get("relay_base_url") or "",
        "relay_topic": cfg.get("relay_topic") or "",
        "relay_token": _mask_token(cfg.get("relay_token") or ""),
    }


class RelaySettingsUpdate(BaseModel):
    relay_base_url: Optional[str] = None
    relay_topic: Optional[str] = None
    relay_token: Optional[str] = None


@router.put("/settings/relay", summary="修改中继唤醒配置并热重载")
async def update_relay_settings(body: RelaySettingsUpdate, auth=Depends(require_scopes("admin"))):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    if body.relay_base_url is not None:
        full_cfg["relay_base_url"] = body.relay_base_url
    if body.relay_topic is not None:
        full_cfg["relay_topic"] = body.relay_topic
    if body.relay_token is not None:
        full_cfg["relay_token"] = body.relay_token

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()

    return {
        "message": "中继唤醒配置已更新",
        "relay_base_url": full_cfg.get("relay_base_url") or "",
        "relay_topic": full_cfg.get("relay_topic") or "",
        "relay_token": _mask_token(full_cfg.get("relay_token") or ""),
    }
