"""
tests/test_model_presets.py — multi-model preset system contract tests.

Coverage (per task spec §9):
  1. Param merge + whitelist: anthropic_compat drops penalty; deepseek keeps it.
  2. prompt_style resolution: preset explicit > provider_kind default.
  3. Routing fallback: unknown category → chat preset; missing profile → first preset.
  4. Backward-compat synth: flat llm: config → correct legacy preset + kind detection.
  5. xml transform: system layers wrapped; user/assistant untouched; tags sanitised; order preserved.
"""

import types
import httpx
import pytest


TEST_API_KEY = "test-only-placeholder"


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_preset_wire_headers_preserve_auth_and_identify_application(monkeypatch, protocol):
    import core.model_registry as registry

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "test-response", "output": [], "choices": []})

    monkeypatch.setattr(registry, "_get_preset_config", lambda: {
        "defaults": {}, "presets": {"test": {
            "api_protocol": protocol, "base_url": "https://relay.example/v1",
            "api_key": TEST_API_KEY, "model": "test-model",
        }},
    })
    monkeypatch.setattr(registry, "_get_proxy_url", lambda: None)
    monkeypatch.setattr(registry, "_make_http_client", lambda *a, **kw:
                        httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    mc = registry.build_client_for_preset("test")
    try:
        if protocol == "responses":
            await mc.client.responses.create(model=mc.model, input="ping")
        else:
            await mc.client.chat.completions.create(
                model=mc.model, messages=[{"role": "user", "content": "ping"}])
        assert len(requests) == 1
        assert requests[0].headers["user-agent"] == "PresenceKit/1.0"
        assert requests[0].headers["authorization"] == f"Bearer {TEST_API_KEY}"
        assert requests[0].url.path == (
            "/v1/responses" if protocol == "responses" else "/v1/chat/completions")
    finally:
        await mc.client.close()


# ===========================================================================
# 1. Param merge + whitelist
# ===========================================================================

class TestResolveParams:
    def setup_method(self):
        from core.model_registry import resolve_params
        self.resolve = resolve_params

    def test_anthropic_compat_drops_penalty(self):
        defaults = {
            "temperature": 1.0,
            "top_p": 0.9,
            "max_tokens": 4000,
            "frequency_penalty": 0.3,
            "presence_penalty": 0.4,
        }
        result = self.resolve(defaults, {}, "anthropic_compat")
        assert "frequency_penalty" not in result
        assert "presence_penalty" not in result
        assert result["temperature"] == 1.0
        assert result["max_tokens"] == 4000

    def test_deepseek_keeps_penalty(self):
        defaults = {
            "temperature": 1.0,
            "top_p": 0.9,
            "max_tokens": 4000,
            "frequency_penalty": 0.3,
            "presence_penalty": 0.4,
        }
        result = self.resolve(defaults, {}, "deepseek")
        assert result["frequency_penalty"] == 0.3
        assert result["presence_penalty"] == 0.4

    def test_preset_params_override_defaults(self):
        defaults = {"temperature": 1.0, "max_tokens": 4000}
        preset_params = {"temperature": 0.5}
        result = self.resolve(defaults, preset_params, "deepseek")
        assert result["temperature"] == 0.5
        assert result["max_tokens"] == 4000

    def test_unknown_provider_uses_openai_whitelist(self):
        defaults = {"temperature": 0.7, "frequency_penalty": 0.3}
        result = self.resolve(defaults, {}, "unknown_provider")
        # falls back to openai profile which includes frequency_penalty
        assert "frequency_penalty" in result

    def test_local_drops_penalty(self):
        defaults = {"temperature": 0.7, "frequency_penalty": 0.3}
        result = self.resolve(defaults, {}, "local")
        assert "frequency_penalty" not in result
        assert result["temperature"] == 0.7


# ===========================================================================
# 2. prompt_style resolution
# ===========================================================================

class TestPromptStyleResolution:
    def _make_preset_cfg(self, preset_overrides: dict, kind: str = "deepseek") -> dict:
        preset = {"provider_kind": kind, **preset_overrides}
        return {
            "active_routing": "default",
            "defaults": {},
            "presets": {"p": preset},
            "routing_profiles": {"default": {"chat": "p"}},
        }

    def _build(self, preset_overrides: dict, kind: str = "deepseek"):
        from core.model_registry import PROVIDER_PROFILES, _FALLBACK_PROFILE
        preset = {"provider_kind": kind, **preset_overrides}
        profile = PROVIDER_PROFILES.get(kind, _FALLBACK_PROFILE)
        return preset.get("prompt_style") or profile["default_prompt_style"]

    def test_explicit_xml_wins_over_kind_default(self):
        style = self._build({"prompt_style": "xml"}, kind="deepseek")
        assert style == "xml"

    def test_deepseek_default_is_narrative(self):
        style = self._build({}, kind="deepseek")
        assert style == "narrative"

    def test_anthropic_compat_default_is_xml(self):
        style = self._build({}, kind="anthropic_compat")
        assert style == "xml"

    def test_local_default_is_narrative(self):
        style = self._build({}, kind="local")
        assert style == "narrative"

    def test_explicit_narrative_overrides_anthropic_default(self):
        style = self._build({"prompt_style": "narrative"}, kind="anthropic_compat")
        assert style == "narrative"


@pytest.mark.asyncio
async def test_native_anthropic_preset_uses_httpx_client_and_keeps_auth_mode(monkeypatch):
    import core.model_registry as registry

    monkeypatch.setattr(registry, "_get_preset_config", lambda: {
        "defaults": {},
        "presets": {
            "claude-native": {
                "provider_kind": "anthropic_compat",
                "api_protocol": "anthropic_messages",
                "anthropic_auth_mode": "bearer",
                "base_url": "https://relay.example",
                "api_key": TEST_API_KEY,
                "model": "claude-test",
            },
        },
    })
    monkeypatch.setattr(registry, "_get_proxy_url", lambda: None)

    client = registry._build_model_client("claude-native")
    try:
        assert isinstance(client.client, httpx.AsyncClient)
        assert client.base_url == "https://relay.example"
        assert client.anthropic_auth_mode == "bearer"
    finally:
        await client.client.aclose()


# ===========================================================================
# 3. Routing fallback
# ===========================================================================

class TestRoutingFallback:
    def _make_registry(self, mp: dict):
        """Patch get_config to return a config with the given model_presets."""
        import core.model_registry as reg
        return reg, mp

    def _resolve(self, mp: dict, category: str) -> str:
        import core.model_registry as reg
        original = reg._get_preset_config
        reg._get_preset_config = lambda: mp
        try:
            return reg._resolve_preset_name(category)
        finally:
            reg._get_preset_config = original

    def test_known_category_routes_correctly(self):
        mp = {
            "active_routing": "default",
            "presets": {"ds": {}, "cl": {}},
            "routing_profiles": {
                "default": {"chat": "cl", "probe": "ds"},
            },
        }
        assert self._resolve(mp, "probe") == "ds"
        assert self._resolve(mp, "chat") == "cl"

    def test_unknown_category_falls_back_to_chat(self):
        mp = {
            "active_routing": "default",
            "presets": {"ds": {}},
            "routing_profiles": {
                "default": {"chat": "ds"},
            },
        }
        assert self._resolve(mp, "nonexistent_category") == "ds"

    def test_missing_active_profile_uses_first_profile(self):
        mp = {
            "active_routing": "missing",
            "presets": {"ds": {}},
            "routing_profiles": {
                "default": {"chat": "ds"},
            },
        }
        # "missing" not in profiles → falls to first profile → "default" → chat="ds"
        assert self._resolve(mp, "chat") == "ds"

    def test_empty_profile_falls_back_to_first_preset(self):
        mp = {
            "active_routing": "default",
            "presets": {"only_preset": {}},
            "routing_profiles": {"default": {}},  # no chat key
        }
        result = self._resolve(mp, "chat")
        assert result == "only_preset"

    def test_rpg_kp_falls_back_to_chat_when_unmapped(self):
        mp = {
            "active_routing": "default",
            "presets": {"ds": {}, "kp": {}},
            "routing_profiles": {
                "default": {"chat": "ds"},
            },
        }
        assert self._resolve(mp, "rpg_kp") == "ds"

    def test_rpg_kp_uses_explicit_mapping(self):
        mp = {
            "active_routing": "default",
            "presets": {"ds": {}, "kp": {}},
            "routing_profiles": {
                "default": {"chat": "ds", "rpg_kp": "kp"},
            },
        }
        assert self._resolve(mp, "rpg_kp") == "kp"


# ===========================================================================
# 4. Backward-compat synthesis
# ===========================================================================

class TestBackwardCompatSynth:
    def test_deepseek_base_url_gives_deepseek_kind(self):
        from core.model_registry import _kind_from_legacy
        assert _kind_from_legacy({"base_url": "https://api.deepseek.com"}) == "deepseek"

    def test_anthropic_base_url_gives_anthropic_compat_kind(self):
        from core.model_registry import _kind_from_legacy
        assert _kind_from_legacy({"base_url": "https://api.anthropic.com"}) == "anthropic_compat"

    def test_claude_in_url_gives_anthropic_compat(self):
        from core.model_registry import _kind_from_legacy
        assert _kind_from_legacy({"base_url": "https://my-proxy.com/claude/v1"}) == "anthropic_compat"

    def test_localhost_gives_local_kind(self):
        from core.model_registry import _kind_from_legacy
        assert _kind_from_legacy({"base_url": "http://127.0.0.1:8000/v1"}) == "local"

    def test_localhost_name_gives_local_kind(self):
        from core.model_registry import _kind_from_legacy
        assert _kind_from_legacy({"base_url": "http://localhost:11434/v1"}) == "local"

    def test_unknown_url_gives_openai_kind(self):
        from core.model_registry import _kind_from_legacy
        assert _kind_from_legacy({"base_url": "https://api.openai.com"}) == "openai"

    def test_synth_produces_valid_preset_structure(self):
        from core.model_registry import _synth_legacy_presets
        cfg = {
            "llm": {
                "api_key": TEST_API_KEY,
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "tool_call_mode": "function_calling",
                "temperature": 1.0,
                "top_p": 0.9,
                "max_tokens": 4000,
                "frequency_penalty": 0.3,
                "presence_penalty": 0.4,
            }
        }
        mp = _synth_legacy_presets(cfg)
        assert "legacy" in mp["presets"]
        legacy = mp["presets"]["legacy"]
        assert legacy["provider_kind"] == "deepseek"
        assert legacy["api_protocol"] == "chat_completions"
        assert legacy["model"] == "deepseek-chat"
        assert legacy["params"]["temperature"] == 1.0
        assert legacy["params"]["frequency_penalty"] == 0.3

    def test_synth_all_categories_route_to_legacy(self):
        from core.model_registry import _synth_legacy_presets
        cfg = {"llm": {"base_url": "https://api.deepseek.com", "model": "ds"}}
        mp = _synth_legacy_presets(cfg)
        profile = mp["routing_profiles"]["default"]
        for cat in ("chat", "intent", "probe", "summary", "detect_emotion", "consolidation"):
            assert profile[cat] == "legacy", f"category '{cat}' should route to 'legacy'"

    @pytest.mark.asyncio
    async def test_get_model_client_works_with_legacy_config(self, monkeypatch):
        """get_model_client('chat') must succeed when only llm: block is present."""
        import core.model_registry as reg

        fake_cfg = {
            "llm": {
                "api_key": TEST_API_KEY,
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "tool_call_mode": "function_calling",
                "temperature": 1.0,
                "max_tokens": 4000,
            }
        }
        monkeypatch.setattr(reg, "_model_clients", {})
        monkeypatch.setattr("core.model_registry.get_config", lambda: fake_cfg)

        mc = reg.get_model_client("chat")
        assert mc.name == "legacy"
        assert mc.model == "deepseek-chat"
        assert mc.provider_kind == "deepseek"
        assert "temperature" in mc.params
        assert mc.api_protocol == "chat_completions"


class TestApiProtocol:
    def test_explicit_preset_bypasses_routing_and_is_strict(self, monkeypatch):
        import core.model_registry as reg

        cfg = {
            "model_presets": {
                "presets": {
                    "chat-model": {"provider_kind": "openai", "model": "chat", "api_key": TEST_API_KEY},
                    "review-model": {"provider_kind": "openai", "model": "review", "api_key": TEST_API_KEY},
                },
                "routing_profiles": {"default": {"chat": "chat-model"}},
            },
        }
        monkeypatch.setattr(reg, "_model_clients", {})
        monkeypatch.setattr("core.model_registry.get_config", lambda: cfg)

        assert reg.get_model_client("chat", preset_name="review-model").name == "review-model"
        with pytest.raises(ValueError, match="preset 'missing' not found"):
            reg.get_model_client("chat", preset_name="missing")

    def test_explicit_responses_survives_routing_to_final_preset(self, monkeypatch):
        import core.model_registry as reg

        cfg = {
            "model_presets": {
                "active_routing": "default",
                "defaults": {},
                "presets": {
                    "response-preset": {
                        "provider_kind": "openai",
                        "api_protocol": "responses",
                        "api_key": TEST_API_KEY,
                        "model": "test-model",
                    },
                },
                "routing_profiles": {"default": {"chat": "response-preset"}},
            },
        }
        monkeypatch.setattr(reg, "_model_clients", {})
        monkeypatch.setattr("core.model_registry.get_config", lambda: cfg)

        assert reg.get_model_client("chat").api_protocol == "responses"

    def test_invalid_protocol_fails_fast_with_preset_diagnostics(self, monkeypatch):
        import core.model_registry as reg

        cfg = {
            "model_presets": {
                "presets": {"bad": {"provider_kind": "openai", "model": "x", "api_protocol": "guessed"}},
                "routing_profiles": {"default": {"chat": "bad"}},
            },
        }
        monkeypatch.setattr("core.model_registry.get_config", lambda: cfg)
        with pytest.raises(ValueError, match="api_protocol.*preset='bad'.*'guessed'"):
            reg._build_model_client("bad")


# ===========================================================================
# 5. xml transform (Phase 2)
# ===========================================================================

class TestXmlTransform:
    def _msgs(self):
        return [
            {"role": "system", "content": "You are helpful.", "_layer": "1_system_prompt"},
            {"role": "user",   "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "system", "content": "Some context.", "_layer": "5_profile"},
            {"role": "system", "content": "No layer here."},
        ]

    def test_system_layers_wrapped(self):
        from core.prompt_style import apply_prompt_style
        result = apply_prompt_style(self._msgs(), "xml")
        assert result[0]["content"] == "<1_system_prompt>You are helpful.</1_system_prompt>"
        assert result[3]["content"] == "<5_profile>Some context.</5_profile>"

    def test_user_assistant_untouched(self):
        from core.prompt_style import apply_prompt_style
        result = apply_prompt_style(self._msgs(), "xml")
        assert result[1]["content"] == "Hello"
        assert result[2]["content"] == "Hi there"

    def test_no_layer_uses_context_tag(self):
        from core.prompt_style import apply_prompt_style
        result = apply_prompt_style(self._msgs(), "xml")
        assert result[4]["content"] == "<context>No layer here.</context>"

    def test_order_preserved(self):
        from core.prompt_style import apply_prompt_style
        msgs = self._msgs()
        result = apply_prompt_style(msgs, "xml")
        assert len(result) == len(msgs)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"

    def test_tag_name_sanitised(self):
        from core.prompt_style import apply_prompt_style
        msgs = [{"role": "system", "content": "x", "_layer": "layer.with-dashes and spaces"}]
        result = apply_prompt_style(msgs, "xml")
        assert "<layer_with_dashes_and_spaces>" in result[0]["content"]

    def test_narrative_is_noop(self):
        from core.prompt_style import apply_prompt_style
        msgs = self._msgs()
        result = apply_prompt_style(msgs, "narrative")
        assert result == msgs

    def test_unknown_style_is_noop(self):
        from core.prompt_style import apply_prompt_style
        msgs = self._msgs()
        result = apply_prompt_style(msgs, "someunknownstyle")
        assert result == msgs

    def test_internal_layer_key_preserved_in_output(self):
        """_layer must still be present in the dict after xml transform (sanitize strips it later)."""
        from core.prompt_style import apply_prompt_style
        msgs = [{"role": "system", "content": "x", "_layer": "1_system_prompt"}]
        result = apply_prompt_style(msgs, "xml")
        assert result[0].get("_layer") == "1_system_prompt"


# ===========================================================================
# 6. detect_emotion integration with new routing (regression)
# ===========================================================================

def _make_fake_model_client(emotion: str):
    """Build a minimal ModelClient-like object whose client.chat.completions.create returns `emotion`."""
    from core.model_registry import ModelClient

    async def fake_create(**kwargs):
        msg = types.SimpleNamespace(content=emotion)
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice])

    completions = types.SimpleNamespace(create=fake_create)
    chat_obj = types.SimpleNamespace(completions=completions)
    fake_client = types.SimpleNamespace(chat=chat_obj)

    return ModelClient(
        name="test",
        provider_kind="deepseek",
        model="test-model",
        tool_call_mode="function_calling",
        prompt_style="narrative",
        params={"temperature": 0.0, "max_tokens": 10},
        client=fake_client,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("emotion", ["thinking", "sleepy", "happy", "neutral"])
async def test_detect_emotion_routes_through_preset(emotion, monkeypatch):
    from core import llm_client

    fake_mc = _make_fake_model_client(emotion)
    # patch the imported name inside llm_client (not the registry module attribute)
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat: fake_mc)
    assert await llm_client.detect_emotion("some text") == emotion


@pytest.mark.asyncio
async def test_detect_emotion_invalid_falls_back(monkeypatch):
    from core import llm_client

    fake_mc = _make_fake_model_client("confused")
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat: fake_mc)
    assert await llm_client.detect_emotion("some text") == "neutral"


# ===========================================================================
# 7. sensor_judge routing: must use intent preset model, not legacy llm block
# ===========================================================================

@pytest.mark.asyncio
async def test_sensor_judge_uses_sensor_judge_preset_model(monkeypatch):
    """Sensor judge uses its dedicated routing category, not generic intent."""
    import core.scheduler.sensor_judge as sj
    from core.model_registry import ModelClient

    captured: dict = {}

    async def capturing_create(**kwargs):
        captured["model"] = kwargs.get("model")
        captured["category"] = captured.get("_last_cat")
        msg = types.SimpleNamespace(content='{"score": 55, "reason": "测试"}')
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice])

    completions = types.SimpleNamespace(create=capturing_create)
    chat_obj = types.SimpleNamespace(completions=completions)
    fake_client_obj = types.SimpleNamespace(chat=chat_obj)

    fake_mc = ModelClient(
        name="intent-preset",
        provider_kind="deepseek",
        model="model-B",
        tool_call_mode="function_calling",
        prompt_style="narrative",
        params={},
        client=fake_client_obj,
    )

    seen_categories: list = []

    def fake_get_model_client(cat: str) -> ModelClient:
        seen_categories.append(cat)
        return fake_mc

    monkeypatch.setattr(sj, "get_model_client", fake_get_model_client)

    event = {
        "type": "TEST",
        "narrative": "test narrative",
        "context": {"local_hour": 14, "presence": "active"},
    }
    result = await sj.judge(event)

    assert seen_categories == ["sensor_judge"], (
        f"sensor_judge must call get_model_client('sensor_judge'), got {seen_categories}"
    )
    assert captured["model"] == "model-B", (
        f"LLM call must use intent preset model 'model-B', got {captured['model']!r}"
    )
    assert result["score"] == 55
