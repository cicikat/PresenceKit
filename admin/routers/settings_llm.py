"""
LLM 生成参数配置接口
GET  /llm-params                  — 读取当前 chat preset 的生成参数
PUT  /llm-params                  — 修改当前 chat preset 的生成参数并热重载
GET  /vision-params               — 读取 vision 配置
PUT  /vision-params               — 修改 vision 配置并热重载
GET  /vision-params/phone-control — 读取手机自动化视觉覆盖
PUT  /vision-params/phone-control — 修改手机自动化视觉覆盖并热重载
GET/PUT /llm-debug-requests       — 管理高敏感 LLM 请求快照开关与保留期
GET    /model-presets                        — 读取多模型 preset 配置（api_key 打码）
PUT    /model-presets/active-routing          — 切换当前生效的路由方案
PUT    /model-presets/presets/{name}          — 新增或更新一个 preset
DELETE /model-presets/presets/{name}          — 删除一个 preset（被 routing_profiles 引用时拒绝）
PUT    /model-presets/routing-profiles/{name} — 新增或更新一个 routing profile
POST   /model-presets/presets/{name}/test     — 连通性测试：发一条 1 token ping，返回延迟/错误
"""

import json
import logging
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config

router = APIRouter()
CONFIG_FILE = Path("config.yaml")
logger = logging.getLogger(__name__)


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

    full_cfg = read_config_file(CONFIG_FILE)

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

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader, llm_client
    config_loader.reload_config()
    await llm_client.reload_client()
    return {"message": "LLM 参数已更新", "params": {k: target_params[k] for k in updates if k in target_params}}


# ---------------------------------------------------------------------------
# /llm-debug-requests — explicit opt-in full semantic request snapshots
# ---------------------------------------------------------------------------

class LlmDebugRequestsUpdate(BaseModel):
    enabled: Optional[bool] = None
    keep_days: Optional[int] = None


def _llm_debug_request_settings(cfg: dict) -> dict:
    raw = cfg.get("llm_debug_requests", {})
    raw = raw if isinstance(raw, dict) else {}
    try:
        keep_days = int(raw.get("keep_days", 1))
    except (TypeError, ValueError):
        keep_days = 1
    return {"enabled": bool(raw.get("enabled", False)), "keep_days": max(1, min(7, keep_days))}


@router.get("/llm-debug-requests", summary="读取 LLM 请求快照调试开关")
async def get_llm_debug_requests(_auth=Depends(require_scopes("admin"))):
    """Admin-only: snapshots contain prompt text and tool schemas."""
    return _llm_debug_request_settings(get_config())


@router.put("/llm-debug-requests", summary="更新 LLM 请求快照调试开关")
async def update_llm_debug_requests(body: LlmDebugRequestsUpdate, _auth=Depends(require_scopes("admin"))):
    if body.keep_days is not None and not 1 <= body.keep_days <= 7:
        raise HTTPException(status_code=422, detail="keep_days 必须在 1~7 之间")
    full_cfg = read_config_file(CONFIG_FILE)

    target = full_cfg.setdefault("llm_debug_requests", {})
    for field in body.model_fields_set:
        setattr_value = getattr(body, field)
        if setattr_value is not None:
            target[field] = setattr_value
    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader
    config_loader.reload_config()
    return _llm_debug_request_settings(full_cfg)


# ---------------------------------------------------------------------------
# /settings/base-model — 配置中心 §1「必填」：基础聊天模型连接
# 透明兼容 model_presets 与 legacy llm: 两种模式，写回目标由现有路由解析规则决定，
# 不引入第三套真值来源（Brief 93 §1）。
# ---------------------------------------------------------------------------

def _looks_placeholder(value) -> bool:
    """config.example.yaml 里的占位符统一 YOUR_ / YOUR- 前缀（见 PLACEHOLDER_ADMIN_SECRET 同一约定）。"""
    v = str(value or "").strip()
    if not v:
        return True
    upper = v.upper()
    return upper.startswith("YOUR_") or upper.startswith("YOUR-")


def _resolve_base_chat_preset_name(cfg: dict) -> Optional[str]:
    """当前 chat call_category 解析到的 preset 名；无 model_presets 块时返回 None（走 legacy llm:）。"""
    mp = cfg.get("model_presets")
    if not mp:
        return None
    active = mp.get("active_routing", "default")
    profiles = mp.get("routing_profiles", {})
    profile = profiles.get(active) or (next(iter(profiles.values())) if profiles else {})
    return profile.get("chat") or next(iter(mp.get("presets", {})), None)


def _base_model_view(cfg: dict) -> dict:
    preset_name = _resolve_base_chat_preset_name(cfg)
    if preset_name:
        target = cfg.get("model_presets", {}).get("presets", {}).get(preset_name, {})
    else:
        target = cfg.get("llm", {})
    base_url = target.get("base_url", "")
    api_key  = target.get("api_key", "")
    model    = target.get("model", "")
    configured = not (_looks_placeholder(base_url) or _looks_placeholder(api_key) or _looks_placeholder(model))
    return {
        "mode": "preset" if preset_name else "legacy",
        "preset_name": preset_name,
        "base_url": "" if _looks_placeholder(base_url) else base_url,
        "model": "" if _looks_placeholder(model) else model,
        "api_key_masked": _mask_key(api_key) if api_key and not _looks_placeholder(api_key) else "",
        "api_key_set": bool(api_key) and not _looks_placeholder(api_key),
        "configured": configured,
    }


@router.get("/settings/base-model", summary="读取基础聊天模型连接（配置中心 §1 必填项）")
async def get_base_model(auth=Depends(require_scopes("admin"))):
    return _base_model_view(get_config())


class BaseModelUpdate(BaseModel):
    base_url: Optional[str] = None
    api_key:  Optional[str] = None
    model:    Optional[str] = None


class ModelDiscoveryRequest(BaseModel):
    base_url: str
    api_key: str = ""
    preset_name: Optional[str] = None
    use_base_model: bool = False
    api_protocol: Literal["chat_completions", "responses", "anthropic_messages"] = "chat_completions"
    anthropic_auth_mode: Literal["x_api_key", "bearer"] = "x_api_key"


@router.post("/model-presets/discover", summary="查询连接可用模型（不保存配置）")
async def discover_models(body: ModelDiscoveryRequest, auth=Depends(require_scopes("admin"))):
    from core.model_discovery import catalogue_url, discover
    cfg = get_config()
    saved = {}
    name = body.preset_name
    if body.use_base_model:
        name = _resolve_base_chat_preset_name(cfg)
        if name is None:
            saved = cfg.get("llm", {})
    if name is not None:
        saved = cfg.get("model_presets", {}).get("presets", {}).get(name)
        if saved is None:
            raise HTTPException(404, "模型连接不存在")
    try:
        requested_url = catalogue_url(body.base_url)
        key = body.api_key.strip()
        if not key and saved.get("api_key"):
            # Editing the address must not forward the saved credential to a new destination.
            if requested_url != catalogue_url(saved.get("base_url", "")):
                return {"status": "key_required", "models": []}
            key = saved["api_key"]
        return await discover(body.base_url, key, body.api_protocol, body.anthropic_auth_mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/settings/base-model", summary="写入基础聊天模型连接并热重载（配置中心 §1 必填项）")
async def update_base_model(body: BaseModelUpdate, auth=Depends(require_scopes("admin"))):
    updates = {k: v.strip() for k, v in body.model_dump().items() if v is not None and v.strip() != ""}
    if not updates:
        raise HTTPException(status_code=422, detail="至少提供 base_url / api_key / model 之一")

    full_cfg = read_config_file(CONFIG_FILE)

    preset_name = _resolve_base_chat_preset_name(full_cfg)
    if preset_name:
        target = full_cfg.setdefault("model_presets", {}).setdefault("presets", {}).setdefault(preset_name, {})
    else:
        target = full_cfg.setdefault("llm", {})
    target.update(updates)

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader, llm_client
    config_loader.reload_config()
    await llm_client.reload_client()
    return _base_model_view(get_config())


# ---------------------------------------------------------------------------
# /settings/embedding — 配置中心 §1「强烈建议」：语义 Embedding（长期记忆语义召回）
# 缺失时召回自动降级为关键词路径，fail-open 不崩，不属于必填项。
# ---------------------------------------------------------------------------

def _embedding_view(cfg: dict) -> dict:
    target = cfg.get("embedding", {})
    base_url = target.get("base_url", "")
    api_key  = target.get("api_key", "")
    model    = target.get("model", "")
    dim      = target.get("dim")
    configured = not (_looks_placeholder(base_url) or _looks_placeholder(api_key) or _looks_placeholder(model)) and bool(dim)
    return {
        "base_url": "" if _looks_placeholder(base_url) else base_url,
        "model": "" if _looks_placeholder(model) else model,
        "dim": dim,
        "api_key_masked": _mask_key(api_key) if api_key and not _looks_placeholder(api_key) else "",
        "api_key_set": bool(api_key) and not _looks_placeholder(api_key),
        "configured": configured,
    }


@router.get("/settings/embedding", summary="读取语义 Embedding 配置（配置中心 §1 强烈建议项）")
async def get_embedding_settings(auth=Depends(require_scopes("admin"))):
    return _embedding_view(get_config())


class EmbeddingSettingsUpdate(BaseModel):
    base_url: Optional[str] = None
    api_key:  Optional[str] = None
    model:    Optional[str] = None
    dim:      Optional[int] = None


@router.put("/settings/embedding", summary="写入语义 Embedding 配置并热重载（配置中心 §1 强烈建议项）")
async def update_embedding_settings(body: EmbeddingSettingsUpdate, auth=Depends(require_scopes("admin"))):
    if body.dim is not None and not (1 <= body.dim <= 8192):
        raise HTTPException(status_code=422, detail="dim 必须在 1~8192 之间（须与 model 实际输出维度一致）")

    updates: dict = {}
    for key in ("base_url", "api_key", "model"):
        v = getattr(body, key)
        if v is not None and v.strip() != "":
            updates[key] = v.strip()
    if body.dim is not None:
        updates["dim"] = body.dim
    if not updates:
        raise HTTPException(status_code=422, detail="至少提供 base_url / api_key / model / dim 之一")

    full_cfg = read_config_file(CONFIG_FILE)

    full_cfg.setdefault("embedding", {}).update(updates)

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader
    config_loader.reload_config()
    return _embedding_view(get_config())


# ---------------------------------------------------------------------------
# /settings/setup-status — 配置中心整体状态：首启自动跳转 + 顶部缺项横幅判定
# ---------------------------------------------------------------------------

@router.get("/settings/setup-status", summary="配置中心整体状态（首启自动跳转 + 缺项横幅判定）")
async def get_setup_status(auth=Depends(require_scopes("admin"))):
    from admin.routers.scheduler import owner_status

    cfg = get_config()
    base = _base_model_view(cfg)
    embedding = _embedding_view(cfg)
    owner = owner_status(cfg)
    return {
        "base_chat": base,
        "embedding": embedding,
        "owner": owner,
        # owner_id 升为必填②（Brief 95 §1）：任一缺失都触发首启自动跳转 + 顶部横幅
        "needs_setup": not base["configured"] or not owner["configured"],
    }


# ---------------------------------------------------------------------------
# /vision-params
# ---------------------------------------------------------------------------

class VisionParamsUpdate(BaseModel):
    api_protocol: Optional[Literal["chat_completions", "responses", "anthropic_messages"]] = None
    enabled:  Optional[bool]  = None
    provider: Optional[str]   = None
    api_key:  Optional[str]   = None
    model:    Optional[str]   = None
    base_url: Optional[str]   = None


class PhoneControlVisionParamsUpdate(BaseModel):
    """phone_control_vision 只存显式覆盖；空值删除字段并回退通用 vision。"""

    enabled: Optional[bool] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None


@router.get("/vision-params", summary="获取 Vision 配置")
async def get_vision_params(auth=Depends(require_scopes("admin"))):
    cfg = get_config().get("vision", {})
    return {
        "enabled":  cfg.get("enabled",  False),
        "provider": cfg.get("provider", ""),
        "api_key":  "",
        "has_api_key": bool(cfg.get("api_key")),
        "model":    cfg.get("model",    ""),
        "base_url": cfg.get("base_url", ""),
        "api_protocol": cfg.get("api_protocol", "chat_completions"),
    }


@router.post("/image-recognition/test/{connection}", summary="测试已保存的图像连接")
async def test_image_connection(
    connection: Literal["general", "ocr", "phone"],
    auth=Depends(require_scopes("admin")),
):
    """One synthetic image, no user media or automation, no config mutation."""
    import asyncio
    import base64
    import io
    import time
    from PIL import Image, ImageDraw
    from core import image_recognition, api_call_log
    from core.llm_client import _make_http_client, _get_proxy_url
    from openai import AsyncOpenAI

    cfg = get_config()
    vision = dict(cfg.get("vision") or {})
    if connection == "phone":
        vision.update({k: v for k, v in (cfg.get("phone_control_vision") or {}).items()
                       if v is not None and v != ""})
    if connection != "ocr" and not (vision.get("enabled") and vision.get("base_url") and vision.get("model")):
        raise HTTPException(422, "Vision connection is disabled or incomplete")
    if connection == "ocr" and not image_recognition.view(cfg)["configured"]:
        raise HTTPException(422, "OCR connection is incomplete")
    image = Image.new("RGB", (80, 25), "white")
    ImageDraw.Draw(image).text((8, 6), "TEST 123", fill="black")
    image = image.resize((320, 100))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    started = time.monotonic()
    ok, error = False, ""
    try:
        async def probe():
            if connection == "ocr":
                return await image_recognition.recognize_ocr(uri, image_recognition.settings(cfg))
            async with AsyncOpenAI(api_key=vision.get("api_key") or "none", base_url=vision["base_url"],
                http_client=_make_http_client(_get_proxy_url()), timeout=20, max_retries=0) as client:
                if vision.get("api_protocol", "chat_completions") == "responses":
                    response = await client.responses.create(model=vision["model"], max_output_tokens=32,
                        input=[{"role": "user", "content": [{"type": "input_text", "text": "Read the text in this image. Return only that text."}, {"type": "input_image", "image_url": uri}]}])
                    return getattr(response, "output_text", "") or ""
                if vision.get("api_protocol") == "anthropic_messages":
                    import httpx
                    headers = {"x-api-key": vision.get("api_key") or "", "anthropic-version": "2023-06-01"}
                    async with httpx.AsyncClient(timeout=20) as hc:
                        rr = await hc.post(vision["base_url"].rstrip("/") + "/v1/messages", headers=headers, json={"model": vision["model"], "max_tokens": 32, "messages": [{"role": "user", "content": [{"type": "text", "text": "Read the text in this image. Return only that text."}, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": uri.split(",", 1)[1]}}]}]})
                        rr.raise_for_status()
                        return "".join(x.get("text", "") for x in rr.json().get("content", []) if x.get("type") == "text")
                response = await client.chat.completions.create(model=vision["model"], max_tokens=32,
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": "Read the text in this image. Return only that text."},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ]}])
                return response.choices[0].message.content if response.choices else ""
        from core.conversation_stats import exclude_diagnostic
        with exclude_diagnostic():
            answer = await asyncio.wait_for(probe(), timeout=25)
        ok = bool(answer and answer.strip() and answer.strip() != "[OCR: no text detected]")
        error = "" if ok else "empty_response"
    except Exception as exc:
        error = type(exc).__name__
    duration = int((time.monotonic() - started) * 1000)
    api_call_log.append(caller="admin_image_test", purpose=connection, provider="image_test",
                        model=str(vision.get("model") or "") if connection != "ocr" else str(image_recognition.settings(cfg).get("model") or ""),
                        duration_ms=duration, ok=ok, error_category=error)
    return {"ok": ok, "connection": connection, "duration_ms": duration, "error_category": error}


@router.put("/vision-params", summary="修改 Vision 配置并热重载")
async def update_vision_params(body: VisionParamsUpdate, auth=Depends(require_scopes("admin"))):
    full_cfg = read_config_file(CONFIG_FILE)

    vision_cfg = full_cfg.setdefault("vision", {})
    if body.enabled  is not None: vision_cfg["enabled"]  = body.enabled
    if body.provider is not None: vision_cfg["provider"] = body.provider
    if body.api_key and body.api_key.strip(): vision_cfg["api_key"] = body.api_key.strip()
    if body.model    is not None: vision_cfg["model"]    = body.model
    if body.base_url is not None: vision_cfg["base_url"] = body.base_url
    if body.api_protocol is not None: vision_cfg["api_protocol"] = body.api_protocol

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader, llm_client
    config_loader.reload_config()
    await llm_client.reload_client()
    return {"message": "Vision 配置已更新", "vision": await get_vision_params(auth)}


class ImageRecognitionUpdate(BaseModel):
    mode: Optional[Literal["vision", "ocr"]] = None
    provider: Optional[str] = None
    api_protocol: Optional[Literal["chat_completions", "glm_layout_parsing"]] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    endpoint_url: Optional[str] = None
    api_key: Optional[str] = None


@router.get("/image-recognition", summary="Image upload routing and OCR connection")
async def get_image_recognition(auth=Depends(require_scopes("admin"))):
    from core.image_recognition import view
    return view(get_config())


@router.put("/image-recognition", summary="Update image upload routing and OCR connection")
async def update_image_recognition(body: ImageRecognitionUpdate, auth=Depends(require_scopes("admin"))):
    from core.image_recognition import endpoint, settings, view
    full_cfg = read_config_file(CONFIG_FILE)
    cfg = settings(full_cfg)
    for key, value in body.model_dump(exclude_none=True).items():
        value = value.strip()
        if key == "api_key" and not value:
            continue
        cfg[key] = value
    try:
        endpoint(cfg)
        if cfg["mode"] == "ocr" and not cfg["model"]:
            raise ValueError("OCR model is required")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    full_cfg["image_recognition"] = cfg
    write_config_file(CONFIG_FILE, full_cfg)
    from core import config_loader
    config_loader.reload_config()
    return view(full_cfg)


def _phone_control_vision_view(cfg: dict) -> dict:
    dedicated = cfg.get("phone_control_vision")
    dedicated = dedicated if isinstance(dedicated, dict) else {}
    return {
        "enabled": dedicated.get("enabled"),
        "api_key": dedicated.get("api_key", ""),
        "model": dedicated.get("model", ""),
        "base_url": dedicated.get("base_url", ""),
    }


@router.get("/vision-params/phone-control", summary="获取手机自动化视觉覆盖")
async def get_phone_control_vision_params(auth=Depends(require_scopes("admin"))):
    """仅返回覆盖字段；未填字段由客户端明确展示为继承通用 Vision。"""
    return _phone_control_vision_view(get_config())


@router.put("/vision-params/phone-control", summary="修改手机自动化视觉覆盖并热重载")
async def update_phone_control_vision_params(
    body: PhoneControlVisionParamsUpdate,
    _auth=Depends(require_scopes("admin")),
):
    full_cfg = read_config_file(CONFIG_FILE)

    dedicated = full_cfg.get("phone_control_vision")
    dedicated = dict(dedicated) if isinstance(dedicated, dict) else {}
    for field in ("enabled", "api_key", "model", "base_url"):
        if field not in body.model_fields_set:
            continue
        value = getattr(body, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            dedicated.pop(field, None)
        else:
            dedicated[field] = value.strip() if isinstance(value, str) else value
    if dedicated:
        full_cfg["phone_control_vision"] = dedicated
    else:
        full_cfg.pop("phone_control_vision", None)

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader

    config_loader.reload_config()
    return {
        "message": "手机自动化视觉覆盖已更新",
        "phone_control_vision": _phone_control_vision_view(full_cfg),
    }


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


def _active_character_id_for_routing_warning() -> str | None:
    """Read the selected character without making a failed admin read fatal."""
    try:
        from core.sandbox import get_paths

        active_id = json.loads(
            get_paths().active_prompt_assets().read_text(encoding="utf-8")
        ).get("active_character")
        if isinstance(active_id, str) and active_id:
            return active_id
    except Exception as exc:
        logger.debug("[model-routing] unable to read active prompt assets: %s", exc)

    raw = get_config().get("character", {}).get("default")
    if not isinstance(raw, str) or not raw:
        return None
    return Path(raw).stem


def _active_character_routing_override() -> dict | None:
    """Return the active card's valid explicit routing binding for admin display.

    This is deliberately fail-soft: model routing remains readable even while an
    authored card or active prompt asset file is temporarily unavailable.
    """
    char_id = _active_character_id_for_routing_warning()
    if not char_id:
        return None

    try:
        from core.character_loader import load as load_character
        from core.model_registry import resolve_routing_info

        character = load_character(char_id)
        routing = resolve_routing_info(char_id)
    except Exception as exc:
        logger.debug("[model-routing] unable to resolve active character %r: %s", char_id, exc)
        return None

    model_routing = routing.get("model_routing")
    effective_profile = routing.get("effective_profile")
    if not isinstance(model_routing, str) or not model_routing:
        return None
    if model_routing != effective_profile:
        # A stale card binding already falls back to the global route. Do not
        # claim that it still overrides the global selection.
        return None

    result = {
        "char_id": char_id,
        "label": character.name or char_id,
        "model_routing": model_routing,
        "effective_profile": effective_profile,
        "resolved_chat_preset": routing.get("resolved_chat_preset", ""),
    }
    if "resolved_scenario_reconcile_preset" in routing:
        result["resolved_scenario_reconcile_preset"] = routing.get("resolved_scenario_reconcile_preset", "")
    return result


@router.get("/model-presets", summary="获取多模型 preset 配置")
async def get_model_presets(auth=Depends(require_scopes("admin"))):
    """返回 presets 列表（api_key 打码）、routing_profiles、active_routing。
    若配置中无 model_presets 块，返回合成的 legacy 视图。
    """
    from core.model_registry import _get_preset_config
    mp = _get_preset_config()
    from core.model_registry import resolve_category_info
    routing_effective = {
        profile_name: {
            "scenario_reconcile": resolve_category_info(
                "scenario_reconcile", profile_name=profile_name
            ),
            "event_edge_proposer": resolve_category_info(
                "event_edge_proposer", profile_name=profile_name
            ),
        }
        for profile_name in mp.get("routing_profiles", {})
    }
    return {
        "active_routing":    mp.get("active_routing", "default"),
        "presets":           _mask_presets(mp.get("presets", {})),
        "routing_profiles":  mp.get("routing_profiles", {}),
        "defaults":          mp.get("defaults", {}),
        "is_legacy_synth":   "model_presets" not in get_config(),
        "active_character_routing": _active_character_routing_override(),
        "routing_effective": routing_effective,
    }


class ActiveRoutingUpdate(BaseModel):
    active_routing: str


async def _persist_model_presets(full_cfg: dict) -> None:
    """Persist config and invalidate every cached model client."""
    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader, llm_client
    config_loader.reload_config()
    await llm_client.reload_client()


@router.post("/model-presets/bootstrap", summary="从 legacy llm 配置初始化 model_presets")
async def bootstrap_model_presets(auth=Depends(require_scopes("admin"))):
    """One-time migration used by the visual admin panel."""
    full_cfg = read_config_file(CONFIG_FILE)

    if full_cfg.get("model_presets"):
        return {"message": "model_presets 已存在，无需初始化", "created": False}

    from core.model_registry import _synth_legacy_presets
    full_cfg["model_presets"] = _synth_legacy_presets(full_cfg)
    await _persist_model_presets(full_cfg)
    return {"message": "已从 legacy llm 配置初始化 model_presets", "created": True}


@router.get("/settings/model-routing", summary="桌面端读取可选模型路由")
async def get_desktop_model_routing(auth=Depends(require_scopes("persona"))):
    """Return safe display data; API keys and endpoint credentials stay admin-only."""
    from core.model_registry import _get_preset_config
    mp = _get_preset_config()
    presets = mp.get("presets", {})
    profiles = mp.get("routing_profiles", {})
    from core.model_registry import resolve_category_info
    rows = []
    for name, profile in profiles.items():
        preset_name = profile.get("chat") or next(iter(presets), "")
        preset = presets.get(preset_name, {})
        reconcile_route = resolve_category_info("scenario_reconcile", profile_name=name)
        rows.append({
            "name": name,
            "chat_preset": preset_name,
            "provider_kind": preset.get("provider_kind", "openai"),
            "model": preset.get("model", ""),
            "tool_call_mode": preset.get("tool_call_mode", "function_calling"),
            "scenario_reconcile_preset": reconcile_route.get("effective_preset", ""),
            "scenario_reconcile_source": reconcile_route.get("source", ""),
        })
    return {
        "active_routing": mp.get("active_routing", "default"),
        "profiles": rows,
        "is_legacy_synth": "model_presets" not in get_config(),
    }


@router.put("/settings/model-routing", summary="桌面端切换已有模型路由")
async def set_desktop_model_routing(body: ActiveRoutingUpdate, auth=Depends(require_scopes("persona"))):
    """Allow desktop selection without exposing preset secrets."""
    full_cfg = read_config_file(CONFIG_FILE)

    mp = full_cfg.get("model_presets")
    if not mp:
        raise HTTPException(status_code=409, detail="请先在管理面板初始化 model_presets")
    if body.active_routing not in mp.get("routing_profiles", {}):
        raise HTTPException(status_code=422, detail="未知 routing profile")

    mp["active_routing"] = body.active_routing
    await _persist_model_presets(full_cfg)
    return {"message": "模型路由已切换", "active_routing": body.active_routing}


@router.get("/model-presets/routing-profiles", summary="可选 routing profile 清单（角色绑定下拉框数据源）")
async def list_routing_profiles(auth=Depends(require_scopes("persona"))):
    """返回全部 routing profile 名 + 各 category→preset 映射摘要（Brief 87 §1）。

    persona scope（非 admin-only）：不暴露 preset 的 api_key/base_url，
    只暴露 profile 结构本身，供角色模型绑定下拉框使用。
    """
    from core.model_registry import _get_preset_config, resolve_category_info
    mp = _get_preset_config()
    profiles = mp.get("routing_profiles", {})
    return {
        "active_routing": mp.get("active_routing", "default"),
        "profiles": [
            {
                "name": name,
                "categories": dict(mapping),
                "effective": {
                    "scenario_reconcile": resolve_category_info("scenario_reconcile", profile_name=name),
                    "event_edge_proposer": resolve_category_info("event_edge_proposer", profile_name=name),
                },
            }
            for name, mapping in profiles.items()
        ],
    }


@router.put("/model-presets/active-routing", summary="切换当前生效的路由方案")
async def set_active_routing(body: ActiveRoutingUpdate, auth=Depends(require_scopes("admin"))):
    """切换 active_routing（如 'default' → 'claude-main'）并热重载。
    只支持已有 model_presets 块的配置；legacy 模式下无意义，会返回 400。
    """
    full_cfg = read_config_file(CONFIG_FILE)

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

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader, llm_client
    config_loader.reload_config()
    await llm_client.reload_client()
    return {"message": f"已切换到路由方案 '{body.active_routing}'", "active_routing": body.active_routing}


# ---------------------------------------------------------------------------
# /model-presets/presets/{name} — preset CRUD（Phase 4）
# ---------------------------------------------------------------------------

class PresetUpsert(BaseModel):
    provider_kind: Optional[str] = None
    force_stream: Optional[bool] = None
    api_protocol: Optional[str] = None
    anthropic_auth_mode: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    tool_call_mode: Optional[str] = None
    prompt_style: Optional[str] = None
    params: Optional[dict] = None
    reasoning_native: Optional[bool] = None
    reasoning_extra_body: Optional[dict] = None
    tool_preset: Optional[str] = None


class PresetRename(BaseModel):
    new_name: str


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
    if body.api_protocol is not None:
        from core.llm_protocol import VALID_API_PROTOCOLS
        if body.api_protocol not in VALID_API_PROTOCOLS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"未知 api_protocol: {body.api_protocol!r}，"
                    f"可选: {sorted(VALID_API_PROTOCOLS)}"
                ),
            )
    if body.anthropic_auth_mode is not None:
        from core.llm_protocol import VALID_ANTHROPIC_AUTH_MODES
        if body.anthropic_auth_mode not in VALID_ANTHROPIC_AUTH_MODES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"未知 anthropic_auth_mode: {body.anthropic_auth_mode!r}，"
                    f"可选: {sorted(VALID_ANTHROPIC_AUTH_MODES)}"
                ),
            )

    full_cfg = read_config_file(CONFIG_FILE)

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
    if existing.get("force_stream") and existing.get("api_protocol", "chat_completions") != "chat_completions":
        raise HTTPException(status_code=422, detail="强制流式请求目前仅支持 Chat Completions 协议。")
    presets[name] = existing

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader, llm_client
    config_loader.reload_config()
    await llm_client.reload_client()

    return {
        "message": f"preset '{name}' 已{'创建' if is_new else '更新'}",
        "name": name,
        "preset": _mask_presets({name: presets[name]})[name],
    }


@router.post("/model-presets/presets/{name}/rename", summary="重命名 model preset 并更新 routing profile 引用")
async def rename_preset(name: str, body: PresetRename, auth=Depends(require_scopes("admin"))):
    """Rename one preset atomically with every routing-profile reference to it."""
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="新的 preset 名称不能为空")

    full_cfg = read_config_file(CONFIG_FILE)
    mp = _require_model_presets_block(full_cfg)
    presets = mp.setdefault("presets", {})
    if name not in presets:
        raise HTTPException(status_code=404, detail=f"preset {name!r} 不存在")
    if new_name != name and new_name in presets:
        raise HTTPException(status_code=409, detail=f"preset {new_name!r} 已存在")

    updated_references: list[str] = []
    if new_name != name:
        # Rebuild instead of pop+append so the renamed item preserves its order
        # in the admin UI and config file.
        mp["presets"] = {
            (new_name if preset_name == name else preset_name): preset
            for preset_name, preset in presets.items()
        }
        presets = mp["presets"]
        for profile_name, profile in mp.get("routing_profiles", {}).items():
            for category, preset_name in profile.items():
                if preset_name == name:
                    profile[category] = new_name
                    updated_references.append(f"{profile_name}.{category}")

        await _persist_model_presets(full_cfg)

    return {
        "message": f"preset {name!r} 已重命名为 {new_name!r}",
        "name": new_name,
        "old_name": name,
        "updated_references": updated_references,
        "preset": _mask_presets({new_name: presets[new_name]})[new_name],
    }


@router.delete("/model-presets/presets/{name}", summary="删除一个 model preset")
async def delete_preset(name: str, auth=Depends(require_scopes("admin"))):
    """删除指定 preset。仍被某个 routing profile 的任意 call_category 引用时拒绝（409），
    唯一剩余的 preset 也拒绝删除（409），避免路由解析无 preset 可用。
    """
    full_cfg = read_config_file(CONFIG_FILE)

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

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader, llm_client
    config_loader.reload_config()
    await llm_client.reload_client()

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

    full_cfg = read_config_file(CONFIG_FILE)

    mp = _require_model_presets_block(full_cfg)
    presets = mp.get("presets", {})
    unknown = sorted({v for v in body.values() if v not in presets})
    if unknown:
        raise HTTPException(status_code=422, detail=f"routing profile 引用了不存在的 preset: {unknown}")

    profiles = mp.setdefault("routing_profiles", {})
    profile = dict(profiles.get(name, {}))
    profile.update(body)
    profiles[name] = profile

    write_config_file(CONFIG_FILE, full_cfg)

    from core import config_loader, llm_client
    config_loader.reload_config()
    await llm_client.reload_client()

    return {"message": f"routing profile '{name}' 已更新", "name": name, "profile": profile}


# ---------------------------------------------------------------------------
# /model-presets/presets/{name}/test — 连通性测试（Phase 4）
# ---------------------------------------------------------------------------

@router.post("/model-presets/presets/{name}/test", summary="模型连通性与协议诊断")
async def test_preset_connectivity(name: str, auth=Depends(require_scopes("admin"))):
    """Bounded probe; report protocol failures without echoing provider bodies."""
    from core.model_registry import build_client_for_preset
    import time as _time
    import asyncio
    from core.model_diagnostics import diagnose, request_metadata

    try:
        client = build_client_for_preset(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    t0 = _time.monotonic()
    try:
        from core.llm_protocol import create as create_protocol_response
        resp = await asyncio.wait_for(create_protocol_response(
            client,
            [{"role": "user", "content": "ping"}],
            tools=None,
            tool_choice=None,
            gen_kwargs={"max_tokens": 256, "timeout": 30.0},
        ), timeout=30.0)
        latency_ms = round((_time.monotonic() - t0) * 1000, 1)
        reply_preview = ""
        try:
            reply_preview = resp.assistant_text[:20]
        except Exception:
            pass
        return {
            "ok": True, "name": name, "model": client.model,
            "latency_ms": latency_ms, "reply_preview": reply_preview,
            "warning": "连接成功，但没有返回可见文字；可能 token 预算被推理消耗，尚不能确认正常对话可用。" if not reply_preview else "",
            **request_metadata(client),
        }
    except Exception as e:
        latency_ms = round((_time.monotonic() - t0) * 1000, 1)
        return {
            "ok": False, "name": name, "model": client.model,
            "latency_ms": latency_ms, **diagnose(e), **request_metadata(client),
        }
    finally:
        try:
            close = getattr(client.client, "close", None) or getattr(client.client, "aclose", None)
            if callable(close):
                await close()
        except Exception:
            pass
