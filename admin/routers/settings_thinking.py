"""
思考开关设置接口（Brief 32 §5）
GET  /settings/thinking   — 读取当前 thinking 配置 + 只读的 auto 模式判定展示字段
POST /settings/thinking   — 部分更新 enabled / mode / apply_to_proactive 并热重载

配对前端实现：PresenceKit-desktop 的思考开关设置。
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

_VALID_MODES = ("auto", "native", "monologue")

_DEFAULTS = {
    "enabled": False,
    "mode": "auto",
    "monologue_max_tokens": 200,
    "apply_to_proactive": False,
}


def _chat_preset_reasoning_native() -> bool:
    """当前 chat preset 是否声明 reasoning_native（供前端展示 auto 会走哪条路）。

    只读 preset 配置字段，不走 get_model_client()（避免为一次只读检查顺带建出
    真实的 AsyncOpenAI/httpx 客户端，同 settings_tool_loop._chat_preset_supports_fc）。
    """
    from core.model_registry import _get_preset_config, _resolve_preset_name
    mp = _get_preset_config()
    preset_name = _resolve_preset_name("chat")
    preset = mp.get("presets", {}).get(preset_name, {})
    return bool(preset.get("reasoning_native", False))


class ThinkingUpdate(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    apply_to_proactive: Optional[bool] = None


@router.get("/settings/thinking", summary="获取思考开关配置")
async def get_thinking(auth=Depends(require_scopes("persona"))):
    cfg = get_config().get("thinking", {})
    return {
        "enabled": bool(cfg.get("enabled", _DEFAULTS["enabled"])),
        "mode": cfg.get("mode", _DEFAULTS["mode"]),
        "monologue_max_tokens": cfg.get("monologue_max_tokens", _DEFAULTS["monologue_max_tokens"]),
        "apply_to_proactive": bool(cfg.get("apply_to_proactive", _DEFAULTS["apply_to_proactive"])),
        "chat_preset_reasoning_native": _chat_preset_reasoning_native(),
    }


@router.post("/settings/thinking", summary="更新思考开关配置并热重载")
async def update_thinking(body: ThinkingUpdate, auth=Depends(require_scopes("persona"))):
    if body.mode is not None and body.mode not in _VALID_MODES:
        raise HTTPException(status_code=422, detail=f"mode 必须是 {_VALID_MODES} 之一")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    th = full_cfg.setdefault("thinking", {})
    if body.enabled is not None:
        th["enabled"] = body.enabled
    if body.mode is not None:
        th["mode"] = body.mode
    if body.apply_to_proactive is not None:
        th["apply_to_proactive"] = body.apply_to_proactive

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()

    return {
        "message": "思考开关配置已更新",
        "thinking": {
            "enabled": bool(th.get("enabled", _DEFAULTS["enabled"])),
            "mode": th.get("mode", _DEFAULTS["mode"]),
            "monologue_max_tokens": th.get("monologue_max_tokens", _DEFAULTS["monologue_max_tokens"]),
            "apply_to_proactive": bool(th.get("apply_to_proactive", _DEFAULTS["apply_to_proactive"])),
        },
        "chat_preset_reasoning_native": _chat_preset_reasoning_native(),
    }
