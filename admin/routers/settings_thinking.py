"""
思考开关设置接口（Brief 32 §5）
GET  /settings/thinking   — 读取当前 thinking 配置 + 只读的 auto 模式判定展示字段
POST /settings/thinking   — 部分更新 enabled / mode / apply_to_proactive 并热重载

管理面入口在「模型连接与分工」；GET/POST /settings/thinking 仍是 persona API。桌面只展开显示，不新增设置。
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config

router = APIRouter()
CONFIG_FILE = Path("config.yaml")

_VALID_MODES = ("auto", "native", "monologue")

_DEFAULTS = {
    "enabled": False,
    "mode": "auto",
    "monologue_max_tokens": 200,
    "apply_to_proactive": False,
    "character_voice": True,
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
    monologue_max_tokens: Optional[int] = None
    character_voice: Optional[bool] = None


@router.get("/settings/thinking", summary="获取思考开关配置")
async def get_thinking(auth=Depends(require_scopes("persona"))):
    cfg = get_config().get("thinking", {})
    from core.thinking_voice import preview
    voice_enabled = bool(cfg.get("character_voice", True))
    voice = preview()
    voice.update({
        "enabled": voice_enabled,
        "effective": bool(cfg.get("enabled", False)) and voice_enabled,
        "blocking_reason": "thinking_disabled" if not cfg.get("enabled", False) else ("voice_disabled" if not voice_enabled else ""),
        "control": "prompt_guidance", "output_guaranteed": False,
    })
    return {
        "enabled": bool(cfg.get("enabled", _DEFAULTS["enabled"])),
        "mode": cfg.get("mode", _DEFAULTS["mode"]),
        "monologue_max_tokens": cfg.get("monologue_max_tokens", _DEFAULTS["monologue_max_tokens"]),
        "apply_to_proactive": bool(cfg.get("apply_to_proactive", _DEFAULTS["apply_to_proactive"])),
        "chat_preset_reasoning_native": _chat_preset_reasoning_native(),
        "character_voice": voice_enabled,
        "voice_preview": voice,
    }


@router.post("/settings/thinking", summary="更新思考开关配置并热重载")
async def update_thinking(body: ThinkingUpdate, auth=Depends(require_scopes("persona"))):
    if body.mode is not None and body.mode not in _VALID_MODES:
        raise HTTPException(status_code=422, detail=f"mode 必须是 {_VALID_MODES} 之一")

    full_cfg = read_config_file(CONFIG_FILE)

    th = full_cfg.setdefault("thinking", {})
    if body.enabled is not None:
        th["enabled"] = body.enabled
    if body.character_voice is not None:
        th["character_voice"] = body.character_voice
    if body.mode is not None:
        th["mode"] = body.mode
    if body.apply_to_proactive is not None:
        th["apply_to_proactive"] = body.apply_to_proactive
    if body.monologue_max_tokens is not None:
        th["monologue_max_tokens"] = max(32, min(2000, body.monologue_max_tokens))

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader
    config_loader.reload_config()

    return {
        "message": "思考开关配置已更新",
        "thinking": {
            "enabled": bool(th.get("enabled", _DEFAULTS["enabled"])),
            "mode": th.get("mode", _DEFAULTS["mode"]),
            "monologue_max_tokens": th.get("monologue_max_tokens", _DEFAULTS["monologue_max_tokens"]),
            "apply_to_proactive": bool(th.get("apply_to_proactive", _DEFAULTS["apply_to_proactive"])),
            "character_voice": bool(th.get("character_voice", True)),
        },
        "chat_preset_reasoning_native": _chat_preset_reasoning_native(),
    }
