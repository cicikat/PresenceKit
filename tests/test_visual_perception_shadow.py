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
    from core.perception.vlm_client import MAX_CAPTION_CHARS, _parse_observation
    assert _parse_observation({"scene": "bad", "activity": "working", "confidence": .5, "sensitive": False, "caption": "x"}) is None
    # 30 字上限放宽到 120，且过长改为截断而非判废——判废会逼模型写得极短。
    long_caption = "x" * (MAX_CAPTION_CHARS + 20)
    parsed = _parse_observation({"scene": "desk", "activity": "working", "confidence": .5, "sensitive": False, "caption": long_caption})
    assert parsed is not None and len(parsed.caption) == MAX_CAPTION_CHARS + 1 and parsed.caption.endswith("…")
    mid = _parse_observation({"scene": "desk", "activity": "working", "confidence": .5, "sensitive": False, "caption": "x" * 80})
    assert mid is not None and mid.caption == "x" * 80


def test_screen_observation_result_is_natural_chinese():
    from core.perception.screen_observation import _describe_in_chinese
    from core.perception.vlm_client import VisualObservation
    text = _describe_in_chinese(VisualObservation("desk", "working", .8, False, "屏幕上开着表格和聊天窗口"))
    assert text == "在桌前，在工作。屏幕上开着表格和聊天窗口"
    assert "desk" not in text and "working" not in text
    low = _describe_in_chinese(VisualObservation("other", "unknown", .2, False, "画面偏暗"))
    assert low == "画面偏暗（画面不太确定）"


def test_screen_observation_projection_drops_engineering_fields():
    import json as _json
    from core.context_continuity import _tool_result_projection
    row = {"ts": 0, "tool": "observe_user_screen", "device": "desktop", "talk_sent": None,
           "content": _json.dumps({"status": "ok", "device": "desktop", "观察": "在桌前，在工作。开着表格"}, ensure_ascii=False)}
    text = _tool_result_projection(row, "他")
    assert "在桌前，在工作。开着表格" in text
    assert "status" not in text and "observe_user_screen" not in text and "instruction" not in text


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


@pytest.mark.asyncio
async def test_vlm_describe_failure_logs_exception_type_not_body(monkeypatch, caplog):
    import builtins
    import sys

    from core.perception import vlm_client

    monkeypatch.setattr(
        "core.perception.vlm_client.get_visual_perception_config",
        lambda: {"enabled": True, "base_url": "http://vlm", "model": "local", "timeout_s": 1, "provider": "local"},
    )
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "aiohttp":
            raise RuntimeError("secret window title Payment-1234")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    sys.modules.pop("aiohttp", None)
    caplog.set_level("WARNING", logger="core.perception.vlm_client")
    observation, reason = await vlm_client.describe_with_status(b"image")
    assert observation is None and reason == "error"
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in text
    assert "Payment-1234" not in text
    assert "secret window title" not in text
