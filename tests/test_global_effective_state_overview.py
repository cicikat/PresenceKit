from pathlib import Path
from types import SimpleNamespace


def test_character_can_enable_tool_loop_when_global_default_is_off(monkeypatch):
    from core.control_center.effective_state import _tool_loop_row
    from core import pipeline_registry, tool_dispatcher

    monkeypatch.setattr(tool_dispatcher, "tool_loop_active", lambda uid: True)
    monkeypatch.setattr(pipeline_registry, "get", lambda: SimpleNamespace(
        character=SimpleNamespace(presence_ext={"tool_loop": "on"})))
    row = _tool_loop_row({"tool_loop": {"enabled": False}}, "owner")
    assert row["configured_value"] is False
    assert row["effective_value"] is True
    assert row["override_source"] == "character_card"


def test_routing_summary_is_safe_and_follows_global_after_reset(monkeypatch):
    from core import model_registry as registry

    monkeypatch.setattr(registry, "_get_preset_config", lambda: {
        "active_routing": "main", "routing_profiles": {"main": {"chat": "primary"}},
        "presets": {"primary": {"model": "example-model", "base_url": "https://example.invalid",
                                "api_key": "test-private-value"}},
    })
    monkeypatch.setattr(registry, "_char_model_routing", lambda char_id: None)
    monkeypatch.setattr(registry, "_resolve_preset_name", lambda *args, **kwargs: "primary")
    result = registry.resolve_routing_info("example")
    assert result["model_routing"] is None
    assert result["binding_source"] == "global"
    assert result["resolved_chat_model"] == "example-model"
    assert result["chat_configured"] is True
    assert "test-private-value" not in str(result)
    assert "example.invalid" not in str(result)


def test_global_effective_state_contract_covers_required_features(sandbox):
    from core.control_center.effective_state import build_global_effective_state

    payload = build_global_effective_state("owner", "char")
    assert payload["contract_version"] == "global-effective-state.v1"
    rows = {row["id"]: row for row in payload["features"]}
    assert {
        "tool_loop", "mcp", "self_capability", "autonomy", "scheduler",
        "channels", "model_routing", "embedding", "tts", "hardware_intiface",
    } <= set(rows)
    required = {
        "default_value", "configured_value", "effective_value", "override_source",
        "runtime_status", "blocking_reason", "restart_required", "edit_path",
    }
    assert all(required <= set(row) for row in rows.values())
    assert all(row["override_source"] in {
        "default", "config", "character_card", "user_grant", "agent_override", "runtime_gate",
    } for row in rows.values())
    assert rows["hardware_intiface"]["runtime_status"] == "dormant"
    assert rows["hardware_intiface"]["details"]["mcp"] is False


def test_control_center_endpoint_and_overview_use_the_contract():
    from admin.admin_server import app
    from admin_static_assets import read_admin_client_source, read_admin_page

    paths = {route.path for route in app.routes}
    assert "/admin/control-center/effective-state" in paths
    assert "/admin/effective-state" in paths
    source = read_admin_client_source()
    overview = read_admin_page("overview")
    assert "loadOverview" in source
    assert "/admin/control-center/effective-state" in source
    assert 'id="overview-effective-state"' in overview


def test_overview_static_asset_version_is_explicit():
    index = (Path(__file__).parents[1] / "admin" / "static" / "index.html").read_text(encoding="utf-8")
    assert "/static/js/overview.js?v=brief-180-admin-static-1" in index
