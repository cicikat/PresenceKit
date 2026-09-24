import io

import pytest
from PIL import Image

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


@pytest.mark.asyncio
async def test_camera_observation_is_ephemeral_scoped_and_busy_drops(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", _config)
    async def fake_chat(messages, **kwargs):
        assert kwargs == {"use_vision": True, "vision_purpose": "video_call"}
        assert messages[0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return "桌面上有一本书"
    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    result = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert result["status"] == "ready"
    receipt = result["observation_id"]
    assert video_call.consume(receipt, uid="owner", char_id="wrong", token_label="desktop") is None
    assert video_call.consume(receipt, uid="owner", char_id="character", token_label="desktop") == "桌面上有一本书"
    assert video_call.consume(receipt, uid="owner", char_id="character", token_label="desktop") is None
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
