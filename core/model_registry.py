"""Model registry — multi-preset routing for LLM calls.

Owns ModelClient construction, param merging, provider whitelist filtering,
and backward compatibility synthesis from a legacy flat `llm:` config block.

Phase 1: core logic (registry, param merge, routing, backward compat).
Phase 2: prompt_style wiring is done in llm_client.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
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


def _resolve_preset_name(call_category: str) -> str:
    """Map a call_category to a preset name via the active routing profile.

    Fallback chain:
      1. active_routing profile → call_category key
      2. → "chat" key in same profile
      3. → first preset name in presets dict
    """
    mp = _get_preset_config()
    active = mp.get("active_routing", "default")
    profiles = mp.get("routing_profiles", {})
    profile = profiles.get(active) or (next(iter(profiles.values())) if profiles else {})
    name = profile.get(call_category) or profile.get("chat")
    if not name:
        presets = mp.get("presets", {})
        name = next(iter(presets), "legacy")
    return name


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
    )


def get_model_client(call_category: str) -> ModelClient:
    """Resolve call_category → preset → ModelClient (cached per preset name)."""
    preset_name = _resolve_preset_name(call_category)
    if preset_name not in _model_clients:
        _model_clients[preset_name] = _build_model_client(preset_name)
    return _model_clients[preset_name]


def reload_registry() -> None:
    """Clear the client cache; next call rebuilds from current config."""
    global _model_clients
    _model_clients = {}
    logger.info("[model_registry] registry cleared, will rebuild on next request")
