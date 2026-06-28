"""
LLM 生成参数配置接口
GET  /llm-params                  — 读取当前 chat preset 的生成参数
PUT  /llm-params                  — 修改当前 chat preset 的生成参数并热重载
GET  /vision-params               — 读取 vision 配置
PUT  /vision-params               — 修改 vision 配置并热重载
GET  /model-presets               — 读取多模型 preset 配置（api_key 打码）
PUT  /model-presets/active-routing — 切换当前生效的路由方案
"""

from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import verify_token
from core.config_loader import get_config

router = APIRouter()
CONFIG_FILE = Path("config.yaml")


# ---------------------------------------------------------------------------
# /llm-params — 读写当前 chat preset 的生成参数
# （旧扁平 llm: 块兼容：没有 model_presets 时读写 llm: 块）
# ---------------------------------------------------------------------------

class LlmParamsUpdate(BaseModel):
    temperature:       Optional[float] = None
    top_p:             Optional[float] = None
    max_tokens:        Optional[int]   = None
    frequency_penalty: Optional[float] = None


def _get_chat_preset_params(cfg: dict) -> dict:
    """返回当前 chat preset 的生成参数（provider 白名单过滤后，与真实发送值一致）。"""
    mp = cfg.get("model_presets")
    if mp:
        active = mp.get("active_routing", "default")
        profiles = mp.get("routing_profiles", {})
        profile = profiles.get(active) or (next(iter(profiles.values())) if profiles else {})
        preset_name = profile.get("chat") or next(iter(mp.get("presets", {})), None)
        if preset_name:
            from core.model_registry import resolve_params
            preset = mp.get("presets", {}).get(preset_name, {})
            defaults = mp.get("defaults", {})
            kind = preset.get("provider_kind", "openai")
            return resolve_params(defaults, preset.get("params", {}), kind)
    return cfg.get("llm", {})


@router.get("/llm-params", summary="获取 LLM 生成参数")
async def get_llm_params(auth=Depends(verify_token)):
    """读取当前 chat preset 的生成参数（或 legacy llm: 块）。"""
    params = _get_chat_preset_params(get_config())
    return {
        "temperature":       float(params.get("temperature",       0.7)),
        "top_p":             float(params.get("top_p",             0.9)),
        "max_tokens":        int(params.get("max_tokens",          1000)),
        "frequency_penalty": float(params.get("frequency_penalty", 0.0)),
    }


@router.put("/llm-params", summary="修改 LLM 生成参数并热重载")
async def update_llm_params(body: LlmParamsUpdate, auth=Depends(verify_token)):
    """修改当前 chat preset 的生成参数并热重载。
    legacy 模式（无 model_presets 块）写回 llm: 块，保持旧行为。
    """
    if body.temperature is not None and not (0.0 <= body.temperature <= 2.0):
        raise HTTPException(status_code=422, detail="temperature 必须在 0.0~2.0 之间")
    if body.top_p is not None and not (0.0 <= body.top_p <= 1.0):
        raise HTTPException(status_code=422, detail="top_p 必须在 0.0~1.0 之间")
    if body.max_tokens is not None and not (100 <= body.max_tokens <= 4000):
        raise HTTPException(status_code=422, detail="max_tokens 必须在 100~4000 之间")
    if body.frequency_penalty is not None and not (0.0 <= body.frequency_penalty <= 2.0):
        raise HTTPException(status_code=422, detail="frequency_penalty 必须在 0.0~2.0 之间")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}

    mp = full_cfg.get("model_presets")
    if mp:
        # Write into the chat preset's params section
        active = mp.get("active_routing", "default")
        profiles = mp.get("routing_profiles", {})
        profile = profiles.get(active) or (next(iter(profiles.values())) if profiles else {})
        preset_name = profile.get("chat") or next(iter(mp.get("presets", {})), None)
        if preset_name and preset_name in mp.get("presets", {}):
            mp["presets"][preset_name].setdefault("params", {}).update(updates)
        target_params = mp.get("presets", {}).get(preset_name, {}).get("params", {})
    else:
        # Legacy: write into flat llm: block
        llm_cfg = full_cfg.setdefault("llm", {})
        llm_cfg.update(updates)
        target_params = llm_cfg

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader, llm_client
    config_loader.reload_config()
    llm_client.reload_client()
    return {"message": "LLM 参数已更新", "params": {k: target_params[k] for k in updates if k in target_params}}


# ---------------------------------------------------------------------------
# /vision-params
# ---------------------------------------------------------------------------

class VisionParamsUpdate(BaseModel):
    enabled:  Optional[bool]  = None
    provider: Optional[str]   = None
    api_key:  Optional[str]   = None
    model:    Optional[str]   = None
    base_url: Optional[str]   = None


@router.get("/vision-params", summary="获取 Vision 配置")
async def get_vision_params(auth=Depends(verify_token)):
    cfg = get_config().get("vision", {})
    return {
        "enabled":  cfg.get("enabled",  False),
        "provider": cfg.get("provider", ""),
        "api_key":  cfg.get("api_key",  ""),
        "model":    cfg.get("model",    ""),
        "base_url": cfg.get("base_url", ""),
    }


@router.put("/vision-params", summary="修改 Vision 配置并热重载")
async def update_vision_params(body: VisionParamsUpdate, auth=Depends(verify_token)):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    vision_cfg = full_cfg.setdefault("vision", {})
    if body.enabled  is not None: vision_cfg["enabled"]  = body.enabled
    if body.provider is not None: vision_cfg["provider"] = body.provider
    if body.api_key  is not None: vision_cfg["api_key"]  = body.api_key
    if body.model    is not None: vision_cfg["model"]    = body.model
    if body.base_url is not None: vision_cfg["base_url"] = body.base_url

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader, llm_client
    config_loader.reload_config()
    llm_client.reload_client()
    return {"message": "Vision 配置已更新", "vision": vision_cfg}


# ---------------------------------------------------------------------------
# /model-presets — Phase 3 endpoints
# ---------------------------------------------------------------------------

def _mask_key(key: str) -> str:
    if not key or len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


def _mask_presets(presets: dict) -> dict:
    masked = {}
    for name, p in presets.items():
        entry = dict(p)
        if "api_key" in entry:
            entry["api_key"] = _mask_key(entry["api_key"])
        masked[name] = entry
    return masked


@router.get("/model-presets", summary="获取多模型 preset 配置")
async def get_model_presets(auth=Depends(verify_token)):
    """返回 presets 列表（api_key 打码）、routing_profiles、active_routing。
    若配置中无 model_presets 块，返回合成的 legacy 视图。
    """
    from core.model_registry import _get_preset_config
    mp = _get_preset_config()
    return {
        "active_routing":    mp.get("active_routing", "default"),
        "presets":           _mask_presets(mp.get("presets", {})),
        "routing_profiles":  mp.get("routing_profiles", {}),
        "defaults":          mp.get("defaults", {}),
        "is_legacy_synth":   "model_presets" not in get_config(),
    }


class ActiveRoutingUpdate(BaseModel):
    active_routing: str


@router.put("/model-presets/active-routing", summary="切换当前生效的路由方案")
async def set_active_routing(body: ActiveRoutingUpdate, auth=Depends(verify_token)):
    """切换 active_routing（如 'default' → 'claude-main'）并热重载。
    只支持已有 model_presets 块的配置；legacy 模式下无意义，会返回 400。
    """
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    mp = full_cfg.get("model_presets")
    if not mp:
        raise HTTPException(
            status_code=400,
            detail="当前配置使用 legacy llm: 块，不支持切换路由方案。请先配置 model_presets 块。",
        )

    profiles = mp.get("routing_profiles", {})
    if body.active_routing not in profiles:
        raise HTTPException(
            status_code=422,
            detail=f"routing profile '{body.active_routing}' 不存在。可用: {list(profiles.keys())}",
        )

    mp["active_routing"] = body.active_routing

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader, llm_client
    config_loader.reload_config()
    llm_client.reload_client()
    return {"message": f"已切换到路由方案 '{body.active_routing}'", "active_routing": body.active_routing}
