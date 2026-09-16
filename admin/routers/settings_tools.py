"""Admin control plane for registered tool exposure presets.

It controls built-in registered tools and the schemas that reach a particular
chat model preset.  MCP remains an independent subsystem.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config
from core.tool_presets import normalize_tool_presets

router = APIRouter()
CONFIG_FILE = Path("config.yaml")
_MAX_PRESETS = 32
_MAX_TOOLS_PER_PRESET = 64


class ToolPresetInput(BaseModel):
    name: str
    tools: list[str] = Field(default_factory=list)


class ToolExposureInput(BaseModel):
    categories: Optional[list[str]] = None
    tools: Optional[list[str]] = None
    exclude_tools: Optional[list[str]] = None


class ToolControlUpdate(BaseModel):
    tool_presets: Optional[list[ToolPresetInput]] = None
    model_bindings: Optional[dict[str, Optional[str]]] = None
    execution_enabled: Optional[dict[str, bool]] = None
    global_default_tools: Optional[list[str]] = None
    exposure: Optional[dict[str, ToolExposureInput]] = None


def _static_tool_enabled(name: str, tools_config: dict) -> bool:
    value = tools_config.get(name)
    if isinstance(value, dict):
        return bool(value.get("enabled", True))
    if value is not None:
        return bool(value)
    return name != "read_xiaohongshu"


class XiaohongshuSettings(BaseModel):
    enabled: bool = False
    local_service: bool = False
    reader_url: str = ""
    max_comments: int = Field(10, ge=1, le=30)
    max_images: int = Field(2, ge=0, le=4)


@router.get('/settings/xiaohongshu')
async def get_xiaohongshu_settings(auth=Depends(require_scopes('admin'))):
    from core.tools.xiaohongshu import settings
    return settings(get_config())


@router.put('/settings/xiaohongshu')
async def update_xiaohongshu_settings(body: XiaohongshuSettings, auth=Depends(require_scopes('admin'))):
    from urllib.parse import urlsplit
    address = body.reader_url.strip().rstrip('/')
    if body.local_service:
        from core.xiaohongshu_service import LOCAL_URL
        if address and address != LOCAL_URL:
            raise HTTPException(422, '本地托管服务使用 http://127.0.0.1:18060；其他地址请使用外部服务模式')
        address = LOCAL_URL
    if address:
        parsed = urlsplit(address)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(422, '读取服务需要有效的 HTTP(S) 根地址')
    cfg = read_config_file(CONFIG_FILE)
    cfg.setdefault('xiaohongshu', {}).update(reader_url=address, max_comments=body.max_comments,
                                           max_images=body.max_images, local_service=body.local_service)
    cfg.setdefault('tools', {})['read_xiaohongshu'] = {'enabled': body.enabled}
    write_config_file(CONFIG_FILE, cfg)
    from core import config_loader
    config_loader.reload_config()
    from core.tools.xiaohongshu import settings
    return settings(cfg)


@router.post('/settings/xiaohongshu/install', status_code=202)
async def install_xiaohongshu(auth=Depends(require_scopes('admin'))):
    from core.xiaohongshu_service import runtime, status
    try:
        runtime().install()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return status()


@router.post('/settings/xiaohongshu/login/{action}')
async def login_xiaohongshu(action: str, auth=Depends(require_scopes('admin'))):
    from core.xiaohongshu_service import runtime
    if action not in {'qrcode', 'status'}:
        raise HTTPException(404, 'unknown_action')
    try:
        from fastapi.responses import JSONResponse
        return JSONResponse(await runtime().login(action), headers={'Cache-Control': 'no-store'})
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _registry_rows(cfg: dict) -> list[dict]:
    from core.tool_dispatcher import _INTIFACE_TOOL_NAMES, _TOOL_REGISTRY, intiface_opted_in

    tools_config = cfg.get("tools", {})
    rows: list[dict] = []
    for name, info in _TOOL_REGISTRY.items():
        # Dynamic MCP registration is necessarily incomplete while servers are
        # offline.  Do not present that transient subset as a tool catalogue.
        if info.get("category") == "mcp":
            continue
        rows.append({
            "name": name,
            "description": (info.get("description") or info.get("desc") or "").strip(),
            "category": info.get("category") or "other",
            "execution_enabled": _static_tool_enabled(name, tools_config) and (
                name not in _INTIFACE_TOOL_NAMES or intiface_opted_in()
            ),
            "frozen": name in _INTIFACE_TOOL_NAMES and not intiface_opted_in(),
        })
    return sorted(rows, key=lambda item: (item["category"], item["name"]))


def _response(cfg: dict) -> dict:
    tool_loop = cfg.get("tool_loop", {})
    model_presets = cfg.get("model_presets", {}).get("presets", {})
    rows = _registry_rows(cfg)
    builtin_names = {row["name"] for row in rows}
    global_categories = tool_loop.get("categories", ["info", "desktop", "memory", "artifacts"])
    global_excluded = tool_loop.get("exclude_tools", [])
    if not isinstance(global_categories, list):
        global_categories = []
    if not isinstance(global_excluded, list):
        global_excluded = []
    global_default_tools = [
        row["name"] for row in rows
        if row["category"] in global_categories
        and row["name"] not in global_excluded
        and not row.get("frozen")
    ]
    exposure_cfg = cfg.get("tool_exposure") if isinstance(cfg.get("tool_exposure"), dict) else {}
    path_c_legacy = tool_loop.get("categories", ["info", "desktop", "memory", "artifacts"])
    path_c_excludes = tool_loop.get("exclude_tools", [])
    path_exposure = {}
    for path, defaults in {
        "path_a": {"categories": ["info", "desktop"], "tools": None, "exclude_tools": []},
        "path_c": {"categories": path_c_legacy, "tools": None, "exclude_tools": path_c_excludes},
    }.items():
        block = exposure_cfg.get(path) if isinstance(exposure_cfg.get(path), dict) else {}
        path_exposure[path] = {
            "categories": block.get("categories", defaults["categories"]),
            "tools": block.get("tools", defaults["tools"]),
            "exclude_tools": block.get("exclude_tools", defaults["exclude_tools"]),
        }
    tool_presets = [
        {"name": item["name"], "tools": [tool for tool in item["tools"] if tool in builtin_names]}
        for item in normalize_tool_presets(tool_loop.get("tool_presets"))
    ]
    return {
        "tools": rows,
        "tool_presets": tool_presets,
        "model_bindings": {
            name: preset.get("tool_preset")
            for name, preset in model_presets.items()
            if isinstance(preset, dict) and preset.get("tool_preset")
        },
        "model_presets": sorted(model_presets),
        "legacy_mode": "model_presets" not in cfg,
        "mcp_enabled": bool(cfg.get("mcp_servers", {}).get("enabled", False)),
        "intiface_opt_in": bool((cfg.get("hardware") or {}).get("intiface_opt_in") is True),
        "global_default_tools": global_default_tools,
        "global_categories": global_categories,
        "global_exclude_tools": global_excluded,
        "path_exposure": path_exposure,
    }


@router.get("/settings/tools", summary="Read registered tools and model-specific exposure presets")
async def get_tool_controls(auth=Depends(require_scopes("admin"))):
    return _response(get_config())


def _validate_presets(raw: list[ToolPresetInput], registry_names: set[str]) -> list[dict]:
    if len(raw) > _MAX_PRESETS:
        raise HTTPException(status_code=422, detail=f"最多保存 {_MAX_PRESETS} 个工具预设")
    result: list[dict] = []
    names: set[str] = set()
    for preset in raw:
        name = preset.name.strip()
        if not name or len(name) > 64:
            raise HTTPException(status_code=422, detail="工具预设名称需为 1-64 个字符")
        if name in names:
            raise HTTPException(status_code=422, detail=f"工具预设名称重复: {name}")
        if len(preset.tools) > _MAX_TOOLS_PER_PRESET:
            raise HTTPException(status_code=422, detail=f"预设 {name} 最多包含 {_MAX_TOOLS_PER_PRESET} 个工具")
        tools = list(dict.fromkeys(tool.strip() for tool in preset.tools if tool.strip()))
        unknown = [tool for tool in tools if tool not in registry_names]
        if unknown:
            raise HTTPException(status_code=422, detail=f"预设 {name} 含未注册工具: {', '.join(unknown)}")
        names.add(name)
        result.append({"name": name, "tools": tools})
    return result


@router.put("/settings/tools", summary="Update tool exposure presets and execution switches")
async def update_tool_controls(body: ToolControlUpdate, auth=Depends(require_scopes("admin"))):
    full_cfg = read_config_file(CONFIG_FILE)
    cfg_for_validation = get_config()
    rows = _registry_rows(cfg_for_validation)
    registry_names = {row["name"] for row in rows}
    builtin_names = {row["name"] for row in rows}

    tool_loop = full_cfg.setdefault("tool_loop", {})
    if body.tool_presets is not None:
        saved_presets = _validate_presets(body.tool_presets, registry_names)
        tool_loop["tool_presets"] = saved_presets
        # Deleting a preset must not leave a dangling model binding that would
        # silently fail closed on the next chat turn.
        configured_models = full_cfg.get("model_presets", {}).get("presets", {})
        if isinstance(configured_models, dict):
            valid_names = {item["name"] for item in saved_presets}
            for preset in configured_models.values():
                if isinstance(preset, dict) and preset.get("tool_preset") not in valid_names:
                    preset.pop("tool_preset", None)
    else:
        saved_presets = normalize_tool_presets(tool_loop.get("tool_presets"))
    preset_names = {item["name"] for item in saved_presets}

    model_presets = full_cfg.get("model_presets", {}).get("presets")
    if body.model_bindings is not None:
        if not isinstance(model_presets, dict):
            raise HTTPException(status_code=409, detail="当前为 legacy llm 配置；请先初始化 model_presets")
        for model_name, tool_preset in body.model_bindings.items():
            if model_name not in model_presets:
                raise HTTPException(status_code=422, detail=f"未知模型 preset: {model_name}")
            if tool_preset is not None and tool_preset not in preset_names:
                raise HTTPException(status_code=422, detail=f"未知工具预设: {tool_preset}")
            if tool_preset:
                model_presets[model_name]["tool_preset"] = tool_preset
            else:
                model_presets[model_name].pop("tool_preset", None)

    if body.execution_enabled is not None:
        unknown = set(body.execution_enabled) - builtin_names
        if unknown:
            raise HTTPException(status_code=422, detail=f"仅内置工具可设置执行开关: {', '.join(sorted(unknown))}")
        tools_config = full_cfg.setdefault("tools", {})
        for name, enabled in body.execution_enabled.items():
            current = tools_config.get(name)
            if isinstance(current, dict):
                current["enabled"] = enabled
            else:
                tools_config[name] = {"enabled": enabled}

    if body.global_default_tools is not None:
        selected = set(body.global_default_tools)
        unknown = selected - builtin_names
        if unknown:
            raise HTTPException(status_code=422, detail=f"全局默认含未注册工具: {', '.join(sorted(unknown))}")
        # Keep categories not represented by this built-in-only panel intact
        # (for example a currently enabled external category), without making
        # MCP a special case in the tool-control contract.
        builtin_categories = {row["category"] for row in rows}
        previous_categories = tool_loop.get("categories", ["info", "desktop", "memory"])
        if not isinstance(previous_categories, list):
            previous_categories = []
        preserved_categories = [
            category for category in previous_categories
            if category not in builtin_categories
        ]
        selected_categories = [
            category for category in dict.fromkeys(row["category"] for row in rows)
            if any(row["category"] == category and row["name"] in selected for row in rows)
        ]
        tool_loop["categories"] = preserved_categories + selected_categories

        previous_excluded = tool_loop.get("exclude_tools", [])
        if not isinstance(previous_excluded, list):
            previous_excluded = []
        preserved_excluded = [name for name in previous_excluded if name not in builtin_names]
        selected_category_set = set(selected_categories)
        derived_excluded = [
            row["name"] for row in rows
            if row["category"] in selected_category_set and row["name"] not in selected
        ]
        tool_loop["exclude_tools"] = list(dict.fromkeys(preserved_excluded + derived_excluded))

    if body.exposure is not None:
        from core.tool_dispatcher import _TOOL_REGISTRY

        all_registry_names = set(_TOOL_REGISTRY)
        exposure_cfg = full_cfg.setdefault("tool_exposure", {})
        for path, update in body.exposure.items():
            if path not in {"path_a", "path_c"}:
                raise HTTPException(status_code=422, detail=f"未知工具路径: {path}")
            block = exposure_cfg.setdefault(path, {})
            if update.categories is not None:
                block["categories"] = list(dict.fromkeys(str(v).strip() for v in update.categories if str(v).strip()))
            if update.tools is not None:
                selected = list(dict.fromkeys(str(v).strip() for v in update.tools if str(v).strip()))
                unknown = [name for name in selected if name not in all_registry_names]
                if unknown:
                    raise HTTPException(status_code=422, detail=f"{path} 含未注册工具: {', '.join(unknown)}")
                block["tools"] = selected
            if update.exclude_tools is not None:
                selected = list(dict.fromkeys(str(v).strip() for v in update.exclude_tools if str(v).strip()))
                unknown = [name for name in selected if name not in all_registry_names]
                if unknown:
                    raise HTTPException(status_code=422, detail=f"{path} 含未注册工具: {', '.join(unknown)}")
                block["exclude_tools"] = selected

    write_config_file(CONFIG_FILE, full_cfg)
    from core import config_loader
    config_loader.reload_config()
    return _response(get_config())
