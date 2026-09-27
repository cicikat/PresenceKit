from __future__ import annotations

from copy import deepcopy

from admin.config_control import ConfigDocument
from core.self_management.models import CapabilityChange


def _change(capability_id, value, revision, action_id):
    return CapabilityChange("set_value", capability_id, value, "control-plane test", revision, action_id)


def test_fresh_install_exposes_safe_management_matrix(sandbox):
    from core.self_management import registry
    from core.self_management.service import agent_gateway_context, view

    snapshot = view("u1", "char_a")
    row = next(item for item in snapshot["capabilities"] if item["capability_id"] == registry.TOOL_LOOP_ENABLED)
    assert row["grant"]["default"] is True
    assert row["grant"]["mutable_by_agent"] is True
    assert row["high_risk"] is False
    context = agent_gateway_context("u1", "char_a")
    assert context and any(item["id"] == registry.TOOL_LOOP_ENABLED for item in context["mutable_capabilities"])


def test_agent_can_change_global_server_allowlist_and_preset(sandbox, monkeypatch):
    import core.config_loader as loader
    from core.self_management import registry, settings
    from core.self_management.service import agent_change, restore_user_setting

    cfg = {
        "tool_loop": {"enabled": False, "tool_presets": [{"name": "read_only", "tools": ["get_time"]}]},
        "mcp_servers": {"enabled": False, "servers": [{"name": "cedar", "enabled": False, "allow_tools": ["status"], "tool_policy": {"status": {"effect": "read", "require_confirm": False}}}]},
        "scheduler": {},
    }
    monkeypatch.setattr(loader, "get_config", lambda: cfg)
    monkeypatch.setattr(settings, "get_config", lambda: cfg)
    monkeypatch.setattr(settings, "reload_config", lambda: cfg)
    monkeypatch.setattr(settings, "read_config_file", lambda _path: ConfigDocument(cfg))

    def persist(_path, document):
        cfg.clear()
        cfg.update(deepcopy(dict(document)))

    monkeypatch.setattr(settings, "write_config_file", persist)

    enabled = agent_change("u1", "char_a", _change(registry.TOOL_LOOP_ENABLED, True, 0, "a1"), source="assistant_self_management")
    assert enabled.ok and cfg["tool_loop"]["enabled"] is True
    mcp = agent_change("u1", "char_a", _change(registry.MCP_ENABLED, True, 1, "a2"), source="assistant_self_management")
    assert mcp.ok and cfg["mcp_servers"]["enabled"] is True
    server = agent_change("u1", "char_a", _change("mcp.server:cedar.enabled", True, 2, "a3"), source="assistant_self_management")
    assert server.ok and cfg["mcp_servers"]["servers"][0]["enabled"] is True
    allow = agent_change("u1", "char_a", _change("mcp.server:cedar.allowlist", ["status"], 3, "a4"), source="assistant_self_management")
    assert allow.ok and cfg["mcp_servers"]["servers"][0]["allow_tools"] == ["status"]
    policy = agent_change("u1", "char_a", _change("mcp.server:cedar.policy:status", {"require_confirm": True}, 4, "a5-policy"), source="assistant_self_management")
    assert policy.ok and cfg["mcp_servers"]["servers"][0]["tool_policy"]["status"]["require_confirm"] is True
    high_risk = agent_change("u1", "char_a", _change("mcp.server:cedar.policy:status", {"effect": "unrestricted"}, 5, "a6-policy"), source="assistant_self_management")
    assert high_risk.code == "high_risk_requires_admin"
    preset = agent_change("u1", "char_a", _change("tool_loop.preset:read_only", ["get_time"], 5, "a6"), source="assistant_self_management")
    assert preset.ok and preset.code == "unchanged" and preset.revision == 5
    scheduler = agent_change("u1", "char_a", _change(registry.SCHEDULER_ENABLED, True, 5, "a7"), source="assistant_self_management")
    assert scheduler.ok and cfg["scheduler"]["enabled"] is True
    restored = restore_user_setting("u1", "char_a", capability_id=registry.TOOL_LOOP_ENABLED, reason="restore")
    assert restored.ok and cfg["tool_loop"]["enabled"] is False


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
    assert user_grant("u1", "char_a", capability_id=capability_id, allowed=True, mutable_by_agent=True, constraints={}, reason="admin review").ok
    result = agent_change("u1", "char_a", _change(capability_id, True, 1, "danger"), source="assistant_self_management")
    assert result.code == "high_risk_requires_admin"
    row = next(item for item in view("u1", "char_a")["capabilities"] if item["capability_id"] == capability_id)
    assert row["high_risk"] is True


def test_setting_mutation_uses_revision_and_audit(sandbox):
    from core.self_management import registry, store
    from core.self_management.service import agent_change

    first = agent_change("u1", "char_a", _change(registry.AUTONOMY_SETTING_ENABLED, True, 0, "autonomy-on"), source="assistant_self_management")
    assert first.ok and first.revision == 1
    conflict = agent_change("u1", "char_a", _change(registry.AUTONOMY_SETTING_ENABLED, False, 0, "stale"), source="assistant_self_management")
    assert conflict.code == "revision_conflict"
    audit = store.read_audit("u1", "char_a", limit=10)
    assert any(item.get("result") == "revision_conflict" for item in audit)
