"""Global effective-state contract for the admin control-center overview."""
from __future__ import annotations

import time
from typing import Any


CONTRACT_VERSION = "global-effective-state.v1"


def _same(left: Any, right: Any) -> bool:
    return left == right


def _row(
    *,
    feature_id: str,
    default: Any,
    configured: Any,
    effective: Any,
    source: str,
    status: str,
    reason: str | None,
    consumer: str,
    edit_page: str | None,
    read_only: bool = False,
    restart_required: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contradiction = not _same(configured, effective)
    explanation = reason or (
        "Runtime value differs from the configured value."
        if contradiction
        else "Runtime value follows the configured value."
    )
    return {
        "id": feature_id,
        "default_value": default,
        "configured_value": configured,
        "effective_value": effective,
        "default": default,
        "configured": configured,
        "effective": effective,
        "override_source": source,
        "source": source,
        "runtime_status": status,
        "runtime_state": status,
        "blocking_reason": reason,
        "reason": reason,
        "explanation": explanation,
        "contradictory": contradiction,
        "restart_required": bool(restart_required),
        "reload_mode": "restart_required" if restart_required else "hot_reload",
        "runtime_consumer": consumer,
        "edit_page": edit_page,
        "edit_path": f"/admin#{edit_page}" if edit_page else None,
        "edit_destination": edit_page,
        "read_only": bool(read_only),
        "details": details or {},
    }


def _configured(section: dict[str, Any], key: str, default: Any) -> tuple[Any, str]:
    if key not in section:
        return default, "default"
    return section.get(key), "config"


def _safe_call(fn, fallback):
    try:
        return fn()
    except Exception:
        return fallback


def _owner_scope() -> tuple[str, str]:
    from core.scheduler.loop import _active_char_id_or_none, _owner_id

    uid = str(_owner_id() or "")
    char_id = str(_active_char_id_or_none() or "")
    return uid, char_id


def _tool_loop_row(cfg: dict[str, Any], uid: str) -> dict[str, Any]:
    from core.tool_dispatcher import tool_loop_active
    from core import pipeline_registry

    section = cfg.get("tool_loop") or {}
    configured, source = _configured(section, "enabled", False)
    configured = bool(configured)
    effective = bool(_safe_call(lambda: tool_loop_active(uid), False))
    reason = None
    effective_source = source
    card_override = _safe_call(
        lambda: (getattr(getattr(pipeline_registry.get(), "character", None), "presence_ext", {}) or {}).get("tool_loop"),
        None,
    )
    if card_override in {"on", "off"}:
        effective_source = "character_card"
    if card_override == "off":
        effective = False
        reason = "character_card_override"
    if not configured and card_override != "on":
        status = "disabled"
        reason = "tool_loop_disabled"
    elif card_override == "off":
        status = "unavailable"
    elif effective:
        status = "enabled"
    else:
        status = "unavailable"
        effective_source = "runtime_gate"
        owner = str((cfg.get("scheduler") or {}).get("owner_id") or "")
        if owner and owner != uid:
            reason = "owner_only_runtime_gate"
        else:
            reason = "chat_preset_not_function_calling_or_unavailable"
    return _row(
        feature_id="tool_loop",
        default=False,
        configured=configured,
        effective=effective,
        source=effective_source,
        status=status,
        reason=reason,
        consumer="core.tool_dispatcher.tool_loop_active",
        edit_page="conversation-settings",
        details={"path": "path_c", "max_steps": section.get("max_steps", 5)},
    )


def _mcp_row(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("mcp_servers") or {}
    configured, source = _configured(section, "enabled", False)
    configured = bool(configured)
    servers = [item for item in (section.get("servers") or []) if isinstance(item, dict)]
    enabled_servers = [item for item in servers if bool(item.get("enabled", True))]
    connected_names: list[str] = []
    if enabled_servers:
        try:
            from core import mcp_client

            connected_names = [
                str(item.get("name") or "")
                for item in enabled_servers
                if mcp_client.server_runtime(str(item.get("name") or "")).get("connected", False)
            ]
        except Exception:
            connected_names = []
    effective = bool(configured and connected_names)
    if not configured:
        status, reason, effective_source = "disabled", "mcp_disabled", source
    elif not enabled_servers:
        status, reason, effective_source = "unavailable", "no_enabled_mcp_server", "runtime_gate"
    elif not connected_names:
        status, reason, effective_source = "unavailable", "no_connected_mcp_server", "runtime_gate"
    else:
        status, reason, effective_source = "enabled", None, source
    return _row(
        feature_id="mcp",
        default=False,
        configured=configured,
        effective=effective,
        source=effective_source,
        status=status,
        reason=reason,
        consumer="core.mcp_client.server_runtime",
        edit_page="mcp",
        details={
            "configured_server_count": len(servers),
            "enabled_server_count": len(enabled_servers),
            "connected_server_count": len(connected_names),
        },
    )


def _self_capability_row(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("self_management") or {}
    configured, source = _configured(section, "enabled", True)
    effective = bool(configured)
    return _row(
        feature_id="self_capability",
        default=True,
        configured=bool(configured),
        effective=effective,
        source=source,
        status="enabled" if effective else "disabled",
        reason=None if effective else "self_management_disabled",
        consumer="core.self_management.policy.feature_enabled",
        edit_page="autonomy-settings",
    )


def _autonomy_and_scheduler_rows(cfg: dict[str, Any], uid: str, char_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from core.autonomy.effective_state import build_effective_state

    state = _safe_call(lambda: build_effective_state(uid, char_id), {})
    scheduler = state.get("scheduler") or {}
    autonomy = state.get("autonomy") or {}
    scheduler_configured = bool(scheduler.get("effective_value"))
    scheduler_runtime = scheduler.get("runtime") or {}
    scheduler_effective = bool(scheduler_configured and scheduler_runtime.get("available", False))
    scheduler_status = "enabled" if scheduler_effective else ("disabled" if not scheduler_configured else "unavailable")
    scheduler_reason = None if scheduler_effective else ("scheduler_disabled" if not scheduler_configured else "scheduler_not_running")
    scheduler_source = scheduler.get("override_source") or "config"
    if scheduler_source.startswith("config."):
        scheduler_source = "config"
    scheduler_row = _row(
        feature_id="scheduler",
        default=True,
        configured=scheduler.get("configured_value", True),
        effective=scheduler_effective,
        source=scheduler_source,
        status=scheduler_status,
        reason=scheduler_reason,
        consumer=scheduler.get("runtime_consumer", "core.scheduler.loop._loop"),
        edit_page="scheduler",
        details={"runtime": scheduler_runtime},
    )
    capability = autonomy.get("self_capability") or {}
    autonomy_source = autonomy.get("override_source") or "config"
    if autonomy_source in {"autonomy_config", "autonomy_config_disabled"}:
        autonomy_source = "config"
    if autonomy_source == "self_capability":
        autonomy_source = "agent_override" if capability.get("selected_value") is not None else "user_grant"
    autonomy_effective = bool(autonomy.get("effective_value"))
    proactive = state.get("proactive") or {}
    proactive_state = proactive.get("state")
    proactive_reason = proactive.get("reason")
    if not autonomy_effective:
        autonomy_status, autonomy_reason = "disabled", "autonomy_disabled"
    elif proactive_state in {"blocked", "cooled_down"}:
        autonomy_status, autonomy_reason = "blocked", proactive_reason
    elif proactive_state == "unavailable":
        autonomy_status, autonomy_reason = "unavailable", proactive_reason
    else:
        autonomy_status, autonomy_reason = "enabled", None
    autonomy_row = _row(
        feature_id="autonomy",
        default=False,
        configured=autonomy.get("configured_value", False),
        effective=autonomy_effective,
        source=autonomy_source,
        status=autonomy_status,
        reason=autonomy_reason,
        consumer=autonomy.get("runtime_consumer", "core.autonomy.runner.tick"),
        edit_page="autonomy-settings",
        details={
            "proactive_state": proactive_state,
            "proactive_reason": proactive_reason,
            "self_capability": capability,
        },
    )
    return scheduler_row, autonomy_row


def _channels_row() -> dict[str, Any]:
    from channels.registry import get_active

    channels = _safe_call(lambda: [str(channel.name) for channel in get_active()], [])
    effective = bool(channels)
    return _row(
        feature_id="channels",
        default=False,
        configured=effective,
        effective=effective,
        source="runtime_gate",
        status="enabled" if effective else "unavailable",
        reason=None if effective else "no_active_channel",
        consumer="channels.registry.get_active",
        edit_page="status",
        read_only=True,
        details={"active_channels": channels},
    )


def _model_routing_row(cfg: dict[str, Any], char_id: str) -> dict[str, Any]:
    from core.model_registry import _get_preset_config, resolve_routing_info

    model_cfg = _safe_call(_get_preset_config, None)
    if not model_cfg:
        return _row(
            feature_id="model_routing",
            default="default",
            configured=None,
            effective=None,
            source="config",
            status="unavailable",
            reason="no_model_presets",
            consumer="core.model_registry.resolve_routing_info",
            edit_page="model-routing",
            details={"resolved_chat_preset": "", "character_binding": None},
        )
    configured = model_cfg.get("active_routing", "default")
    info = _safe_call(lambda: resolve_routing_info(char_id), {})
    effective = info.get("effective_profile") or configured
    char_override = info.get("model_routing")
    source = "character_card" if char_override and char_override == effective and char_override != configured else "config"
    ready = bool(info.get("resolved_chat_preset"))
    return _row(
        feature_id="model_routing",
        default="default",
        configured=configured,
        effective=effective,
        source=source,
        status="enabled" if ready else "unavailable",
        reason=None if ready else "no_chat_preset",
        consumer="core.model_registry.resolve_routing_info",
        edit_page="model-routing",
        details={"resolved_chat_preset": info.get("resolved_chat_preset", ""), "character_binding": char_override},
    )


def _embedding_row(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("embedding") or {}

    def _usable(value: Any) -> bool:
        text = str(value or "").strip().upper()
        return bool(text) and not text.startswith("YOUR_") and not text.startswith("YOUR-")

    configured = bool(
        _usable(section.get("base_url"))
        and _usable(section.get("model"))
        and section.get("dim")
    )
    return _row(
        feature_id="embedding",
        default=False,
        configured=configured,
        effective=configured,
        source="config" if "embedding" in cfg else "default",
        status="enabled" if configured else "unavailable",
        reason=None if configured else "embedding_not_configured",
        consumer="core.memory.vector_store",
        edit_page="embedding-config",
        details={"model": section.get("model", ""), "dim": section.get("dim")},
    )


def _tts_row(cfg: dict[str, Any]) -> dict[str, Any]:
    from core.output.voice_adapter import get_provider_status

    section = cfg.get("tts") or {}
    configured, source = _configured(section, "enabled", False)
    configured = bool(configured)
    provider = _safe_call(lambda: get_provider_status(section), {"ready": False, "reason": "tts_provider_unavailable", "provider": ""})
    effective = bool(configured and provider.get("ready", False))
    if not configured:
        status, reason, effective_source = "disabled", "tts_disabled", source
    elif not effective:
        status, reason, effective_source = "unavailable", provider.get("reason") or "tts_provider_unavailable", "runtime_gate"
    else:
        status, reason, effective_source = "enabled", None, source
    return _row(
        feature_id="tts",
        default=False,
        configured=configured,
        effective=effective,
        source=effective_source,
        status=status,
        reason=reason,
        consumer="core.output.voice_adapter.get_provider_status",
        edit_page="tts-config",
        details={"provider": provider.get("provider", ""), "provider_ready": bool(provider.get("ready"))},
    )


def _hardware_row(cfg: dict[str, Any]) -> dict[str, Any]:
    from core.tool_dispatcher import intiface_opted_in

    section = cfg.get("hardware") or {}
    configured_value, configured_source = _configured(section, "intiface_opt_in", False)
    configured = bool(configured_value)
    hardware_enabled = bool(section.get("enabled", False))
    effective = bool(configured and hardware_enabled and _safe_call(intiface_opted_in, False))
    if not configured:
        status, reason, source = "dormant", "intiface_frozen_default", configured_source
    elif not hardware_enabled:
        status, reason, source = "dormant", "hardware_global_disabled", "runtime_gate"
    elif not effective:
        status, reason, source = "unavailable", "intiface_runtime_unavailable", "runtime_gate"
    else:
        status, reason, source = "enabled", None, "config"
    return _row(
        feature_id="hardware_intiface",
        default=False,
        configured=configured,
        effective=effective,
        source=source,
        status=status,
        reason=reason,
        consumer="core.tool_dispatcher.intiface_opted_in",
        edit_page="tools",
        details={"frozen": not effective, "hardware_enabled": hardware_enabled, "mcp": False},
    )


def build_global_effective_state(uid: str, char_id: str) -> dict[str, Any]:
    """Return the complete, safe overview projection without secret material."""
    from core.config_loader import get_config

    cfg = get_config() or {}
    rows: list[dict[str, Any]] = [
        _tool_loop_row(cfg, uid),
        _mcp_row(cfg),
        _self_capability_row(cfg),
    ]
    scheduler, autonomy = _autonomy_and_scheduler_rows(cfg, uid, char_id)
    rows.extend([scheduler, autonomy, _channels_row()])
    rows.extend([
        _model_routing_row(cfg, char_id),
        _embedding_row(cfg),
        _tts_row(cfg),
        _hardware_row(cfg),
    ])
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": time.time(),
        "uid": str(uid),
        "char_id": str(char_id),
        "features": rows,
        "features_by_id": {row["id"]: row for row in rows},
    }


def build_scoped_global_effective_state() -> dict[str, Any]:
    uid, char_id = _owner_scope()
    if not uid or not char_id:
        raise RuntimeError("owner or active character is not configured")
    return build_global_effective_state(uid, char_id)
