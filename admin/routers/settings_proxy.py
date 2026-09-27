"""
代理配置接口
GET /proxy  — 读取当前代理配置
PUT /proxy  — 修改代理配置并热重载
"""

from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config
from core.model_network import normalize_model_connection_mode

router = APIRouter()
CONFIG_FILE = Path("config.yaml")


class ProxyUpdate(BaseModel):
    enabled: Optional[bool] = None
    http: Optional[str] = None
    https: Optional[str] = None
    model_connection_mode: Optional[Literal["follow_global", "auto", "direct", "proxy"]] = None


@router.get("/proxy", summary="获取当前代理配置")
async def get_proxy(auth=Depends(require_scopes("admin"))):
    proxy_cfg = get_config().get("proxy", {}) or {}
    return {
        "enabled": proxy_cfg.get("enabled", False),
        "http":    proxy_cfg.get("http",    ""),
        "https":   proxy_cfg.get("https",   ""),
        "model_connection_mode": normalize_model_connection_mode(
            proxy_cfg.get("model_connection_mode", "follow_global")
        ),
    }


@router.put("/proxy", summary="修改代理配置并热重载")
async def update_proxy(body: ProxyUpdate, auth=Depends(require_scopes("admin"))):
    """修改 proxy 字段，热重载 config + 重置 LLM 客户端"""
    full_cfg = read_config_file(CONFIG_FILE)

    proxy_cfg = full_cfg.setdefault("proxy", {})
    if body.enabled is not None:
        proxy_cfg["enabled"] = body.enabled
    if body.http is not None:
        proxy_cfg["http"] = body.http
    if body.https is not None:
        proxy_cfg["https"] = body.https
    if body.model_connection_mode is not None:
        proxy_cfg["model_connection_mode"] = body.model_connection_mode

    mode = normalize_model_connection_mode(proxy_cfg.get("model_connection_mode", "follow_global"))
    if mode in {"auto", "proxy"}:
        proxy_url = str(proxy_cfg.get("http") or "").strip()
        parsed = urlsplit(proxy_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HTTPException(status_code=422, detail="模型自动/代理模式需要有效的 HTTP 代理地址")
    if body.model_connection_mode is not None:
        proxy_cfg["model_connection_mode"] = mode

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader, llm_client
    config_loader.reload_config()
    await llm_client.reload_client()

    return {"message": "代理配置已更新并热重载", "proxy": proxy_cfg}
