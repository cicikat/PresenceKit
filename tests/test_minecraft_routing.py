from core import model_registry as registry


def test_reaction_route_resolution_and_effective_view_share_fallback(monkeypatch):
    config = {"presets": {"main": {"model": "main"}, "small": {"model": "small"}, "fast": {"model": "fast"}},
              "routing_profiles": {"default": {"chat": "main", "sensor_judge": "small"}},
              "active_routing": "default", "default_preset": "main"}
    monkeypatch.setattr(registry, "_get_preset_config", lambda: config)
    monkeypatch.setattr(registry, "_active_char_model_routing", lambda: None)
    monkeypatch.setattr(registry, "_char_model_routing", lambda _: None)
    assert registry._resolve_preset_name("minecraft_reaction") == "small"
    assert registry.resolve_category_info("minecraft_reaction")["source"] == "sensor_fallback"
    config["routing_profiles"]["default"]["minecraft_reaction"] = "fast"
    assert registry._resolve_preset_name("minecraft_reaction") == "fast"
    assert registry.resolve_category_info("minecraft_reaction")["effective_preset"] == "fast"
    captured = []
    monkeypatch.setattr(registry, "_model_clients", {})
    monkeypatch.setattr(registry, "_build_model_client", lambda name, request_policy: captured.append((name,request_policy)) or object())
    registry.get_model_client("minecraft_reaction")
    assert captured == [("fast", {"timeout_s": 3, "max_retries": 0})]
