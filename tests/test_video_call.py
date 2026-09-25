import io
import asyncio
import time
from types import SimpleNamespace
import httpx

import pytest
from PIL import Image
from openai import APIConnectionError, APITimeoutError

from core import video_call
from core.image_presets import video_call_ready


def _config(base_url="http://127.0.0.1:11434/v1"):
    return {"image_presets": {
        "presets": {"local_vision": {
            "kind": "vision", "enabled": True, "provider": "custom",
            "api_protocol": "chat_completions", "model": "vendor/model:tag",
            "base_url": base_url,
        }},
        "routes": {"video_call": "local_vision"},
    }}


def _jpeg():
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), (30, 60, 90)).save(stream, format="JPEG")
    return stream.getvalue()


def test_video_call_requires_loopback_vision():
    local = _config()["image_presets"]["presets"]["local_vision"]
    assert video_call_ready(local) == (True, "")
    for address in ("https://127.0.0.1:11434/v1", "http://192.168.1.5:11434/v1", "https://api.example/v1"):
        assert video_call_ready({**local, "base_url": address})[0] is False
    assert video_call_ready({**local, "kind": "ocr"})[0] is False


def test_camera_tool_uses_video_call_route_only(monkeypatch):
    from core import tool_dispatcher
    monkeypatch.setattr(tool_dispatcher, "get_config", _config)
    assert tool_dispatcher._is_tool_enabled("observe_video_call_camera")
    monkeypatch.setattr(tool_dispatcher, "get_config", lambda: _config("https://remote.example/v1"))
    assert not tool_dispatcher._is_tool_enabled("observe_video_call_camera")
    assert "observe_video_call_camera" in tool_dispatcher._TOOL_REGISTRY
    assert "observe_user_screen" in tool_dispatcher._TOOL_REGISTRY


def test_closed_camera_blocks_merged_autonomy_talk(monkeypatch):
    from core.autonomy import runner
    monkeypatch.setattr(video_call, "camera_session", lambda uid, char_id: None)
    job = SimpleNamespace(uid="owner", char_id="character", opportunity={"signals": [
        {"source": "video_call_camera"}, {"source": "interval"},
    ]})
    assert runner._camera_signal_closed(job)
    job.opportunity = {"signals": [{"source": "interval"}]}
    assert not runner._camera_signal_closed(job)


def test_camera_signal_uses_interval_even_when_scene_is_unchanged(monkeypatch):
    from core.autonomy import store
    queued = []
    monkeypatch.setattr(store, "enqueue_signal", lambda uid, char_id, signal, **kwargs: (
        queued.append((uid, char_id, signal, kwargs)) or True, "queued"
    ))
    session = {"opened_at": 100.0}
    video_call._queue_camera_signal("owner", "character", session, "书桌", 130.0)
    assert queued == []
    video_call._queue_camera_signal("owner", "character", session, "书桌", 161.0)
    assert len(queued) == 1
    assert queued[0][2].source == "video_call_camera"
    assert queued[0][2].evidence[0]["description"] == "书桌"
    assert queued[0][2].expiry - queued[0][2].created_at > 9 * 60
    video_call._queue_camera_signal("owner", "character", session, "书桌", 200.0)
    video_call._queue_camera_signal("owner", "character", session, "书桌", 201.0)
    assert len(queued) == 1
    video_call._queue_camera_signal("owner", "character", session, "书桌", 162.0 + video_call.CAMERA_SIGNAL_INTERVAL_SECONDS)
    assert len(queued) == 2


@pytest.mark.asyncio
async def test_camera_tool_requests_fresh_frame_on_video_call_route(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", _config)
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)
    async def fake_chat(messages, **kwargs):
        assert kwargs["vision_purpose"] == "video_call"
        return "窗边有一本书"
    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    video_call._camera_sessions[("owner-tool", "character-tool")] = {
        "seen_at": time.monotonic(), "opened_at": time.monotonic(),
        "token_label": "desktop", "description": "旧画面",
    }
    task = asyncio.create_task(video_call.observe_fresh_camera("owner-tool", "character-tool"))
    await asyncio.sleep(0)
    request = video_call.poll_camera("owner-tool", "character-tool", "desktop")["request"]
    assert request and request["request_id"]
    assert video_call.poll_camera("owner-tool", "character-tool", "desktop")["request"] is None
    assert not video_call.accept_camera_frame("owner-tool", "character-tool", "other", request["request_id"], _jpeg())
    assert video_call.accept_camera_frame("owner-tool", "character-tool", "desktop", request["request_id"], _jpeg())
    assert not video_call.accept_camera_frame("owner-tool", "character-tool", "desktop", request["request_id"], _jpeg())
    assert await task == {"status": "ok", "description": "窗边有一本书"}
    video_call.close_camera("owner-tool", "character-tool", "desktop")


@pytest.mark.asyncio
async def test_camera_observation_is_ephemeral_scoped_and_busy_drops(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", _config)
    async def fake_chat(messages, **kwargs):
        assert kwargs == {"use_vision": True, "vision_purpose": "video_call"}
        assert messages[0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return "桌面上有一本书"
    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    result = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert video_call.camera_session("owner", "character")["description"] == "桌面上有一本书"
    assert video_call.camera_status()["active_sessions"] >= 1
    assert result["status"] == "ready"
    receipt = result["observation_id"]
    assert video_call.consume(receipt, uid="owner", char_id="wrong", token_label="desktop") is None
    assert video_call.consume(receipt, uid="owner", char_id="character", token_label="desktop") == "桌面上有一本书"
    assert video_call.consume(receipt, uid="owner", char_id="character", token_label="desktop") is None
    video_call.close_camera("owner", "character", "other-device")
    assert video_call.camera_session("owner", "character") is not None
    video_call.close_camera("owner", "character", "desktop")
    assert video_call.camera_session("owner", "character") is None
    result = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert video_call.consume(result["observation_id"], uid="owner", char_id="character", token_label="desktop") == "桌面上有一本书"
    async with video_call.local_resource():
        assert (await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop"))["status"] == "busy"


@pytest.mark.asyncio
async def test_camera_observation_rejects_remote_route_and_oversized_frame(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", lambda: _config("https://remote.example/v1"))
    with pytest.raises(ValueError, match="loopback_http_required"):
        await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    monkeypatch.setattr("core.config_loader.get_config", _config)
    with pytest.raises(ValueError, match="frame_size_invalid"):
        await video_call.observe(b"x" * (video_call.MAX_FRAME_BYTES + 1), uid="owner", char_id="character", token_label="desktop")


@pytest.mark.asyncio
async def test_unavailable_local_model_skips_following_frames_without_queue(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", _config)
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)
    calls = []

    async def disconnected(*_args, **_kwargs):
        calls.append(True)
        raise APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions"))

    monkeypatch.setattr("core.llm_client.chat", disconnected)
    first = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    second = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert first["status"] == second["status"] == "unavailable"
    assert len(calls) == 1
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)

    async def recovered(*_args, **_kwargs):
        return "恢复后的当前画面"

    monkeypatch.setattr("core.llm_client.chat", recovered)
    recovered_frame = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert recovered_frame["status"] == "ready"


@pytest.mark.asyncio
async def test_model_timeout_is_distinct_from_disconnection(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", _config)
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)

    async def timed_out(*_args, **_kwargs):
        raise APITimeoutError(request=httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions"))

    monkeypatch.setattr("core.llm_client.chat", timed_out)
    result = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert result["status"] == "timeout"
