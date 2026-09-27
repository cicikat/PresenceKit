from __future__ import annotations

from core.self_management.models import CapabilityChange


def _change(capability_id, value, revision, action_id):
    return CapabilityChange("set_value", capability_id, value, "control-plane test", revision, action_id)


def test_fresh_install_exposes_safe_management_matrix(sandbox):
    from core.self_management import registry
    from core.self_management.service import agent_gateway_context, view

    snapshot = view("u1", "char_a")
    row = next(item for item in snapshot["capabilities"] if item["capability_id"] == registry.TOOL_LOOP_ENABLED)
    assert row["grant"] is None
    assert row["mutable_by_agent"] is False
    assert row["high_risk"] is False
    talk = next(item for item in snapshot["capabilities"] if item["capability_id"] == "setting.autonomy.talk_enabled")
    assert talk["grant"]["default"] is True
    assert talk["mutable_by_agent"] is True
    context = agent_gateway_context("u1", "char_a")
    assert context and any(item["id"] == "setting.autonomy.talk_enabled" for item in context["mutable_capabilities"])
    assert all(item["id"] != registry.TOOL_LOOP_ENABLED for item in context["mutable_capabilities"])


def test_global_and_privacy_controls_remain_owner_only_with_stale_grants(sandbox):
    import asyncio
    import json

    from core.self_management import policy, registry, store
    from core.self_management.service import agent_change, agent_gateway_context, user_grant
    from core.tool_dispatcher import _list_self_capabilities_wrapper

    owner_only = (
        registry.TOOL_LOOP_ENABLED, registry.MCP_ENABLED, registry.SCHEDULER_ENABLED,
        registry.AUTONOMY_SETTING_ENABLED, "setting.tool_loop.exposure:path_c",
        "setting.tool.read_life_records.enabled", "setting.tool.fs_read.enabled",
    )
    state = store.load("u1", "char_a")
    for capability_id in owner_only:
        assert registry.resolve(capability_id) is not None
        assert not user_grant("u1", "char_a", capability_id=capability_id,
                              allowed=True, mutable_by_agent=True, constraints={}, reason="old grant").ok
        state["grants"][capability_id] = {"allowed": True, "mutable_by_agent": True}
    assert store.save("u1", "char_a", state)
    for index, capability_id in enumerate(owner_only):
        assert policy.can_agent_manage("u1", "char_a", capability_id) == (False, "managed_by_user_only")
        result = agent_change("u1", "char_a", _change(capability_id, True, 0, f"attempt-{index}"),
                              source="assistant_self_management")
        assert result.code == "managed_by_user_only"
    context = agent_gateway_context("u1", "char_a")
    assert context and not set(owner_only) & {item["id"] for item in context["mutable_capabilities"]}
    listed = json.loads(asyncio.run(_list_self_capabilities_wrapper(user_id="u1", char_id="char_a")))
    rows = {row["key"]: row for row in listed["capabilities"]}
    assert all(rows[capability_id]["can_self_modify"] is False for capability_id in owner_only)


def test_protected_secret_auth_and_url_changes_are_rejected(sandbox):
    from core.self_management.service import agent_change, user_grant

    for capability_id in ("auth.disabled", "secret.api_key", "mcp.import_url", "setting.tool_loop.arbitrary_path"):
        result = agent_change("u1", "char_a", _change(capability_id, True, 0, capability_id), source="assistant_self_management")
        assert result.code in {"protected_setting", "unknown_capability"}
        assert not user_grant("u1", "char_a", capability_id=capability_id, allowed=True, mutable_by_agent=True, constraints={}, reason="no").ok


def test_high_risk_tool_policy_is_visible_but_not_agent_mutable(sandbox):
    from core.self_management import registry
    from core.self_management.service import agent_change, user_grant, view

    capability_id = "setting.tool.device_shutdown.enabled"
    spec = registry.resolve(capability_id)
    if spec is None:
        # The registry may omit an optional tool in a minimal installation.
        return
    assert spec.high_risk is True
    assert not user_grant("u1", "char_a", capability_id=capability_id, allowed=True, mutable_by_agent=True, constraints={}, reason="admin review").ok
    assert user_grant("u1", "char_a", capability_id=capability_id, allowed=True, mutable_by_agent=False, constraints={}, reason="owner only").ok
    result = agent_change("u1", "char_a", _change(capability_id, True, 1, "danger"), source="assistant_self_management")
    assert result.code == "managed_by_user_only"
    row = next(item for item in view("u1", "char_a")["capabilities"] if item["capability_id"] == capability_id)
    assert row["high_risk"] is True


def test_setting_mutation_uses_revision_and_audit(sandbox):
    from core.self_management import settings, store
    from core.self_management.service import agent_change

    capability_id = "setting.autonomy.talk_enabled"
    original = settings.read("u1", "char_a", capability_id)
    first = agent_change("u1", "char_a", _change(capability_id, not original, 0, "talk-toggle"), source="assistant_self_management")
    assert first.ok and first.revision == 1
    conflict = agent_change("u1", "char_a", _change(capability_id, original, 0, "stale"), source="assistant_self_management")
    assert conflict.code == "revision_conflict"
    audit = store.read_audit("u1", "char_a", limit=10)
    assert any(item.get("result") == "revision_conflict" for item in audit)
