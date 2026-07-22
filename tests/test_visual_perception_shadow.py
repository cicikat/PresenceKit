import asyncio
import json

import pytest


def _config(enabled=True):
    return {"visual_perception": {"enabled": enabled, "base_url": "http://vlm", "model": "local", "timeout_s": 1}}


@pytest.mark.asyncio
async def test_disabled_endpoint_does_not_start_processing(sandbox, monkeypatch):
    import admin.routers.perception as router
    monkeypatch.setattr("core.config_loader.get_config", lambda: _config(False))
    called = False
    def create_task(_coro):
        nonlocal called
        called = True
        _coro.close()
    monkeypatch.setattr(router.asyncio, "create_task", create_task)
    class Upload:
        async def read(self): return b"image"
    result = await router.ingest_visual(Upload(), "screen", True)
    assert result["processing"] is False and called is False
    assert not sandbox.visual_trace_log().exists()


@pytest.mark.asyncio
async def test_valid_sensitive_and_invalid_shadow_rows(sandbox, monkeypatch):
    import admin.routers.perception as router
    from core.perception.vlm_client import VisualObservation
    async def valid(*_): return VisualObservation("desk", "working", .8, False, "在桌前工作"), None
    monkeypatch.setattr("core.perception.vlm_client.describe_with_status", valid)
    await router.process_visual_image(b"x", "screen")
    async def sensitive(*_): return VisualObservation("other", "unknown", .9, True, "绝不能写入"), None
    monkeypatch.setattr("core.perception.vlm_client.describe_with_status", sensitive)
    await router.process_visual_image(b"x", "camera")
    async def invalid(*_): return None, "invalid"
    monkeypatch.setattr("core.perception.vlm_client.describe_with_status", invalid)
    await router.process_visual_image(b"x", "screen")
    rows = [json.loads(line) for line in sandbox.visual_trace_log().read_text(encoding="utf-8").splitlines()]
    assert rows[0]["caption"] == "在桌前工作"
    assert rows[1] == {"ts": rows[1]["ts"], "source": "camera", "dropped": "sensitive"}
    assert rows[2]["dropped"] == "invalid" and "caption" not in rows[2]


def test_parse_rejects_bad_enums_and_captions():
    from core.perception.vlm_client import _parse_observation
    assert _parse_observation({"scene": "bad", "activity": "working", "confidence": .5, "sensitive": False, "caption": "x"}) is None
    assert _parse_observation({"scene": "desk", "activity": "working", "confidence": .5, "sensitive": False, "caption": "x" * 31}) is None


def test_enabled_shadow_observation_reuses_configured_vision_credentials(monkeypatch):
    from core.perception.vlm_client import get_visual_perception_config

    monkeypatch.setattr("core.config_loader.get_config", lambda: {
        "vision": {"enabled": True, "provider": "glm", "base_url": "https://glm", "model": "glm-4v-flash", "api_key": "secret"},
        "visual_perception": {"enabled": True, "base_url": "", "model": "", "api_key": ""},
    })
    cfg = get_visual_perception_config()
    assert cfg["provider"] == "glm"
    assert cfg["base_url"] == "https://glm"
    assert cfg["model"] == "glm-4v-flash"


@pytest.mark.asyncio
async def test_producer_preflight_returns_only_gate_and_cooldown(monkeypatch):
    import admin.routers.perception as router

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"visual_perception": {"enabled": True, "api_key": "must-not-leak"}},
    )
    result = await router.get_visual_producer_config(True)

    assert result == {"enabled": True, "cooldown_seconds": 300}
