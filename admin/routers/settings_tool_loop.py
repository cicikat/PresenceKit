"""
tool loop 多步工具执行器配置接口（Brief 28 §3.7）
GET  /settings/tool-loop   — 读取当前 tool_loop 配置 + chat preset 是否支持 function_calling
POST /settings/tool-loop   — 更新 enabled / max_steps / categories / exclude_tools 并热重载
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

_MAX_STEPS_MIN = 1
_MAX_STEPS_MAX = 8

_DEFAULTS = {
    "enabled": False,
    "max_steps": 5,
    "total_timeout_s": 300,
    "categories": ["info", "desktop", "memory", "artifacts"],
    "exclude_tools": ["toy_vibrate", "toy_stop", "toy_pattern", "write_toy_file"],
    "nudge_hint": "",
}


def _chat_preset_supports_fc() -> bool:
    """当前 chat preset 的 tool_call_mode 是否为 function_calling。

    只读 preset 配置字段，不走 get_model_client()（避免为了一次只读检查
    顺带建出真实的 AsyncOpenAI/httpx 客户端）。
    """
    from core.model_registry import _get_preset_config, _resolve_preset_name
    mp = _get_preset_config()
    preset_name = _resolve_preset_name("chat")
    preset = mp.get("presets", {}).get(preset_name, {})
    return preset.get("tool_call_mode", "function_calling") == "function_calling"


class ToolLoopUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_steps: Optional[int] = None
    categories: Optional[list[str]] = None
    exclude_tools: Optional[list[str]] = None
    total_timeout_s: Optional[int] = None
    nudge_hint: Optional[str] = None


@router.get("/settings/tool-loop", summary="获取 tool loop 配置")
async def get_tool_loop(auth=Depends(require_scopes("persona"))):
    cfg = get_config().get("tool_loop", {})
    from core.tool_exposure import resolve
    path_a = resolve("path_a")
    path_c = resolve("path_c")
    return {
        "enabled": bool(cfg.get("enabled", _DEFAULTS["enabled"])),
        "max_steps": int(cfg.get("max_steps", _DEFAULTS["max_steps"])),
        "total_timeout_s": cfg.get("total_timeout_s", _DEFAULTS["total_timeout_s"]),
        "categories": cfg.get("categories", _DEFAULTS["categories"]),
        "exclude_tools": cfg.get("exclude_tools", _DEFAULTS["exclude_tools"]),
        "nudge_hint": cfg.get("nudge_hint", _DEFAULTS["nudge_hint"]),
        "chat_preset_supports_fc": _chat_preset_supports_fc(),
        "path_exposure": {
            "path_a": {
                "categories": list(path_a.categories),
                "tools": sorted(path_a.tools) if path_a.tools is not None else None,
                "exclude_tools": sorted(path_a.exclude_tools),
                "source": path_a.source,
            },
            "path_c": {
                "categories": list(path_c.categories),
                "tools": sorted(path_c.tools) if path_c.tools is not None else None,
                "exclude_tools": sorted(path_c.exclude_tools),
                "source": path_c.source,
            },
        },
    }


@router.post("/settings/tool-loop", summary="更新 tool loop 配置并热重载")
async def update_tool_loop(body: ToolLoopUpdate, auth=Depends(require_scopes("persona"))):
    full_cfg = read_config_file(CONFIG_FILE)

    tl = full_cfg.setdefault("tool_loop", {})
    if body.enabled is not None:
        tl["enabled"] = body.enabled
    if body.max_steps is not None:
        tl["max_steps"] = max(_MAX_STEPS_MIN, min(_MAX_STEPS_MAX, body.max_steps))
    if body.categories is not None:
        tl["categories"] = body.categories
        full_cfg.setdefault("tool_exposure", {}).setdefault("path_c", {})["categories"] = body.categories
    if body.exclude_tools is not None:
        tl["exclude_tools"] = body.exclude_tools
        full_cfg.setdefault("tool_exposure", {}).setdefault("path_c", {})["exclude_tools"] = body.exclude_tools
    if body.total_timeout_s is not None:
        tl["total_timeout_s"] = max(5, min(720, body.total_timeout_s))
    if body.nudge_hint is not None:
        tl["nudge_hint"] = body.nudge_hint[:1000]

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader
    config_loader.reload_config()

    from core.tool_exposure import resolve
    path_a = resolve("path_a")
    path_c = resolve("path_c")
    return {
        "message": "tool loop 配置已更新",
        "tool_loop": full_cfg["tool_loop"],
        "chat_preset_supports_fc": _chat_preset_supports_fc(),
        "path_exposure": {
            "path_a": {"categories": list(path_a.categories), "tools": sorted(path_a.tools) if path_a.tools is not None else None, "exclude_tools": sorted(path_a.exclude_tools), "source": path_a.source},
            "path_c": {"categories": list(path_c.categories), "tools": sorted(path_c.tools) if path_c.tools is not None else None, "exclude_tools": sorted(path_c.exclude_tools), "source": path_c.source},
        },
    }
