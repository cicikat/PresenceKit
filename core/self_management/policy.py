"""Effective capability overlay. Absence of state preserves legacy behavior."""
from __future__ import annotations

from typing import Any

from core.self_management import registry, store


def feature_enabled() -> bool:
    """Global master switch; absent config preserves the existing capability behavior."""
    from core.config_loader import get_config
    return bool((get_config().get("self_management") or {}).get("enabled", True))


def _grant(state: dict[str, Any], capability_id: str) -> dict[str, Any] | None:
    capability_id = registry._canonical(capability_id)
    value = (state.get("grants") or {}).get(capability_id)
    return value if isinstance(value, dict) else None


def _selected(state: dict[str, Any], capability_id: str, default: Any) -> Any:
    value = (state.get("agent_state") or {}).get(capability_id)
    return value if isinstance(value, (bool, int, str, list, dict)) and not isinstance(value, tuple) else default


def global_available(capability_id: str) -> bool:
    capability_id = registry._canonical(capability_id)
    spec = registry.resolve(capability_id)
    if spec is None:
        return False
    if spec.kind == "tool":
        from core.tool_dispatcher import _is_tool_enabled
        return bool(_is_tool_enabled(spec.tool_name))
    if spec.kind == "setting":
        # High-risk settings remain observable; their default grant is false
        # and the agent gate rejects mutation even after an admin override.
        return True
    return True


def effective(capability_id: str, uid: str, char_id: str) -> tuple[bool, Any]:
    """Return global availability intersected with user grant and agent choice."""
    if not feature_enabled():
        # Disabling the feature makes durable overrides dormant and preserves
        # the legacy tool/autonomy defaults without deleting user state.
        return True, None
    capability_id = registry._canonical(capability_id)
    spec = registry.resolve(capability_id)
    if spec is None:
        return False, None
    if not global_available(capability_id):
        return False, None
    state = store.load(uid, char_id)
    grant = _grant(state, capability_id)
    # Safe management settings are granted on a fresh install.  Legacy tool
    # and autonomy IDs retain their explicit-grant semantics for compatibility.
    if grant is None:
        if spec.kind == "setting":
            from core.self_management import settings
            return bool(spec.default_grant), settings.read(uid, char_id, capability_id)
        if not store.exists(uid, char_id):
            return True, None
        return True, None
    if not bool(grant.get("allowed", False)):
        if spec.kind == "setting":
            from core.self_management import settings
            return False, settings.read(uid, char_id, capability_id)
        return False, None
    if spec.kind == "setting":
        from core.self_management import settings
        return True, settings.read(uid, char_id, capability_id)
    if spec is not None and spec.kind == "autonomy_min_interval":
        value = (state.get("agent_state") or {}).get(capability_id)
        return True, value if isinstance(value, int) and not isinstance(value, bool) else None
    return bool(_selected(state, capability_id, True)), _selected(state, capability_id, True)


def tool_allowed(uid: str, char_id: str, tool_name: str) -> bool:
    capability_id = registry.capability_for_tool(tool_name)
    return capability_id is not None and effective(capability_id, uid, char_id)[0]


def autonomy_enabled(uid: str, char_id: str, base_enabled: bool) -> bool:
    allowed, selected = effective(registry.AUTONOMY_ENABLED, uid, char_id)
    return bool(base_enabled and allowed and (selected is not False))


def autonomy_min_interval(uid: str, char_id: str, base_seconds: int) -> int:
    allowed, value = effective(registry.AUTONOMY_MIN_INTERVAL, uid, char_id)
    if not allowed or not isinstance(value, int) or isinstance(value, bool):
        return int(base_seconds)
    return max(60, int(value))


def can_agent_manage(uid: str, char_id: str, capability_id: str) -> tuple[bool, str]:
    if not feature_enabled():
        return False, "self_management_disabled"
    capability_id = registry._canonical(capability_id)
    state = store.load(uid, char_id)
    spec = registry.resolve(capability_id)
    if spec is None:
        if registry.is_protected(capability_id):
            return False, "protected_setting"
        return False, "unknown_capability"
    if not spec.mutable_by_agent:
        return False, "managed_by_user_only"
    grant = _grant(state, capability_id)
    if grant is None and spec.kind == "setting" and spec.default_grant:
        grant = {"allowed": True, "mutable_by_agent": spec.mutable_by_agent}
    if grant is None or not bool(grant.get("allowed", False)):
        return False, "not_granted"
    if not bool(grant.get("mutable_by_agent", False)):
        return False, "managed_by_user_only"
    if bool((state.get("locks") or {}).get(capability_id, False)):
        return False, "locked_by_user"
    if spec.high_risk:
        return False, "high_risk_requires_admin"
    if not global_available(capability_id):
        return False, "globally_unavailable"
    return True, "allowed"
