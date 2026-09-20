import asyncio
from types import SimpleNamespace

import pytest


def test_sensor_judge_routing_falls_back_to_intent_then_chat(monkeypatch):
    from core import model_registry

    monkeypatch.setattr(model_registry, "_active_char_model_routing", lambda: None)
    monkeypatch.setattr(model_registry, "_get_preset_config", lambda: {
        "active_routing": "default",
        "presets": {"chat-model": {}, "intent-model": {}},
        "routing_profiles": {"default": {"chat": "chat-model", "intent": "intent-model"}},
    })
    assert model_registry._resolve_preset_name("sensor_judge") == "intent-model"

    monkeypatch.setattr(model_registry, "_get_preset_config", lambda: {
        "active_routing": "default",
        "presets": {"chat-model": {}},
        "routing_profiles": {"default": {"chat": "chat-model"}},
    })
    assert model_registry._resolve_preset_name("sensor_judge") == "chat-model"


@pytest.mark.asyncio
async def test_sensor_judge_failure_is_ledgered_and_fail_closed(monkeypatch):
    from core.scheduler import sensor_judge as sj

    sj._BREAKERS.clear()
    calls = []
    mc = SimpleNamespace(
        name="sensor",
        provider_kind="openai",
        model="small",
        api_protocol="chat_completions",
        prompt_style="narrative",
        request_timeout_s=0.01,
    )
    monkeypatch.setattr(sj, "get_model_client", lambda category: mc)

    async def fail(*args, **kwargs):
        error = RuntimeError("Upstream access forbidden")
        error.status_code = 502
        raise error

    monkeypatch.setattr("core.llm_protocol.create", fail)
    monkeypatch.setattr("core.api_call_log.append", lambda **row: calls.append(row))
    result = await sj.judge({"type": "test", "narrative": "n", "context": {}})
    assert result["intent_tier"] == "drop"
    assert calls[0]["caller"] == "sensor_judge"
    assert calls[0]["ok"] is False
    assert calls[0]["error_category"] == "upstream_unavailable"
    assert "prompt" not in calls[0]


@pytest.mark.asyncio
async def test_sensor_judge_breaker_opens_and_half_open_success_resets(monkeypatch):
    from core.scheduler import sensor_judge as sj

    sj._BREAKERS.clear()
    key = ("sensor", "sensor_judge")
    now = 100.0
    for _ in range(sj._BREAKER_THRESHOLD):
        sj._breaker_record(key, "upstream_5xx", ok=False, now=now)
    assert not sj._breaker_permits(key, now + 1)
    assert sj._breaker_permits(key, now + sj._BREAKER_COOLDOWN_S + 1)
    sj._breaker_record(key, "", ok=True, now=now + sj._BREAKER_COOLDOWN_S + 1)
    assert sj._breaker_permits(key, now + sj._BREAKER_COOLDOWN_S + 2)
