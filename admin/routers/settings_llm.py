"""
LLM 生成参数配置接口
GET  /llm-params                  — 读取当前 chat preset 的生成参数
PUT  /llm-params                  — 修改当前 chat preset 的生成参数并热重载
GET  /vision-params               — 读取 vision 配置
PUT  /vision-params               — 修改 vision 配置并热重载
GET    /model-presets                        — 读取多模型 preset 配置（api_key 打码）
PUT    /model-presets/active-routing          — 切换当前生效的路由方案
PUT    /model-presets/presets/{name}          — 新增或更新一个 preset
DELETE /model-presets/presets/{name}          — 删除一个 preset（被 routing_profiles 引用时拒绝）
PUT    /model-presets/routing-profiles/{name} — 新增或更新一个 routing profile
POST   /model-presets/presets/{name}/test     — 连通性测试：发一条 1 token ping，返回延迟/错误
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
async def get_llm_params(auth=Depends(require_scopes("admin"))):
    """读取当前 chat preset 的生成参数（或 legacy llm: 块）。"""
    params = _get_chat_preset_params(get_config())
    return {
        "temperature":       float(params.get("temperature",       0.7)),
        "top_p":             float(params.get("top_p",             0.9)),
        "max_tokens":        int(params.get("max_tokens",          1000)),
        "frequency_penalty": float(params.get("frequency_penalty", 0.0)),
    }


@router.put("/llm-params", summary="修改 LLM 生成参数并热重载")
async def update_llm_params(body: LlmParamsUpdate, auth=Depends(require_scopes("admin"))):
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
async def get_vision_params(auth=Depends(require_scopes("admin"))):
    cfg = get_config().get("vision", {})
    return {
        "enabled":  cfg.get("enabled",  False),
        "provider": cfg.get("provider", ""),
        "api_key":  cfg.get("api_key",  ""),
        "model":    cfg.get("model",    ""),
        "base_url": cfg.get("base_url", ""),
    }


@router.put("/vision-params", summary="修改 Vision 配置并热重载")
async def update_vision_params(body: VisionParamsUpdate, auth=Depends(require_scopes("admin"))):
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
async def get_model_presets(auth=Depends(require_scopes("admin"))):
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


def _persist_model_presets(full_cfg: dict) -> None:
    """Persist config and invalidate every cached model client."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader, llm_client
    config_loader.reload_config()
    llm_client.reload_client()


@router.post("/model-presets/bootstrap", summary="从 legacy llm 配置初始化 model_presets")
async def bootstrap_model_presets(auth=Depends(require_scopes("admin"))):
    """One-time migration used by the visual admin panel."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    if full_cfg.get("model_presets"):
        return {"message": "model_presets 已存在，无需初始化", "created": False}

    from core.model_registry import _synth_legacy_presets
    full_cfg["model_presets"] = _synth_legacy_presets(full_cfg)
    _persist_model_presets(full_cfg)
    return {"message": "已从 legacy llm 配置初始化 model_presets", "created": True}


@router.get("/settings/model-routing", summary="桌面端读取可选模型路由")
async def get_desktop_model_routing(auth=Depends(require_scopes("persona"))):
    """Return safe display data; API keys and endpoint credentials stay admin-only."""
    from core.model_registry import _get_preset_config
    mp = _get_preset_config()
    presets = mp.get("presets", {})
    profiles = mp.get("routing_profiles", {})
    rows = []
    for name, profile in profiles.items():
        preset_name = profile.get("chat") or next(iter(presets), "")
        preset = presets.get(preset_name, {})
        rows.append({
            "name": name,
            "chat_preset": preset_name,
            "provider_kind": preset.get("provider_kind", "openai"),
            "model": preset.get("model", ""),
            "tool_call_mode": preset.get("tool_call_mode", "function_calling"),
        })
    return {
        "active_routing": mp.get("active_routing", "default"),
        "profiles": rows,
        "is_legacy_synth": "model_presets" not in get_config(),
    }


@router.put("/settings/model-routing", summary="桌面端切换已有模型路由")
async def set_desktop_model_routing(body: ActiveRoutingUpdate, auth=Depends(require_scopes("persona"))):
    """Allow desktop selection without exposing preset secrets."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    mp = full_cfg.get("model_presets")
    if not mp:
        raise HTTPException(status_code=409, detail="请先在管理面板初始化 model_presets")
    if body.active_routing not in mp.get("routing_profiles", {}):
        raise HTTPException(status_code=422, detail="未知 routing profile")

    mp["active_routing"] = body.active_routing
    _persist_model_presets(full_cfg)
    return {"message": "模型路由已切换", "active_routing": body.active_routing}


@router.get("/model-presets/routing-profiles", summary="可选 routing profile 清单（角色绑定下拉框数据源）")
async def list_routing_profiles(auth=Depends(require_scopes("persona"))):
    """返回全部 routing profile 名 + 各 category→preset 映射摘要（Brief 87 §1）。

    persona scope（非 admin-only）：不暴露 preset 的 api_key/base_url，
    只暴露 profile 结构本身，供角色模型绑定下拉框使用。
    """
    from core.model_registry import _get_preset_config
    mp = _get_preset_config()
    profiles = mp.get("routing_profiles", {})
    return {
        "active_routing": mp.get("active_routing", "default"),
        "profiles": [{"name": name, "categories": dict(mapping)} for name, mapping in profiles.items()],
    }


@router.put("/model-presets/active-routing", summary="切换当前生效的路由方案")
async def set_active_routing(body: ActiveRoutingUpdate, auth=Depends(require_scopes("admin"))):
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


# ---------------------------------------------------------------------------
# /model-presets/presets/{name} — preset CRUD（Phase 4）
# ---------------------------------------------------------------------------

class PresetUpsert(BaseModel):
    provider_kind: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    tool_call_mode: Optional[str] = None
    prompt_style: Optional[str] = None
    params: Optional[dict] = None
    reasoning_native: Optional[bool] = None
    reasoning_extra_body: Optional[dict] = None


def _require_model_presets_block(full_cfg: dict) -> dict:
    mp = full_cfg.get("model_presets")
    if not mp:
        raise HTTPException(
            status_code=400,
            detail="当前配置使用 legacy llm: 块，不支持 preset/routing profile 管理。请先配置 model_presets 块。",
        )
    return mp


@router.put("/model-presets/presets/{name}", summary="新增或更新一个 model preset")
async def upsert_preset(name: str, body: PresetUpsert, auth=Depends(require_scopes("admin"))):
    """合并更新指定 preset；preset 不存在时新建（新建必须提供 provider_kind）。"""
    from core.model_registry import PROVIDER_PROFILES
    if body.provider_kind is not None and body.provider_kind not in PROVIDER_PROFILES:
        raise HTTPException(
            status_code=422,
            detail=f"未知 provider_kind: {body.provider_kind!r}，可选: {sorted(PROVIDER_PROFILES)}",
        )

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    mp = _require_model_presets_block(full_cfg)
    presets = mp.setdefault("presets", {})
    is_new = name not in presets
    if is_new and body.provider_kind is None:
        raise HTTPException(status_code=422, detail="新建 preset 必须提供 provider_kind")

    existing = dict(presets.get(name, {}))
    update_data = body.model_dump(exclude_none=True)

    if "params" in update_data and "params" in existing:
        merged_params = dict(existing["params"])
        merged_params.update(update_data.pop("params"))
        existing["params"] = merged_params
    elif "params" in update_data:
        existing["params"] = update_data.pop("params")

    existing.update(update_data)
    presets[name] = existing

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader, llm_client
    config_loader.reload_config()
    llm_client.reload_client()

    return {
        "message": f"preset '{name}' 已{'创建' if is_new else '更新'}",
        "name": name,
        "preset": _mask_presets({name: presets[name]})[name],
    }


@router.delete("/model-presets/presets/{name}", summary="删除一个 model preset")
async def delete_preset(name: str, auth=Depends(require_scopes("admin"))):
    """删除指定 preset。仍被某个 routing profile 的任意 call_category 引用时拒绝（409），
    唯一剩余的 preset 也拒绝删除（409），避免路由解析无 preset 可用。
    """
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    mp = _require_model_presets_block(full_cfg)
    presets = mp.get("presets", {})
    if name not in presets:
        raise HTTPException(status_code=404, detail=f"preset {name!r} 不存在")
    if len(presets) <= 1:
        raise HTTPException(status_code=409, detail="不能删除唯一的 preset，至少保留一个")

    referencing = [
        f"{profile_name}.{category}"
        for profile_name, profile in mp.get("routing_profiles", {}).items()
        for category, preset_name in profile.items()
        if preset_name == name
    ]
    if referencing:
        raise HTTPException(
            status_code=409,
            detail=f"preset {name!r} 仍被以下 routing profile 引用，请先改指向再删除: {referencing}",
        )

    del presets[name]

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader, llm_client
    config_loader.reload_config()
    llm_client.reload_client()

    return {"message": f"preset {name!r} 已删除", "name": name}


# ---------------------------------------------------------------------------
# /model-presets/routing-profiles/{name} — routing profile CRUD（Phase 4）
# ---------------------------------------------------------------------------

@router.put("/model-presets/routing-profiles/{name}", summary="新增或更新一个 routing profile")
async def upsert_routing_profile(name: str, body: dict[str, str], auth=Depends(require_scopes("admin"))):
    """合并更新指定 routing profile 的 call_category → preset 映射。

    body 例：{"chat": "claude-sonnet", "probe": "deepseek-default"}
    只传入需要修改的 category；未传入的沿用已有映射。所有值必须是已存在的 preset 名。
    """
    if not body:
        raise HTTPException(status_code=422, detail="body 不能为空，至少提供一个 call_category")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    mp = _require_model_presets_block(full_cfg)
    presets = mp.get("presets", {})
    unknown = sorted({v for v in body.values() if v not in presets})
    if unknown:
        raise HTTPException(status_code=422, detail=f"routing profile 引用了不存在的 preset: {unknown}")

    profiles = mp.setdefault("routing_profiles", {})
    profile = dict(profiles.get(name, {}))
    profile.update(body)
    profiles[name] = profile

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader, llm_client
    config_loader.reload_config()
    llm_client.reload_client()

    return {"message": f"routing profile '{name}' 已更新", "name": name, "profile": profile}


# ---------------------------------------------------------------------------
# /model-presets/presets/{name}/test — 连通性测试（Phase 4）
# ---------------------------------------------------------------------------

@router.post("/model-presets/presets/{name}/test", summary="连通性测试：发一条 1 token ping")
async def test_preset_connectivity(name: str, auth=Depends(require_scopes("admin"))):
    """用该 preset 实际发一条 max_tokens=1 的请求，返回延迟/错误，不写入任何缓存。"""
    from core.model_registry import build_client_for_preset
    import time as _time

    try:
        client = build_client_for_preset(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    t0 = _time.monotonic()
    try:
        resp = await client.client.chat.completions.create(
            model=client.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            timeout=15.0,
        )
        latency_ms = round((_time.monotonic() - t0) * 1000, 1)
        reply_preview = ""
        try:
            reply_preview = (resp.choices[0].message.content or "")[:20]
        except Exception:
            pass
        return {
            "ok": True, "name": name, "model": client.model,
            "latency_ms": latency_ms, "reply_preview": reply_preview,
        }
    except Exception as e:
        latency_ms = round((_time.monotonic() - t0) * 1000, 1)
        return {
            "ok": False, "name": name, "model": client.model,
            "latency_ms": latency_ms, "error": str(e)[:300],
        }
    finally:
        try:
            await client.client.close()
        except Exception:
            pass
