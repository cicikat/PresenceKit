"""Model registry — multi-preset routing for LLM calls.

Owns ModelClient construction, param merging, provider whitelist filtering,
and backward compatibility synthesis from a legacy flat `llm:` config block.

Phase 1: core logic (registry, param merge, routing, backward compat).
Phase 2: prompt_style wiring is done in llm_client.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import AsyncOpenAI

from core.config_loader import get_config

logger = logging.getLogger(__name__)

# Per-provider generation parameter whitelist + default prompt style.
# Extend when adding new providers; existing callers pick up changes automatically.
PROVIDER_PROFILES: dict[str, dict] = {
    "openai": {
        "params": {"temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty"},
        "default_prompt_style": "narrative",
    },
    "deepseek": {
        "params": {"temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty"},
        "default_prompt_style": "narrative",
    },
    "anthropic_compat": {
        # Claude via OpenAI-compat layer: penalty params commonly rejected or silently dropped
        "params": {"temperature", "top_p", "max_tokens"},
        "default_prompt_style": "xml",
    },
    "local": {
        # vLLM / llama.cpp / ollama: conservative subset
        "params": {"temperature", "top_p", "max_tokens"},
        "default_prompt_style": "narrative",
    },
}
_FALLBACK_PROFILE = PROVIDER_PROFILES["openai"]

_DEFAULT_CALL_TIMEOUT: float = 90.0


@dataclass
class ModelClient:
    """Resolved, ready-to-use client for a single named preset."""
    name: str
    provider_kind: str
    model: str
    tool_call_mode: str
    prompt_style: str        # "narrative" | "xml"
    params: dict[str, Any]  # merged + whitelist-filtered generation params
    client: AsyncOpenAI
    reasoning_native: bool = False                       # Brief 32：preset 声明原生 reasoning 支持
    reasoning_extra_body: dict[str, Any] = field(default_factory=dict)  # 原样透传 extra_body，绕过参数白名单（逃生舱）


# preset_name → built ModelClient; cleared on reload_registry()
_model_clients: dict[str, ModelClient] = {}


# ---------------------------------------------------------------------------
# Internal HTTP/proxy helpers (independent copy — avoids circular import
# with llm_client which imports from here)
# ---------------------------------------------------------------------------

def _get_proxy_url() -> str | None:
    proxy_cfg = get_config().get("proxy", {})
    if proxy_cfg.get("enabled", False):
        return proxy_cfg.get("http") or None
    return None


def _make_http_client(proxy_url: str | None) -> httpx.AsyncClient:
    base_timeout = httpx.Timeout(timeout=_DEFAULT_CALL_TIMEOUT, connect=10.0)
    if proxy_url:
        return httpx.AsyncClient(proxy=proxy_url, timeout=base_timeout)
    return httpx.AsyncClient(trust_env=False, timeout=base_timeout)


# ---------------------------------------------------------------------------
# Backward compatibility: synthesise preset structure from flat `llm:` block
# ---------------------------------------------------------------------------

def _kind_from_legacy(llm: dict) -> str:
    """Infer provider_kind from base_url in a legacy llm config block."""
    base_url = (llm.get("base_url") or "").lower()
    if "deepseek" in base_url:
        return "deepseek"
    if "anthropic" in base_url or "claude" in base_url:
        return "anthropic_compat"
    if "127.0.0.1" in base_url or "localhost" in base_url:
        return "local"
    return "openai"


def _synth_legacy_presets(cfg: dict) -> dict:
    """Build a synthetic model_presets block from a flat `llm:` config."""
    llm = cfg.get("llm", {})
    _known_params = ("temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty")
    preset: dict[str, Any] = {
        "provider_kind": _kind_from_legacy(llm),
        "base_url": llm.get("base_url", ""),
        "api_key": llm.get("api_key", ""),
        "model": llm.get("model", ""),
        "tool_call_mode": llm.get("tool_call_mode", "function_calling"),
        "params": {k: llm[k] for k in _known_params if k in llm},
    }
    _all_categories = ("chat", "intent", "probe", "summary", "detect_emotion", "consolidation", "perform")
    return {
        "active_routing": "default",
        "defaults": {},
        "presets": {"legacy": preset},
        "routing_profiles": {
            "default": {cat: "legacy" for cat in _all_categories},
        },
    }


# ---------------------------------------------------------------------------
# Pure helpers (easy to unit-test)
# ---------------------------------------------------------------------------

def resolve_params(defaults: dict, preset_params: dict, provider_kind: str) -> dict:
    """Merge defaults + preset overrides then filter to provider whitelist.

    Order:
      1. global defaults
      2. preset.params overrides
      3. provider whitelist (strips unsupported keys like penalty for Claude)
    """
    resolved: dict[str, Any] = {}
    resolved.update(defaults)
    resolved.update(preset_params)
    allow = PROVIDER_PROFILES.get(provider_kind, _FALLBACK_PROFILE)["params"]
    return {k: v for k, v in resolved.items() if k in allow}


def _get_preset_config() -> dict:
    """Return the effective model_presets block (real or synthesised legacy)."""
    cfg = get_config()
    mp = cfg.get("model_presets")
    if mp:
        return mp
    return _synth_legacy_presets(cfg)


def _active_char_model_routing() -> str | None:
    """活跃角色卡 presence_ext.model_routing（Brief 29 · 3.2）。

    fail-soft：未注册/加载失败/字段缺失 → None（回落全局 active_routing）。
    """
    try:
        from core import pipeline_registry
        pl = pipeline_registry.get()
        char = pl.character if pl is not None else None
        if char is None:
            return None
        return getattr(char, "presence_ext", {}).get("model_routing") or None
    except Exception:
        return None


def _char_model_routing(char_id: str) -> str | None:
    """指定角色卡（非活跃角色）的 presence_ext.model_routing（Brief 30 · 2.1）。

    显式 char_id 路径：只读该角色自己的卡，不回落到活跃角色的 override。
    fail-soft：加载失败/字段缺失 → None（回落全局 active_routing）。
    """
    try:
        from core import character_loader
        char = character_loader.load(char_id)
        return getattr(char, "presence_ext", {}).get("model_routing") or None
    except Exception:
        return None


def _resolve_preset_name(call_category: str, char_id: str | None = None) -> str:
    """Map a call_category to a preset name via the active routing profile.

    Routing profile selection:
      1. 显式 char_id 给定（Brief 30）→ 读该角色卡 presence_ext.model_routing；
         否则活跃角色卡 presence_ext.model_routing（Brief 29 · 3.2）
      2. 若该 profile 存在于 routing_profiles → 用它，否则回落全局 active_routing
         （profile 不存在时记 warning）

    Fallback chain within the chosen profile:
      1. profile → call_category key
      2. → "chat" key in same profile
      3. → first preset name in presets dict
    """
    mp = _get_preset_config()
    profiles = mp.get("routing_profiles", {})
    active = mp.get("active_routing", "default")

    char_routing = _char_model_routing(char_id) if char_id else _active_char_model_routing()
    if char_routing:
        if char_routing in profiles:
            active = char_routing
        else:
            logger.warning(
                "[model_registry] 角色卡 model_routing=%r 不是已知 routing profile，回落全局 active_routing=%r",
                char_routing, active,
            )

    profile = profiles.get(active) or (next(iter(profiles.values())) if profiles else {})
    name = profile.get(call_category) or profile.get("chat")
    if not name:
        presets = mp.get("presets", {})
        name = next(iter(presets), "legacy")
    return name


def resolve_routing_info(char_id: str) -> dict:
    """角色卡 model_routing 声明 + 实际解析结果（Brief 87 §1 绑定 API 用）。

    绑定 API 把解析结果一起回给前端，这样"绑定后立刻可见实际会用哪个 preset"，
    不用等一次真实 LLM 调用才知道结果。

    Returns: {"model_routing": str|None, "effective_profile": str, "resolved_chat_preset": str}.
    """
    mp = _get_preset_config()
    profiles = mp.get("routing_profiles", {})
    active = mp.get("active_routing", "default")
    char_routing = _char_model_routing(char_id)
    effective_profile = char_routing if char_routing and char_routing in profiles else active
    return {
        "model_routing": char_routing,
        "effective_profile": effective_profile,
        "resolved_chat_preset": _resolve_preset_name("chat", char_id=char_id),
    }


# ---------------------------------------------------------------------------
# Client construction + registry
# ---------------------------------------------------------------------------

def _build_model_client(preset_name: str) -> ModelClient:
    mp = _get_preset_config()
    presets = mp.get("presets", {})
    preset = presets.get(preset_name)
    if not preset:
        raise ValueError(f"[model_registry] preset '{preset_name}' not found in config")

    kind = preset.get("provider_kind", "openai")
    profile = PROVIDER_PROFILES.get(kind, _FALLBACK_PROFILE)

    prompt_style = preset.get("prompt_style") or profile["default_prompt_style"]
    tool_call_mode = preset.get("tool_call_mode", "function_calling")

    params = resolve_params(
        mp.get("defaults", {}),
        preset.get("params", {}),
        kind,
    )

    proxy_url = _get_proxy_url()
    http_client = _make_http_client(proxy_url)
    oa_client = AsyncOpenAI(
        api_key=preset.get("api_key", ""),
        base_url=preset.get("base_url", "") or None,
        http_client=http_client,
    )
    logger.info(
        "[model_registry] built ModelClient '%s' kind=%s model=%s proxy=%s",
        preset_name, kind, preset.get("model"), "on" if proxy_url else "off",
    )
    return ModelClient(
        name=preset_name,
        provider_kind=kind,
        model=preset.get("model", ""),
        tool_call_mode=tool_call_mode,
        prompt_style=prompt_style,
        params=params,
        client=oa_client,
        reasoning_native=bool(preset.get("reasoning_native", False)),
        reasoning_extra_body=dict(preset.get("reasoning_extra_body") or {}),
    )


def build_client_for_preset(name: str) -> ModelClient:
    """按 preset 名称构建一个不经缓存的全新 ModelClient。

    供连通性测试等一次性场景使用；不写入 `_model_clients` 缓存，调用方用完后
    应自行关闭 `.client`（AsyncOpenAI），避免 httpx 连接泄漏。preset 不存在时
    抛 ValueError（与 `_build_model_client` 一致）。
    """
    return _build_model_client(name)


def get_model_client(call_category: str, *, char_id: str | None = None) -> ModelClient:
    """Resolve call_category → preset → ModelClient (cached per preset name).

    char_id=None（默认）：按活跃角色解析，与现状完全一致。
    char_id 给定：按该角色卡自己的 model_routing 解析（Brief 30 · char 维度穿线）。
    """
    preset_name = _resolve_preset_name(call_category, char_id=char_id)
    if preset_name not in _model_clients:
        _model_clients[preset_name] = _build_model_client(preset_name)
    return _model_clients[preset_name]


def reload_registry() -> None:
    """Clear the client cache; next call rebuilds from current config."""
    global _model_clients
    _model_clients = {}
    logger.info("[model_registry] registry cleared, will rebuild on next request")
