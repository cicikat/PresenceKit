from __future__ import annotations

from core.autonomy.models import Disposition

# Autonomous writes are deliberately narrower than the normal tool-loop
# surface. New entries require an explicit review here; a configurable
# allowlist must never turn a reminder, memory edit, desktop action, or device
# control into an unattended side effect.
_SANDBOXED_WRITE_TOOLS = frozenset({"water_garden"})


def tool_is_eligible(name: str, policy: dict, *, registry: dict, effect: str) -> bool:
    """Return whether one explicitly configured tool is safe for autonomy."""
    info = registry.get(name, {})
    if effect == "read":
        # MCP reads additionally require the operator to acknowledge that an
        # interrupted request has an unknown outcome. Builtins have no remote
        # side effect to reconcile.
        return info.get("category") != "mcp" or (
            bool(policy.get("mcp_explicit"))
            and policy.get("outcome_unknown") == "fail_closed"
        )
    if effect == "write":
        return name in _SANDBOXED_WRITE_TOOLS and info.get("category") == "info"
    return False


def tool_eligibility(name: str, policy: dict, *, registry: dict, effect: str) -> tuple[bool, str]:
    """Public-facing reason used by the configuration API/UI."""
    info = registry.get(name, {})
    if info.get("dangerous") or info.get("require_confirm") or effect not in {"read", "write"}:
        return False, "side_effect_or_confirmation_required"
    if effect == "write" and name not in _SANDBOXED_WRITE_TOOLS:
        return False, "write_not_sandboxed_for_autonomy"
    if info.get("category") == "mcp" and not bool(policy.get("mcp_explicit")):
        return False, "mcp_requires_explicit_enablement"
    if info.get("category") == "mcp" and policy.get("outcome_unknown") != "fail_closed":
        return False, "mcp_requires_fail_closed_outcome_policy"
    return (tool_is_eligible(name, policy, registry=registry, effect=effect), "eligible")


def admission(uid: str, char_id: str, state: dict, *, allow_observed_activity: bool = False) -> str | None:
    cfg = state["config"]
    if not cfg.get("enabled", False):
        return Disposition.SUPPRESSED_PROACTIVE_OFF.value
    from core.autonomy.effective_state import autonomy_enabled, autonomy_min_interval
    if not autonomy_enabled(uid, char_id, state):
        return Disposition.SUPPRESSED_PROACTIVE_OFF.value
    from core.autonomy.store import circuit_open
    if circuit_open(state):
        return Disposition.CIRCUIT_OPEN.value
    from core.character_loader import is_proactive_disabled
    if is_proactive_disabled():
        return Disposition.SUPPRESSED_PROACTIVE_OFF.value
    from core.dream.dream_state import DreamGuardStatus, get_reality_guard_status
    try:
        guard = get_reality_guard_status(uid)
    except Exception:
        return Disposition.BLOCKED_DREAM_UNCERTAIN.value
    if guard == DreamGuardStatus.BLOCK_UNCERTAIN:
        return Disposition.BLOCKED_DREAM_UNCERTAIN.value
    if guard != DreamGuardStatus.ALLOW:
        return Disposition.BLOCKED_DREAM.value
    from core.scheduler.state_machine import TriggerState, get_state
    trigger_state = get_state(uid)
    if trigger_state != TriggerState.QUIET and not (allow_observed_activity and trigger_state == TriggerState.RESTLESS):
        return Disposition.BLOCKED_USER_ACTIVE.value
    from core.conversation_gate import conversation_lock
    if conversation_lock(uid).locked():
        return Disposition.BLOCKED_USER_ACTIVE.value
    # A queued owner message and a held conversation lock are different phases
    # of the normal turn.  Do not start an autonomy run in the gap between them.
    try:
        from core.message_queue import active_sessions, queue_size
        if str(uid) in active_sessions() or queue_size(str(uid)) > 0:
            return Disposition.BLOCKED_USER_ACTIVE.value
    except Exception:
        # Queue observation is advisory; the stronger conversation lock above
        # remains the hard boundary if an optional implementation is unavailable.
        pass
    # Activity sessions own their own user-facing lifecycle. Autonomy must not
    # interleave tools into an active game/reading session.
    try:
        from core.activity.store import find_active_session
        from core.activity.types import ALLOWED_ACTIVITY_TYPES
        if any(find_active_session(char_id, str(uid), kind) for kind in ALLOWED_ACTIVITY_TYPES):
            return Disposition.BLOCKED_USER_ACTIVE.value
        from core.coplay.session import is_active as coplay_active
        if coplay_active(uid, char_id=char_id):
            return Disposition.BLOCKED_USER_ACTIVE.value
    except Exception:
        return Disposition.BLOCKED_USER_ACTIVE.value
    from core.autonomy.store import roll_daily
    roll_daily(state)
    daily = state.get("daily", {})
    budget = int(cfg.get("daily_evaluation_budget") or 0)
    evaluations = int(daily.get("evaluations") or 0)
    talks = int(daily.get("talks") or 0)
    # Budget must not permanently mute a day that never got a real talk_sent.
    # Admission-only retries no longer burn evaluations, but this兜底 still
    # protects older state and any remaining counter leak.
    if budget > 0 and evaluations >= budget and talks > 0:
        return Disposition.SUPPRESSED_DAILY_BUDGET.value
    latest = max(
        (float((value or {}).get("last_evaluated_at") or 0) for value in state.get("sources", {}).values()),
        default=0.0,
    )
    import time
    effective_minimum = autonomy_min_interval(uid, char_id, state)
    if latest and time.time() - latest < effective_minimum:
        return Disposition.DUPLICATE.value
    return None


def allowed_tools(uid: str, char_id: str, state: dict) -> list[dict]:
    from core.tool_dispatcher import get_tools_schema
    schemas = {((s.get("function") or s).get("name")): s for s in get_tools_schema(char_id=char_id, uid=uid)}
    return [schemas[row["name"]] for row in tool_decisions(uid, char_id, state) if row["execution_allowed"] and row["name"] in schemas]


def tool_decisions(uid: str, char_id: str, state: dict) -> list[dict]:
    """Return a safe, execution-time decision matrix for the autonomy surface."""
    from core.config_loader import get_config
    from core.self_management import registry as capability_registry, store as capability_store
    from core.self_management.policy import effective as capability_effective
    from core.tool_dispatcher import _TOOL_REGISTRY, _is_tool_enabled, get_tool_effect, get_tools_schema, is_side_effect_tool

    schemas = {((item.get("function") or item).get("name")) for item in get_tools_schema(char_id=char_id, uid=uid)}
    configured = state.get("config", {}).get("tools", {})
    capability_state = capability_store.load(uid, char_id)
    mcp_config = get_config().get("mcp_servers", {}) or {}
    server_config = {
        str(item.get("name") or ""): item
        for item in (mcp_config.get("servers") or [])
        if isinstance(item, dict)
    }
    rows = []
    for name, info in _TOOL_REGISTRY.items():
        if info.get("self_management"):
            continue
        configured_policy = configured.get(name) if isinstance(configured.get(name), dict) else {}
        if name == "observe_user_screen" and name not in configured:
            from core.perception.screen_observation import enabled as screen_enabled
            configured_policy = {"enabled": screen_enabled()}
        if name in {'read_life_records', 'search_documents', 'read_document', 'reread_image'} and name not in configured:
            from core.life_records import settings as life_settings
            life_cfg = life_settings()
            configured_policy = {'enabled': name != 'read_life_records' or bool(life_cfg['enabled'] and life_cfg['character_readable'])}
        effect = get_tool_effect(name) or ("write" if is_side_effect_tool(name) else "read")
        eligible, eligibility_reason = tool_eligibility(name, configured_policy, registry=_TOOL_REGISTRY, effect=effect)
        is_mcp = info.get("category") == "mcp"
        connected = True
        registered = True
        mcp_policy_ok = True
        mcp_policy_reason = "not_mcp"
        if is_mcp:
            from core.mcp_client import server_runtime
            server = str(info.get("mcp_server") or "")
            runtime = server_runtime(server)
            connected = bool(runtime.get("connected"))
            registered = name in set(runtime.get("registered_tools") or [])
            server_cfg = server_config.get(server) or {}
            if bool(mcp_config.get("require_local_policy")):
                mcp_tool = str(info.get("mcp_tool") or "")
                local = (server_cfg.get("tool_policy") or {}).get(mcp_tool)
                mcp_policy_ok = mcp_tool in set(server_cfg.get("allow_tools") or []) and isinstance(local, dict)
                mcp_policy_reason = "local_policy_ok" if mcp_policy_ok else "mcp_local_policy_denied"
            else:
                mcp_policy_reason = "local_policy_not_required"
        capability_id = capability_registry.capability_for_tool(name)
        self_capability, agent_selected_state = capability_effective(capability_id, uid, char_id) if capability_id else (False, None)
        grant = (capability_state.get("grants") or {}).get(capability_id) if capability_id else None
        explicitly_enabled = bool(configured_policy.get("enabled"))
        direct_mcp_read = bool(
            is_mcp and effect == "read" and self_capability and name in schemas
            and connected and registered and mcp_policy_ok and not info.get("dangerous")
            and not info.get("require_confirm")
        )
        final_schema = bool(direct_mcp_read or (
            explicitly_enabled and eligible and self_capability and name in schemas
            and connected and registered and mcp_policy_ok
        ))
        denial = ""
        if not _is_tool_enabled(name):
            denial = "globally_disabled"
        elif not self_capability:
            denial = "self_capability_disabled"
        elif not explicitly_enabled and not direct_mcp_read:
            denial = "autonomy_allowlist_disabled"
        elif not eligible and not direct_mcp_read:
            denial = eligibility_reason
        elif not connected:
            denial = "mcp_server_disconnected"
        elif not registered:
            denial = "mcp_tool_not_registered"
        elif not mcp_policy_ok:
            denial = mcp_policy_reason
        elif name not in schemas:
            denial = "schema_unavailable"
        rows.append({
            "name": name,
            "source": "mcp" if is_mcp else "builtin",
            "global_enabled": bool(_is_tool_enabled(name)),
            "registered": registered,
            "mcp_server_connected": connected if is_mcp else None,
            "mcp_policy": mcp_policy_reason,
            "mcp_policy_allowed": mcp_policy_ok,
            "self_capability_effective": self_capability,
            "self_capability_granted": bool((grant or {}).get("allowed")) if grant is not None else None,
            "agent_selected_state": agent_selected_state,
            "autonomy_allowlist": explicitly_enabled,
            "mcp_explicit": bool(configured_policy.get("mcp_explicit")),
            "effect": effect,
            "dangerous": bool(info.get("dangerous")),
            "require_confirm": bool(info.get("require_confirm")),
            "eligible": eligible,
            "eligibility_reason": eligibility_reason,
            "final_schema": final_schema,
            "execution_allowed": final_schema,
            "enabled": final_schema,
            "denial_reason": denial,
        })
    return rows
