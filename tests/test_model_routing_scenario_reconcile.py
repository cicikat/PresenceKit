from __future__ import annotations


def _cfg(profile: dict) -> dict:
    return {
        "model_presets": {
            "active_routing": "default",
            "presets": {
                "chat-preset": {"provider_kind": "openai", "model": "chat-model"},
                "intent-preset": {"provider_kind": "deepseek", "model": "intent-model"},
                "reconcile-preset": {"provider_kind": "openai", "model": "reconcile-model"},
            },
            "routing_profiles": {"default": profile},
        }
    }


def test_scenario_reconcile_fallback_is_category_then_intent_then_chat(monkeypatch):
    import core.model_registry as registry

    monkeypatch.setattr(registry, "get_config", lambda: _cfg({
        "chat": "chat-preset",
        "intent": "intent-preset",
    }))
    assert registry._resolve_preset_name("scenario_reconcile") == "intent-preset"
    assert registry.resolve_category_info("scenario_reconcile")["source"] == "intent_fallback"

    monkeypatch.setattr(registry, "get_config", lambda: _cfg({
        "chat": "chat-preset",
        "intent": "intent-preset",
        "scenario_reconcile": "reconcile-preset",
    }))
    info = registry.resolve_category_info("scenario_reconcile")
    assert info["effective_preset"] == "reconcile-preset"
    assert info["source"] == "category"

    monkeypatch.setattr(registry, "get_config", lambda: _cfg({"chat": "chat-preset"}))
    assert registry._resolve_preset_name("scenario_reconcile") == "chat-preset"
    assert registry.resolve_category_info("scenario_reconcile")["source"] == "chat_fallback"


def test_character_profile_wins_and_legacy_synth_includes_category(monkeypatch):
    import core.model_registry as registry

    monkeypatch.setattr(registry, "get_config", lambda: _cfg({
        "chat": "chat-preset",
        "scenario_reconcile": "reconcile-preset",
    }))
    monkeypatch.setattr(registry, "_char_model_routing", lambda _char_id: "default")
    assert registry._resolve_preset_name("scenario_reconcile", char_id="character-a") == "reconcile-preset"
    assert not hasattr(registry, "_synth_legacy_presets")


def test_routing_info_exposes_effective_reconciler_preset(monkeypatch):
    import core.model_registry as registry

    monkeypatch.setattr(registry, "get_config", lambda: _cfg({
        "chat": "chat-preset",
        "intent": "intent-preset",
        "scenario_reconcile": "reconcile-preset",
    }))
    info = registry.resolve_routing_info("character-a")
    assert info["resolved_scenario_reconcile_preset"] == "reconcile-preset"
