from __future__ import annotations

from core.self_management.models import CapabilityChange


def test_agent_changes_are_scoped_by_uid_and_character(sandbox):
    from core.self_management import policy
    from core.self_management.service import agent_change, user_grant

    assert user_grant("u1", "char_a", capability_id="autonomy.enabled", allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    changed = agent_change("u1", "char_a", CapabilityChange("disable", "autonomy.enabled", None, "quiet", 1, "a1"), source="assistant_self_management")
    assert changed.ok
    assert policy.autonomy_enabled("u1", "char_a", True) is False
    assert policy.autonomy_enabled("u1", "char_b", True) is True
    assert policy.autonomy_enabled("u2", "char_a", True) is True


def test_agent_change_requires_grant_lock_revision_and_idempotency(sandbox):
    from core.self_management.service import agent_change, set_lock, user_grant

    request = CapabilityChange("disable", "autonomy.enabled", None, "quiet", 0, "a1")
    assert agent_change("u1", "char_a", request, source="assistant_self_management").code == "not_granted"
    assert user_grant("u1", "char_a", capability_id="autonomy.enabled", allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    assert agent_change("u1", "char_a", request, source="assistant_self_management").code == "revision_conflict"
    from core.self_management import store
    assert store.read_audit("u1", "char_a", limit=10)[-1]["result"] == "revision_conflict"
    applied = agent_change("u1", "char_a", CapabilityChange("disable", "autonomy.enabled", None, "quiet", 1, "a1"), source="assistant_self_management")
    assert applied.ok and applied.revision == 2
    assert agent_change("u1", "char_a", CapabilityChange("disable", "autonomy.enabled", None, "quiet", 2, "a1"), source="assistant_self_management").code == "idempotent"
    assert set_lock("u1", "char_a", capability_id="autonomy.enabled", locked=True, reason="freeze").ok
    assert agent_change("u1", "char_a", CapabilityChange("enable", "autonomy.enabled", None, "resume", 3, "a2"), source="assistant_self_management").code == "locked_by_user"


def test_minimum_interval_is_constrained_and_restorable(sandbox):
    from core.self_management import policy
    from core.self_management.service import agent_change, restore_user_setting, user_grant

    assert user_grant("u1", "char_a", capability_id="autonomy.min_interval_seconds", allowed=True, mutable_by_agent=True, constraints={"minimum": 120, "maximum": 300}, reason="bounded").ok
    assert agent_change("u1", "char_a", CapabilityChange("set_value", "autonomy.min_interval_seconds", 60, "shorten", 1, "a1"), source="assistant_self_management").code == "value_out_of_constraints"
    changed = agent_change("u1", "char_a", CapabilityChange("set_value", "autonomy.min_interval_seconds", 180, "normal", 1, "a2"), source="assistant_self_management")
    assert changed.ok
    assert policy.autonomy_min_interval("u1", "char_a", 900) == 180
    assert restore_user_setting("u1", "char_a", capability_id="autonomy.min_interval_seconds", reason="restore").ok
    assert policy.autonomy_min_interval("u1", "char_a", 900) == 900


def test_management_gateway_cannot_manage_itself_or_accept_wrong_origin(sandbox):
    from core.self_management.registry import capability_for_tool, resolve
    from core.self_management.service import agent_change

    assert capability_for_tool("manage_self_capability") is None
    assert resolve("tool.use:manage_self_capability") is None
    result = agent_change("u1", "char_a", CapabilityChange("disable", "autonomy.enabled", None, "quiet", 0, "a1"), source="assistant_loop")
    assert result.code == "invalid_source"


def test_global_feature_switch_makes_agent_overrides_dormant(sandbox, monkeypatch):
    from core.self_management import policy
    from core.self_management.service import agent_change, agent_gateway_context, user_grant

    assert user_grant("u1", "char_a", capability_id="autonomy.enabled", allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"self_management": {"enabled": False}})
    assert policy.autonomy_enabled("u1", "char_a", True) is True
    assert agent_gateway_context("u1", "char_a") is None
    result = agent_change("u1", "char_a", CapabilityChange("disable", "autonomy.enabled", None, "quiet", 1, "a1"), source="assistant_self_management")
    assert result.code == "self_management_disabled"


def test_tool_overlay_applies_to_chat_schema_and_autonomy_allowlist(sandbox):
    from core.autonomy import policy as autonomy_policy, store as autonomy_store
    from core.self_management.service import user_grant
    from core.tool_dispatcher import get_tools_schema

    assert user_grant("u1", "char_a", capability_id="tool.use:get_time", allowed=False, mutable_by_agent=False, constraints={}, reason="disable").ok
    names = {schema["function"]["name"] for schema in get_tools_schema(uid="u1", char_id="char_a")}
    assert "get_time" not in names
    state = autonomy_store.load("u1", "char_a")
    state["config"]["tools"] = {"get_time": {"enabled": True}}
    assert "get_time" not in {schema["function"]["name"] for schema in autonomy_policy.allowed_tools("u1", "char_a", state)}


def test_management_tool_is_hidden_from_regular_schema_and_rejects_regular_origin(sandbox):
    import asyncio

    from core.tool_dispatcher import execute_structured, get_tools_schema

    assert "manage_self_capability" not in {schema["function"]["name"] for schema in get_tools_schema(uid="u1", char_id="char_a")}
    result = asyncio.run(execute_structured("manage_self_capability", {"action": "disable", "capability_id": "autonomy.enabled", "reason": "quiet", "expected_revision": 0, "action_id": "a1"}, "u1", "u1", False, object(), origin="assistant_loop", char_id="char_a"))
    assert result.confirmation_request is None
    assert "自主管理" in result.result


def test_self_management_origin_cannot_execute_a_business_tool(sandbox):
    import asyncio

    from core.tool_dispatcher import execute_structured

    result = asyncio.run(execute_structured("get_time", {}, "u1", "u1", False, object(), origin="autonomy_self_management", char_id="char_a"))
    assert result.confirmation_request is None
    assert "只能修改自身能力" in result.result


def test_admin_routes_use_active_owner_character_scope(sandbox, monkeypatch):
    import asyncio

    import admin.routers.self_management as api

    monkeypatch.setattr(api, "_scope", lambda: ("owner", "char_a"))
    created = asyncio.run(api.set_grant({"capability_id": "autonomy.enabled", "allowed": True, "mutable_by_agent": True, "constraints": {}, "reason": "allow"}, auth=None))
    assert created["ok"] and created["revision"] == 1
    view = asyncio.run(api.get_self_management(auth=None))
    row = next(row for row in view["capabilities"] if row["capability_id"] == "autonomy.enabled")
    assert row["grant"]["allowed"] is True


def test_audit_contains_only_capability_state_not_transport_secrets(sandbox):
    from core.self_management import store
    from core.self_management.service import agent_change, user_grant

    assert user_grant("u1", "char_a", capability_id="autonomy.enabled", allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    assert agent_change("u1", "char_a", CapabilityChange("disable", "autonomy.enabled", None, "quiet", 1, "a1"), source="assistant_self_management").ok
    rendered = repr(store.read_audit("u1", "char_a"))
    assert "authorization" not in rendered.lower()
    assert "bearer" not in rendered.lower()
