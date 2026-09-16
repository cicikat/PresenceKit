"""Contracts for making active character routing overrides visible in admin."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


def _pinned_default() -> dict:
    return {
        "model_routing": "default",
        "effective_profile": "default",
        "resolved_chat_preset": "deepseek-chat",
    }


def test_active_character_override_keeps_default_profile_distinct_from_inherit(monkeypatch):
    import admin.routers.settings_llm as settings_llm

    monkeypatch.setattr(settings_llm, "_active_character_id_for_routing_warning", lambda: "active-card")
    monkeypatch.setattr("core.character_loader.load", lambda _char_id: SimpleNamespace(name="Active card"))
    monkeypatch.setattr("core.model_registry.resolve_routing_info", lambda _char_id: _pinned_default())

    assert settings_llm._active_character_routing_override() == {
        "char_id": "active-card",
        "label": "Active card",
        **_pinned_default(),
    }


def test_active_character_override_omits_unset_or_invalid_bindings(monkeypatch):
    import admin.routers.settings_llm as settings_llm

    monkeypatch.setattr(settings_llm, "_active_character_id_for_routing_warning", lambda: "active-card")
    monkeypatch.setattr("core.character_loader.load", lambda _char_id: SimpleNamespace(name="Active card"))

    monkeypatch.setattr(
        "core.model_registry.resolve_routing_info",
        lambda _char_id: {
            "model_routing": None,
            "effective_profile": "gpt-main",
            "resolved_chat_preset": "gpt",
        },
    )
    assert settings_llm._active_character_routing_override() is None

    monkeypatch.setattr(
        "core.model_registry.resolve_routing_info",
        lambda _char_id: {
            "model_routing": "removed-profile",
            "effective_profile": "gpt-main",
            "resolved_chat_preset": "gpt",
        },
    )
    assert settings_llm._active_character_routing_override() is None


def test_model_presets_response_includes_the_active_character_override(monkeypatch):
    import admin.routers.settings_llm as settings_llm

    monkeypatch.setattr(
        "core.model_registry._get_preset_config",
        lambda: {
            "active_routing": "gpt-main",
            "presets": {"gpt": {"api_key": "secret"}},
            "routing_profiles": {"gpt-main": {"chat": "gpt"}},
            "defaults": {},
        },
    )
    monkeypatch.setattr(settings_llm, "get_config", lambda: {"model_presets": {}})
    monkeypatch.setattr(
        settings_llm,
        "_active_character_routing_override",
        lambda: {"char_id": "active-card", **_pinned_default()},
    )

    result = asyncio.run(settings_llm.get_model_presets(auth="dummy"))

    assert result["active_routing"] == "gpt-main"
    assert result["active_character_routing"] == {"char_id": "active-card", **_pinned_default()}
    assert result["presets"]["gpt"]["api_key"] == "***"
    rpg = result["routing_effective"]["gpt-main"]["rpg_kp"]
    assert rpg["effective_preset"] == "gpt"
    assert rpg["source"] == "chat_fallback"
    assert "sensor_judge" in result["routing_effective"]["gpt-main"]
    assert "monologue" in result["routing_effective"]["gpt-main"]


def test_model_routing_page_renders_the_override_warning_without_html_injection():
    from admin_static_assets import read_admin_client_source, read_admin_page

    page = read_admin_page("model-routing")
    source = read_admin_client_source()

    assert 'id="mr-active-character-routing-warning"' in page
    assert 'data-i18n="routing.active_binding_hint"' in page
    assert "function _renderActiveCharacterRoutingWarning(override)" in source
    assert "_renderActiveCharacterRoutingWarning(data.active_character_routing);" in source
    assert "window.addEventListener('admin-language-changed'" in source
    warning_function = source.split("function _renderActiveCharacterRoutingWarning(override)", 1)[1].split(
        "async function loadModelRouting()", 1
    )[0]
    assert ".textContent = t(" in warning_function
    assert ".innerHTML" not in warning_function


def test_model_routing_page_exposes_profile_rename_delete_and_default_preset():
    from admin_static_assets import read_admin_client_source, read_admin_page

    page = read_admin_page("model-routing")
    source = read_admin_client_source()
    assert 'id="mr-default-preset-select"' in page
    assert "data-action=\"saveDefaultPreset\"" in page
    assert "`/model-presets/routing-profiles/${encodeURIComponent(previousName)}/rename`" in source
    assert "`/model-presets/routing-profiles/${encodeURIComponent(name)}`" in source
    assert "api('DELETE', `/model-presets/routing-profiles/${encodeURIComponent(name)}`)" in source
    assert "api('PUT', '/model-presets/default-preset'" in source
    assert "nameInput.disabled = false" in source.split("function openProfileModal(name)", 1)[1].split(
        "function closeProfileModal()", 1
    )[0]
    assert "confirmDeleteProfile" in source
    assert "（清除映射，走默认 preset / chat）" in source


def test_model_routing_editor_exposes_rpg_kp_sensor_judge_and_prefixed_monologue():
    from admin_static_assets import read_admin_client_source, read_admin_page

    page = read_admin_page("model-routing")
    source = read_admin_client_source()
    categories = source.split("const MR_CATEGORIES = [", 1)[1].split("];", 1)[0]
    assert "'rpg_kp'" in categories
    assert "'sensor_judge'" in categories
    assert "'monologue'" in categories
    assert "前置独白 / 额外思考链" in source
    assert 'data-i18n="routing.category.rpg_kp"' in page
    assert 'data-i18n="routing.category.sensor_judge"' in page
    assert 'data-i18n="routing.category.monologue"' in page
    assert "RPG Dream 中立裁决" in page


def test_model_routing_override_copy_is_localized_in_both_languages():
    from pathlib import Path

    i18n = (Path(__file__).parents[1] / "admin" / "static" / "i18n.js").read_text(encoding="utf-8")
    for key in (
        "routing.active_binding_hint",
        "dynamic.routing.active_character_override",
        "routing.category.rpg_kp",
        "routing.category.sensor_judge",
        "routing.category.monologue",
        "routing.discover_models",
        "routing.discover_available",
        "routing.available_models",
        "routing.discovery_hint",
        "routing.discovery.fetching",
        "routing.default_preset",
        "routing.default_preset_hint",
        "routing.save_default_preset",
    ):
        assert i18n.count(f"'{key}'") == 2
