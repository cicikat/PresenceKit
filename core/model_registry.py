"""Model registry — multi-preset routing for LLM calls.

Owns ModelClient construction, param merging, provider whitelist filtering,
and routing from a required `model_presets` block.

Phase 1: core logic (registry, param merge, routing).
Phase 2: prompt_style wiring is done in llm_client.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import AsyncOpenAI

from core.config_loader import get_config
from core.llm_protocol import VALID_ANTHROPIC_AUTH_MODES, VALID_API_PROTOCOLS

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
        # Claude native Messages or OpenAI-compatible layer: penalty params are commonly rejected.
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
_SENSOR_JUDGE_POLICY: dict[str, float | int] = {"timeout_s": 10.0, "max_retries": 0}
_FAILOVER_POLICY: dict[str, float | int] = {"timeout_s": 10.0, "max_retries": 0}


@dataclass
class ModelClient:
    """Resolved, ready-to-use client for a single named preset."""
    name: str
    provider_kind: str
    model: str
    tool_call_mode: str
    prompt_style: str        # "narrative" | "xml"
    params: dict[str, Any]  # merged + whitelist-filtered generation params
    client: Any
    api_protocol: str = "chat_completions"
    force_stream: bool = False
    tool_empty_params_placeholder: bool = False
    base_url: str = ""
    api_key: str = ""
    anthropic_auth_mode: str = "x_api_key"
    reasoning_native: bool = False                       # Brief 32：preset 声明原生 reasoning 支持
    reasoning_extra_body: dict[str, Any] = field(default_factory=dict)  # 原样透传 extra_body，绕过参数白名单（逃生舱）
    request_timeout_s: float = _DEFAULT_CALL_TIMEOUT


# preset_name → built ModelClient; cleared on reload_registry()
_model_clients: dict[tuple[str, str], ModelClient] = {}


# ---------------------------------------------------------------------------
# Internal HTTP/proxy helpers (independent copy — avoids circular import
# with llm_client which imports from here)
# ---------------------------------------------------------------------------

def _get_proxy_url() -> str | None:
    from core.model_network import model_proxy_settings
    return model_proxy_settings()[1]


def _make_http_client(proxy_url: str | None, *, timeout_s: float = _DEFAULT_CALL_TIMEOUT) -> httpx.AsyncClient:
    from core.model_network import make_model_http_client
    base_timeout = httpx.Timeout(timeout=timeout_s, connect=min(10.0, timeout_s))
    return make_model_http_client(proxy_url, base_timeout)


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


def _default_preset_name(mp: dict) -> str:
    """Return model_presets.default_preset when it still names a real preset."""
    name = str(mp.get("default_preset") or "").strip()
    presets = mp.get("presets") or {}
    return name if name and name in presets else ""


def _get_preset_config() -> dict:
    """Return the configured model_presets block. Flat `llm:` synthesis is gone."""
    cfg = get_config()
    mp = cfg.get("model_presets")
    if not mp:
        raise ValueError(
            "config.yaml 缺少 model_presets 块；扁平 llm: 合成已退出。"
            "请按 config.example.yaml 配置 model_presets，或用管理面新增 preset。"
        )
    return mp


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
      2. → model_presets.default_preset (if that name still exists)
      3. → "chat" key in same profile
      4. → first preset name in presets dict
    Compatibility chains (sensor_judge / ime_judge / scenario_reconcile) keep
    their intent/chat hops before default_preset, so old profiles without the
    new field still behave the same.
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
    default_name = _default_preset_name(mp)
    # Old routing profiles predate sensor_judge.  Preserve their lightweight
    # intent route before falling back to chat; all other categories retain
    # the established category -> default_preset -> chat fallback.
    if call_category == "scenario_reconcile":
        name = profile.get("scenario_reconcile") or profile.get("intent") or profile.get("chat") or default_name
    elif call_category == "ime_judge":
        name = (
            profile.get("ime_judge")
            or profile.get("sensor_judge")
            or profile.get("intent")
            or profile.get("chat")
            or default_name
        )
    elif call_category == "sensor_judge":
        name = profile.get("sensor_judge") or profile.get("intent") or profile.get("chat") or default_name
    else:
        name = profile.get(call_category) or default_name or profile.get("chat")
    if not name:
        name = next(iter(mp.get("presets", {})), "legacy")
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
    preset_name = _resolve_preset_name("chat", char_id=char_id)
    preset = mp.get("presets", {}).get(preset_name, {})
    def configured_value(key: str) -> bool:
        value = str(preset.get(key) or "").strip()
        return bool(value) and not value.upper().startswith(("YOUR_", "YOUR-"))
    return {
        "model_routing": char_routing,
        "effective_profile": effective_profile,
        "resolved_chat_preset": preset_name,
        "resolved_chat_model": preset.get("model", ""),
        "global_profile": active,
        "binding_source": "character" if char_routing and char_routing in profiles else "global",
        # OpenAI-compatible local servers may accept an empty API key. Native
        # Anthropic requests always send an authentication header.
        "chat_configured": all(configured_value(key) for key in ("base_url", "model"))
        and (preset.get("api_protocol") != "anthropic_messages" or configured_value("api_key")),
        "resolved_scenario_reconcile_preset": _resolve_preset_name("scenario_reconcile", char_id=char_id),
    }


def resolve_category_info(
    call_category: str,
    *,
    char_id: str | None = None,
    profile_name: str | None = None,
) -> dict[str, Any]:
    """Return safe effective preset and fallback source for admin/telemetry."""
    mp = _get_preset_config()
    profiles = mp.get("routing_profiles", {})
    active = profile_name or mp.get("active_routing", "default")
    if not profile_name:
        char_routing = _char_model_routing(char_id) if char_id else _active_char_model_routing()
        if char_routing in profiles:
            active = char_routing
    profile = profiles.get(active) or (next(iter(profiles.values())) if profiles else {})
    if call_category == "scenario_reconcile":
        candidates = (("scenario_reconcile", "category"), ("intent", "intent_fallback"), ("chat", "chat_fallback"))
    elif call_category == "ime_judge":
        candidates = (("ime_judge", "category"), ("sensor_judge", "sensor_fallback"), ("intent", "intent_fallback"), ("chat", "chat_fallback"))
    elif call_category == "sensor_judge":
        candidates = (("sensor_judge", "category"), ("intent", "intent_fallback"), ("chat", "chat_fallback"))
    else:
        candidates = ((call_category, "category"), ("chat", "chat_fallback"))
    preset_name = ""
    source = "first_preset"
    default_name = _default_preset_name(mp)
    for key, candidate_source in candidates:
        if profile.get(key):
            preset_name = str(profile[key])
            source = candidate_source
            break
        if (
            key == call_category
            and call_category not in ("scenario_reconcile", "ime_judge", "sensor_judge")
            and default_name
        ):
            preset_name = default_name
            source = "default_preset"
            break
    if not preset_name:
        if default_name:
            preset_name = default_name
            source = "default_preset"
        else:
            preset_name = str(next(iter(mp.get("presets", {})), "legacy"))
    preset = mp.get("presets", {}).get(preset_name, {})
    fallback = resolve_fallback_route(
        call_category,
        profile_name=active,
        primary_preset=preset_name,
        mp=mp,
    )
    return {
        "category": call_category,
        "effective_profile": active,
        "effective_preset": preset_name,
        "source": source,
        "provider_kind": preset.get("provider_kind", "openai"),
        "model": preset.get("model", ""),
        "model_version": preset.get("model_version", ""),
        "fallback_preset": fallback.get("preset") or "",
        "fallback_source": fallback.get("source") or "off",
        "fallback_refused_reason": fallback.get("refused_reason") or "",
    }


def _normalize_fallback_map(raw: Any) -> dict[str, dict[str, str]]:
    """Coerce model_presets.fallback_routes into profile → category → preset."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for profile_name, mapping in raw.items():
        if not isinstance(profile_name, str) or not profile_name.strip():
            continue
        if not isinstance(mapping, dict):
            continue
        cats: dict[str, str] = {}
        for category, preset in mapping.items():
            if not isinstance(category, str) or not category.strip():
                continue
            name = str(preset or "").strip()
            if name:
                cats[category] = name
        if cats:
            out[profile_name] = cats
    return out


def normalize_fallback_routes(mp: dict | None) -> dict[str, dict[str, str]]:
    """Public helper for admin CRUD: empty/missing maps stay empty (off)."""
    if not isinstance(mp, dict):
        return {}
    return _normalize_fallback_map(mp.get("fallback_routes"))


def _effective_profile_name(
    mp: dict,
    *,
    char_id: str | None = None,
    profile_name: str | None = None,
) -> str:
    profiles = mp.get("routing_profiles", {})
    active = profile_name or mp.get("active_routing", "default")
    if not profile_name:
        char_routing = _char_model_routing(char_id) if char_id else _active_char_model_routing()
        if char_routing in profiles:
            active = char_routing
    return active if isinstance(active, str) else "default"


def resolve_fallback_route(
    call_category: str,
    *,
    char_id: str | None = None,
    profile_name: str | None = None,
    primary_preset: str | None = None,
    mp: dict | None = None,
) -> dict[str, str]:
    """Resolve an optional failure-failover preset for one category.

    Empty / missing config is off. Same-as-primary, unknown preset, or a
    mapping that is not a string name is refused rather than silently used.
    This is independent of the missing-config chain in ``_resolve_preset_name``.
    """
    mp = mp if mp is not None else _get_preset_config()
    profiles = mp.get("routing_profiles", {}) or {}
    presets = mp.get("presets", {}) or {}
    active = _effective_profile_name(mp, char_id=char_id, profile_name=profile_name)
    routes = _normalize_fallback_map(mp.get("fallback_routes"))
    mapping = routes.get(active) or {}
    raw = mapping.get(call_category)
    if not raw:
        return {"preset": "", "source": "off", "profile": active, "refused_reason": ""}
    if raw not in presets:
        logger.warning(
            "[model_registry] fallback_routes[%s][%s]=%r is not a known preset; refusing",
            active, call_category, raw,
        )
        return {
            "preset": "",
            "source": "invalid_config",
            "profile": active,
            "refused_reason": "unknown_preset",
        }
    if primary_preset and raw == primary_preset:
        logger.warning(
            "[model_registry] fallback_routes[%s][%s]=%r equals primary; refusing",
            active, call_category, raw,
        )
        return {
            "preset": "",
            "source": "invalid_config",
            "profile": active,
            "refused_reason": "same_as_primary",
        }
    if active not in profiles:
        return {
            "preset": "",
            "source": "invalid_config",
            "profile": active,
            "refused_reason": "unknown_profile",
        }
    return {"preset": raw, "source": "configured", "profile": active, "refused_reason": ""}


def rewrite_fallback_preset_references(mp: dict, *, old_name: str, new_name: str) -> list[str]:
    """Rewrite fallback_routes values after a preset rename. Returns changed keys."""
    routes = mp.get("fallback_routes")
    if not isinstance(routes, dict):
        return []
    updated: list[str] = []
    for profile_name, mapping in routes.items():
        if not isinstance(mapping, dict):
            continue
        for category, preset_name in list(mapping.items()):
            if preset_name == old_name:
                mapping[category] = new_name
                updated.append(f"fallback_routes.{profile_name}.{category}")
    return updated


def collect_fallback_preset_references(mp: dict, name: str) -> list[str]:
    routes = mp.get("fallback_routes")
    if not isinstance(routes, dict):
        return []
    return [
        f"fallback_routes.{profile_name}.{category}"
        for profile_name, mapping in routes.items()
        if isinstance(mapping, dict)
        for category, preset_name in mapping.items()
        if preset_name == name
    ]


def rewrite_fallback_profile_name(mp: dict, *, old_name: str, new_name: str) -> None:
    routes = mp.get("fallback_routes")
    if not isinstance(routes, dict) or old_name not in routes:
        return
    mp["fallback_routes"] = {
        (new_name if profile_name == old_name else profile_name): mapping
        for profile_name, mapping in routes.items()
    }


def drop_fallback_profile(mp: dict, name: str) -> None:
    routes = mp.get("fallback_routes")
    if isinstance(routes, dict):
        routes.pop(name, None)


def validate_fallback_mapping(
    mapping: dict[str, str],
    *,
    presets: dict,
    primary: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Return (cleaned mapping, error messages). Empty values are omitted (off)."""
    cleaned: dict[str, str] = {}
    errors: list[str] = []
    for category, preset_name in mapping.items():
        if not isinstance(category, str) or not category.strip():
            errors.append(f"非法 category: {category!r}")
            continue
        name = str(preset_name or "").strip()
        if not name:
            continue
        if name not in presets:
            errors.append(f"{category} 引用了不存在的兜底 preset: {name}")
            continue
        if primary and primary.get(category) == name:
            errors.append(f"{category} 的兜底 preset 与主 preset 相同: {name}")
            continue
        cleaned[category] = name
    return cleaned, errors


# ---------------------------------------------------------------------------
# Client construction + registry
# ---------------------------------------------------------------------------

def _build_model_client(preset_name: str, *, request_policy: dict[str, float | int] | None = None) -> ModelClient:
    mp = _get_preset_config()
    presets = mp.get("presets", {})
    preset = presets.get(preset_name)
    if not preset:
        raise ValueError(f"[model_registry] preset '{preset_name}' not found in config")

    kind = preset.get("provider_kind", "openai")
    profile = PROVIDER_PROFILES.get(kind, _FALLBACK_PROFILE)

    prompt_style = preset.get("prompt_style") or profile["default_prompt_style"]
    tool_call_mode = preset.get("tool_call_mode", "function_calling")
    api_protocol = preset.get("api_protocol", "chat_completions")
    if api_protocol not in VALID_API_PROTOCOLS:
        raise ValueError(
            "[model_registry] invalid api_protocol "
            f"for preset={preset_name!r} provider={kind!r} model={preset.get('model', '')!r}: "
            f"{api_protocol!r}; expected one of {sorted(VALID_API_PROTOCOLS)}"
        )
    force_stream = preset.get("force_stream", False)
    if type(force_stream) is not bool or (force_stream and api_protocol != "chat_completions"):
        raise ValueError("force_stream must be boolean and is supported only for chat_completions")
    anthropic_auth_mode = preset.get("anthropic_auth_mode", "x_api_key")
    if anthropic_auth_mode not in VALID_ANTHROPIC_AUTH_MODES:
        raise ValueError(
            "[model_registry] invalid anthropic_auth_mode "
            f"for preset={preset_name!r} provider={kind!r} model={preset.get('model', '')!r}: "
            f"{anthropic_auth_mode!r}; expected one of {sorted(VALID_ANTHROPIC_AUTH_MODES)}"
        )

    params = resolve_params(
        mp.get("defaults", {}),
        preset.get("params", {}),
        kind,
    )

    request_policy = request_policy or {}
    timeout_s = float(request_policy.get("timeout_s", _DEFAULT_CALL_TIMEOUT))
    max_retries = int(request_policy.get("max_retries", 2))
    proxy_url = _get_proxy_url()
    http_client = _make_http_client(proxy_url, timeout_s=timeout_s)
    base_url = preset.get("base_url", "")
    api_key = preset.get("api_key", "")
    # Native Anthropic Messages calls use httpx directly; Chat Completions and
    # Responses retain the OpenAI SDK client.  Both own the same pool lifecycle.
    client: Any
    if api_protocol == "anthropic_messages":
        client = http_client
    else:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            http_client=http_client,
            max_retries=max_retries,
            # Identify the application consistently for relay compatibility.
            # Some gateways reject the SDK's generic Python User-Agent.
            default_headers={"User-Agent": "PresenceKit/1.0"},
        )
    logger.info(
        "[model_registry] built ModelClient '%s' kind=%s model=%s api_protocol=%s proxy=%s",
        preset_name, kind, preset.get("model"), api_protocol, "on" if proxy_url else "off",
    )
    return ModelClient(
        name=preset_name,
        provider_kind=kind,
        model=preset.get("model", ""),
        tool_call_mode=tool_call_mode,
        prompt_style=prompt_style,
        params=params,
        client=client,
        api_protocol=api_protocol,
        force_stream=force_stream,
        tool_empty_params_placeholder=preset.get("tool_empty_params_placeholder", False) is True,
        base_url=base_url,
        api_key=api_key,
        anthropic_auth_mode=anthropic_auth_mode,
        reasoning_native=bool(preset.get("reasoning_native", False)),
        reasoning_extra_body=dict(preset.get("reasoning_extra_body") or {}),
        request_timeout_s=timeout_s,
    )


def build_client_for_preset(name: str) -> ModelClient:
    """按 preset 名称构建一个不经缓存的全新 ModelClient。

    供连通性测试等一次性场景使用；不写入 `_model_clients` 缓存，调用方用完后
    应自行关闭 `.client`（AsyncOpenAI），避免 httpx 连接泄漏。preset 不存在时
    抛 ValueError（与 `_build_model_client` 一致）。
    """
    return _build_model_client(name, request_policy={"timeout_s": 30, "max_retries": 0})


def get_model_client(
    call_category: str,
    *,
    char_id: str | None = None,
    preset_name: str | None = None,
    failover: bool = False,
) -> ModelClient:
    """Resolve a routing category or explicit preset to a cached ModelClient.

    char_id=None（默认）：按活跃角色解析，与现状完全一致。
    char_id 给定：按该角色卡自己的 model_routing 解析（Brief 30 · char 维度穿线）。
    preset_name 给定：直接选择同名 preset，不经过 routing profile；不存在时明确抛错。
    failover=True：零 SDK retry，独立缓存，不改写主 preset 的健康策略。
    """
    if preset_name is not None:
        resolved_name = preset_name.strip()
        if not resolved_name:
            raise ValueError("[model_registry] explicit preset name must not be empty")
    else:
        resolved_name = _resolve_preset_name(call_category, char_id=char_id)
    if failover:
        policy_name = "failover"
        policy: dict[str, float | int] | None = dict(_FAILOVER_POLICY)
        from core.llm_failover import category_timeout
        policy["timeout_s"] = category_timeout(call_category)
    elif call_category == "food_extract":
        policy_name = "food_extract"
        policy = {"timeout_s": 20, "max_retries": 0}
    elif call_category in {"sensor_judge", "ime_judge"}:
        policy_name = "sensor_judge"
        policy = _SENSOR_JUDGE_POLICY
    else:
        policy_name = "default"
        policy = None
    cache_key = (resolved_name, policy_name)
    if cache_key not in _model_clients:
        _model_clients[cache_key] = _build_model_client(resolved_name, request_policy=policy)
    return _model_clients[cache_key]


def reload_registry() -> list[ModelClient]:
    """Detach and return cached clients; next call rebuilds from current config."""
    global _model_clients
    retired = list(_model_clients.values())
    _model_clients = {}
    logger.info("[model_registry] registry cleared, will rebuild on next request")
    return retired
