"""
思考开关设置接口（Brief 32 §5）
GET  /settings/thinking   — 读取当前 thinking 配置 + 只读的 auto 模式判定展示字段
POST /settings/thinking   — 部分更新 enabled / mode / apply_to_proactive / display_prefer_monologue 并热重载

管理面入口在「模型连接与分工」；GET/POST /settings/thinking 仍是 persona API。桌面只展开显示，不新增设置。
GET 的 monologue_route / last_monologue 是管理面只读元数据；桌面不消费，也不返回独白正文。
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
    "display_prefer_monologue": True,
}


def _live_char_id() -> str | None:
    """Active character for live monologue routing, not the profile being edited."""
    try:
        from admin.routers.settings_llm import _active_character_id_for_routing_warning
        return _active_character_id_for_routing_warning()
    except Exception:
        return None


def _chat_preset_reasoning_native() -> bool:
    """当前 chat preset 是否声明 reasoning_native（供前端展示 auto 会走哪条路）。

    只读 preset 配置字段，不走 get_model_client()（避免为一次只读检查顺带建出
    真实的 AsyncOpenAI/httpx 客户端，同 settings_tool_loop._chat_preset_supports_fc）。
    """
    from core.thinking import describe_monologue_route
    return bool(describe_monologue_route(char_id=_live_char_id()).get("chat_preset_reasoning_native"))


def _thinking_read_payload(cfg: dict) -> dict:
    from core.thinking import describe_monologue_route, last_monologue_status
    from core.thinking_voice import preview
    voice_enabled = bool(cfg.get("character_voice", True))
    voice = preview()
    voice.update({
        "enabled": voice_enabled,
        "effective": bool(cfg.get("enabled", False)) and voice_enabled,
        "blocking_reason": "thinking_disabled" if not cfg.get("enabled", False) else ("voice_disabled" if not voice_enabled else ""),
        "control": "prompt_guidance", "output_guaranteed": False,
    })
    route = describe_monologue_route(char_id=_live_char_id())
    last = last_monologue_status(
        chat_reasoning_native=bool(route.get("chat_preset_reasoning_native")),
    )
    last.pop("body", None)
    return {
        "enabled": bool(cfg.get("enabled", _DEFAULTS["enabled"])),
        "mode": cfg.get("mode", _DEFAULTS["mode"]),
        "monologue_max_tokens": cfg.get("monologue_max_tokens", _DEFAULTS["monologue_max_tokens"]),
        "apply_to_proactive": bool(cfg.get("apply_to_proactive", _DEFAULTS["apply_to_proactive"])),
        "chat_preset_reasoning_native": bool(route.get("chat_preset_reasoning_native")),
        "character_voice": voice_enabled,
        "display_prefer_monologue": bool(
            cfg.get("display_prefer_monologue", _DEFAULTS["display_prefer_monologue"])
        ),
        "voice_preview": voice,
        "monologue_route": route,
        "last_monologue": last,
    }


class ThinkingUpdate(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    apply_to_proactive: Optional[bool] = None
    monologue_max_tokens: Optional[int] = None
    character_voice: Optional[bool] = None
    display_prefer_monologue: Optional[bool] = None


@router.get("/settings/thinking", summary="获取思考开关配置")
async def get_thinking(auth=Depends(require_scopes("persona"))):
    return _thinking_read_payload(get_config().get("thinking", {}))


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
    if body.display_prefer_monologue is not None:
        th["display_prefer_monologue"] = body.display_prefer_monologue

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader
    config_loader.reload_config()

    payload = _thinking_read_payload(th)
    return {
        "message": "思考开关配置已更新",
        "thinking": {
            "enabled": payload["enabled"],
            "mode": payload["mode"],
            "monologue_max_tokens": payload["monologue_max_tokens"],
            "apply_to_proactive": payload["apply_to_proactive"],
            "character_voice": payload["character_voice"],
            "display_prefer_monologue": payload["display_prefer_monologue"],
        },
        "chat_preset_reasoning_native": payload["chat_preset_reasoning_native"],
        "monologue_route": payload["monologue_route"],
        "last_monologue": payload["last_monologue"],
    }
