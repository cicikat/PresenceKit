"""Closed capability registry and the safe management policy matrix.

Management capabilities are deliberately separate from admin scopes.  A
capability is a narrow, typed setting operation; it is never an alias for an
admin endpoint or a config-file path supplied by the model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

AUTONOMY_ENABLED = "autonomy.enabled"
AUTONOMY_MIN_INTERVAL = "autonomy.min_interval_seconds"

SETTING_PREFIX = "setting."
TOOL_LOOP_ENABLED = "setting.tool_loop.enabled"
MCP_ENABLED = "setting.mcp.enabled"
SCHEDULER_ENABLED = "setting.scheduler.enabled"
AUTONOMY_SETTING_ENABLED = "setting.autonomy.enabled"

_PROTECTED_PREFIXES = (
    "secret.", "auth.", "authentication.", "token.", "password.",
    "api_key.", "network.", "proxy.", "server.bind", "server.listen",
    "mcp.import", "mcp.url", "mcp.server.url", "data.delete",
    "retention.delete", "retention.destructive",
)
_SCHEMA_REVISION = 0

# Only character-scoped, low-risk preferences may be changed by the character.
# Global tool/MCP/scheduler controls and read access remain owner-controlled.
_SELF_MUTABLE_SETTINGS = frozenset({
    "setting.autonomy.talk_enabled",
    "setting.autonomy.min_interval_seconds",
    "setting.autonomy.interval.seconds",
})


def rebuild_effective_schema() -> int:
    """Invalidate the lightweight registry view after a setting mutation."""
    global _SCHEMA_REVISION
    _SCHEMA_REVISION += 1
    return _SCHEMA_REVISION


def schema_revision() -> int:
    return _SCHEMA_REVISION


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    kind: str
    tool_name: str = ""
    default_grant: bool = False
    mutable_by_agent: bool = False
    user_lockable: bool = True
    requires_confirmation: bool = False
    high_risk: bool = False
    value_type: str = "bool"


def _management(capability_id: str, *, value_type: str = "bool", default: bool = False,
                mutable: bool = False, high_risk: bool = False,
                confirmation: bool = False) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id, "setting", default_grant=default, mutable_by_agent=mutable,
        requires_confirmation=confirmation, high_risk=high_risk, value_type=value_type,
    )


def _canonical(capability_id: str) -> str:
    """Accept the short IDs used by early P0 callers while returning stable IDs."""
    value = str(capability_id or "").strip()
    aliases = {
        "tool_loop.enabled": TOOL_LOOP_ENABLED,
        "mcp.enabled": MCP_ENABLED,
        "scheduler.enabled": SCHEDULER_ENABLED,
        "autonomy.setting.enabled": AUTONOMY_SETTING_ENABLED,
    }
    if value in aliases:
        return aliases[value]
    if value.startswith("tool_loop.") or (value.startswith("mcp.") and not value.startswith("mcp.use:")) or value.startswith("scheduler."):
        return SETTING_PREFIX + value
    if value.startswith("autonomy.setting."):
        return SETTING_PREFIX + value
    if value.startswith("tool.") and not value.startswith("tool.use:"):
        return SETTING_PREFIX + value
    return value


def is_protected(capability_id: str) -> bool:
    value = _canonical(capability_id).lower()
    unwrapped = value[len(SETTING_PREFIX):] if value.startswith(SETTING_PREFIX) else value
    return unwrapped.startswith(_PROTECTED_PREFIXES) or any(
        marker in value for marker in (".api_key", ".password", ".token", ".secret", ".headers")
    )


def capability_for_tool(tool_name: str) -> str | None:
    from core.tool_dispatcher import _INTIFACE_TOOL_NAMES, _TOOL_REGISTRY, intiface_opted_in

    info = _TOOL_REGISTRY.get(tool_name)
    if not isinstance(info, dict):
        return None
    if info.get("self_management"):
        return None
    if tool_name in _INTIFACE_TOOL_NAMES and not intiface_opted_in():
        return None
    if info.get("category") == "mcp":
        server = str(info.get("mcp_server") or "")
        tool = str(info.get("mcp_tool") or "")
        if not server or not tool:
            return None
        return f"mcp.use:{server}/{tool}"
    return f"tool.use:{tool_name}"


def _configured_server(name: str) -> dict[str, Any] | None:
    from core.config_loader import get_config
    for item in ((get_config().get("mcp_servers") or {}).get("servers") or []):
        if isinstance(item, dict) and str(item.get("name") or "") == name:
            return item
    return None


def _dynamic_setting(capability_id: str) -> CapabilitySpec | None:
    value = _canonical(capability_id)
    if value in {TOOL_LOOP_ENABLED, MCP_ENABLED, SCHEDULER_ENABLED, AUTONOMY_SETTING_ENABLED}:
        return _management(value)
    if value.startswith("setting.tool_loop.preset:"):
        from core.config_loader import get_config
        name = value.split(":", 1)[1]
        configured = {
            str(item.get("name") or "")
            for item in ((get_config().get("tool_loop") or {}).get("tool_presets") or [])
            if isinstance(item, dict)
        }
        return _management(value, value_type="string_list") if name in configured else None
    if value.startswith("setting.tool_loop.exposure:"):
        path = value.split(":", 1)[1]
        return _management(value, value_type="object") if path in {"path_a", "path_c"} else None
    if value.startswith("setting.tool_loop."):
        field = value.rsplit(".", 1)[-1]
        allowed_fields = {"enabled", "max_steps", "total_timeout_s", "nudge_hint", "categories", "exclude_tools"}
        if field not in allowed_fields:
            return None
        return _management(value, value_type="integer" if field in {"max_steps", "total_timeout_s"} else "string_list" if field in {"categories", "exclude_tools"} else "bool")
    if value.startswith("setting.tool.") and value.endswith(".enabled"):
        tool_name = value[len("setting.tool."):-len(".enabled")]
        from core.tool_dispatcher import _TOOL_REGISTRY
        info = _TOOL_REGISTRY.get(tool_name)
        if not isinstance(info, dict) or info.get("self_management"):
            return None
        high_risk = bool(info.get("dangerous") or info.get("effect") in {"actuate", "emergency"})
        return _management(value, high_risk=high_risk, confirmation=high_risk)
    if value.startswith("setting.mcp.server:"):
        rest = value[len("setting.mcp.server:"):]
        name, sep, field = rest.rpartition(".")
        server = _configured_server(name) if sep else None
        if server is None:
            return None
        if field == "enabled":
            return _management(value)
        if field == "allowlist":
            return _management(value, value_type="string_list")
        if field.startswith("policy:"):
            tool_name = field.split(":", 1)[1]
            local_policy = (server.get("tool_policy") or {}).get(tool_name)
            if not isinstance(local_policy, dict):
                return None
            effect = str(local_policy.get("effect") or "")
            high_risk = effect in {"actuate", "emergency", "unrestricted"}
            return _management(
                value, value_type="object", default=not high_risk,
                mutable=not high_risk, high_risk=high_risk,
                confirmation=high_risk,
            )
        return None
    if value.startswith("setting.scheduler."):
        field = value[len("setting.scheduler."):]
        bool_fields = {"enabled", "morning_greeting", "night_reminder", "random_message", "daily_journal", "period_reminder", "diary_reminder", "diary_inject", "presence_nag"}
        if field in bool_fields:
            return _management(value)
        if field in {"presence_nag_minutes", "global_proactive_min_gap_seconds"}:
            return _management(value, value_type="integer")
        return None
    if value.startswith("setting.autonomy."):
        field = value[len("setting.autonomy."):]
        bool_fields = {"enabled", "talk_enabled", "interval.enabled", "overflow.enabled", "schedule.enabled"}
        int_fields = {"min_interval_seconds", "daily_evaluation_budget", "max_steps", "max_tools", "max_write_tools", "total_timeout_seconds", "tool_timeout_seconds", "interval.seconds"}
        if field in bool_fields:
            return _management(value, default=value in _SELF_MUTABLE_SETTINGS,
                               mutable=value in _SELF_MUTABLE_SETTINGS)
        if field in int_fields:
            return _management(value, value_type="integer",
                               default=value in _SELF_MUTABLE_SETTINGS,
                               mutable=value in _SELF_MUTABLE_SETTINGS)
        return None
    return None


def resolve(capability_id: str) -> CapabilitySpec | None:
    value = _canonical(capability_id)
    if value == AUTONOMY_ENABLED:
        return CapabilitySpec(value, "autonomy_enabled", mutable_by_agent=True)
    if value == AUTONOMY_MIN_INTERVAL:
        return CapabilitySpec(value, "autonomy_min_interval", mutable_by_agent=True, value_type="integer")
    if value.startswith("tool.use:"):
        tool_name = value[len("tool.use:"):]
        if capability_for_tool(tool_name) == value:
            return CapabilitySpec(value, "tool", tool_name)
        return None
    if value.startswith("mcp.use:"):
        from core.tool_dispatcher import _TOOL_REGISTRY
        suffix = value[len("mcp.use:"):]
        for name, info in _TOOL_REGISTRY.items():
            if isinstance(info, dict) and info.get("category") == "mcp" and f"{info.get('mcp_server')}/{info.get('mcp_tool')}" == suffix:
                return CapabilitySpec(value, "tool", name)
        return None
    return _dynamic_setting(value)


def display_name(capability_id: str) -> str:
    spec = resolve(capability_id)
    return spec.tool_name if spec and spec.kind == "tool" else _canonical(capability_id)


def policy_matrix() -> list[dict[str, Any]]:
    """Return the safe management contract without transport configuration."""
    rows = []
    for spec in list_available():
        if spec.kind != "setting":
            continue
        rows.append({
            "capability_id": spec.capability_id,
            "default_grant": spec.default_grant,
            "mutable_by_agent": spec.mutable_by_agent,
            "user_lockable": spec.user_lockable,
            "user_lock": spec.user_lockable,
            "requires_confirmation": spec.requires_confirmation,
            "confirmation": spec.requires_confirmation,
            "high_risk": spec.high_risk,
            "value_type": spec.value_type,
        })
    return rows


def list_available() -> list[CapabilitySpec]:
    from core.tool_dispatcher import _TOOL_REGISTRY

    items: list[CapabilitySpec] = [
        CapabilitySpec(AUTONOMY_ENABLED, "autonomy_enabled"),
        CapabilitySpec(AUTONOMY_MIN_INTERVAL, "autonomy_min_interval", value_type="integer"),
    ]
    for tool_name in _TOOL_REGISTRY:
        capability_id = capability_for_tool(tool_name)
        if capability_id:
            spec = resolve(capability_id)
            if spec is not None:
                items.append(spec)

    # Stable global controls.
    for capability_id in (TOOL_LOOP_ENABLED, MCP_ENABLED, SCHEDULER_ENABLED, AUTONOMY_SETTING_ENABLED):
        items.append(resolve(capability_id))
    # Ordinary tool execution switches are safe unless the registry marks them
    # dangerous or actuating.  Frozen Intiface tools never enter this list.
    for name, info in _TOOL_REGISTRY.items():
        if isinstance(info, dict) and not info.get("self_management"):
            spec = resolve(f"setting.tool.{name}.enabled")
            if spec is not None:
                items.append(spec)

    from core.config_loader import get_config
    cfg = get_config()
    loop = cfg.get("tool_loop") or {}
    for field in ("max_steps", "total_timeout_s", "nudge_hint", "categories", "exclude_tools"):
        spec = resolve(f"setting.tool_loop.{field}")
        if spec:
            items.append(spec)
    for preset in loop.get("tool_presets") or []:
        if isinstance(preset, dict) and str(preset.get("name") or ""):
            spec = resolve(f"setting.tool_loop.preset:{preset['name']}")
            if spec:
                items.append(spec)
    for path in ("path_a", "path_c"):
        spec = resolve(f"setting.tool_loop.exposure:{path}")
        if spec:
            items.append(spec)

    servers = (cfg.get("mcp_servers") or {}).get("servers") or []
    for server in servers:
        if not isinstance(server, dict) or not str(server.get("name") or ""):
            continue
        name = str(server["name"])
        for field in ("enabled", "allowlist"):
            spec = resolve(f"setting.mcp.server:{name}.{field}")
            if spec:
                items.append(spec)
        for tool_name in (server.get("tool_policy") or {}):
            spec = resolve(f"setting.mcp.server:{name}.policy:{tool_name}")
            if spec:
                items.append(spec)
    scheduler_fields = ("enabled", "morning_greeting", "night_reminder", "random_message", "daily_journal", "period_reminder", "diary_reminder", "diary_inject", "presence_nag", "presence_nag_minutes", "global_proactive_min_gap_seconds")
    items.extend(filter(None, (resolve(f"setting.scheduler.{field}") for field in scheduler_fields)))
    autonomy_fields = ("enabled", "talk_enabled", "min_interval_seconds", "daily_evaluation_budget", "max_steps", "max_tools", "max_write_tools", "total_timeout_seconds", "tool_timeout_seconds", "interval.enabled", "interval.seconds", "overflow.enabled", "schedule.enabled")
    items.extend(filter(None, (resolve(f"setting.autonomy.{field}") for field in autonomy_fields)))
    return sorted({item.capability_id: item for item in items if item is not None}.values(), key=lambda item: item.capability_id)
