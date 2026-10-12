from __future__ import annotations

import pytest


def test_sticker_emotion_uses_local_fallback_when_classifier_is_neutral():
    from core.output.sticker import _resolve_sticker_emotion

    assert _resolve_sticker_emotion("太好了，见到你我真的很开心。", "neutral") == "happy"
    assert _resolve_sticker_emotion("别难过，我会陪着你。", "neutral") == "gentle"
    assert _resolve_sticker_emotion("普通地说一句晚安。", "neutral") == "calm"
    assert _resolve_sticker_emotion("普通文本", "sad") == "sad"


@pytest.mark.asyncio
async def test_sticker_keeps_qq_send_and_broadcasts_self_contained_payload(tmp_path, monkeypatch):
    from core.output import sticker

    image = tmp_path / "sticker.png"
    image.write_bytes(b"png-bytes")
    # Delivery contract tests opt in explicitly; they must not depend on a
    # developer's local sticker feature-switch setting.
    monkeypatch.setattr(sticker, "get_config", lambda: {"sticker": {"enabled": True, "trigger_prob": 1.0}})
    monkeypatch.setattr(sticker, "_pick_sticker", lambda emotion, char_id=None: str(image))
    monkeypatch.setattr(sticker.random, "random", lambda: 0.0)

    qq_calls = []

    async def _send_image(target_id, path, is_group):
        qq_calls.append((target_id, path, is_group))

    monkeypatch.setattr("core.qq_adapter.send_image", _send_image)

    broadcasts = []

    async def _broadcast(content, user_id, **kwargs):
        broadcasts.append((content, user_id, kwargs))
        return {}

    monkeypatch.setattr("channels.registry.broadcast", _broadcast)

    await sticker.maybe_send_sticker("reply", "owner-1", emotion="happy")

    assert qq_calls == [("owner-1", str(image), False)]
    assert len(broadcasts) == 1
    content, user_id, kwargs = broadcasts[0]
    assert (content, user_id) == ("", "owner-1")
    assert kwargs["exclude_channels"] == {"qq"}
    payload = kwargs["sticker"]
    assert payload["kind"] == "sticker"
    assert payload["emotion"] == "开心"
    assert payload["data_url"] == "data:image/png;base64,cG5nLWJ5dGVz"
    assert str(image) not in payload["data_url"]


@pytest.mark.asyncio
async def test_sticker_broadcasts_without_qq_target_for_desktop_reply(tmp_path, monkeypatch):
    """Desktop/mobile replies have no QQ id but must still receive a sticker."""
    from core.output import sticker

    image = tmp_path / "sticker.png"
    image.write_bytes(b"png-bytes")
    monkeypatch.setattr(sticker, "get_config", lambda: {"sticker": {"enabled": True, "trigger_prob": 1.0}})
    monkeypatch.setattr(sticker, "_pick_sticker", lambda emotion, char_id=None: str(image))
    monkeypatch.setattr(sticker.random, "random", lambda: 0.0)
    monkeypatch.setattr(
        "core.qq_adapter.send_image",
        lambda *_args, **_kwargs: pytest.fail("desktop reply must not try QQ delivery"),
    )

    broadcasts = []

    async def _broadcast(content, user_id, **kwargs):
        broadcasts.append((content, user_id, kwargs))
        return {}

    monkeypatch.setattr("channels.registry.broadcast", _broadcast)
    await sticker.maybe_send_sticker(
        "reply", None, emotion="happy", recipient_id="desktop-owner",
    )

    assert len(broadcasts) == 1
    content, user_id, kwargs = broadcasts[0]
    assert (content, user_id) == ("", "desktop-owner")
    assert kwargs["sticker"]["kind"] == "sticker"


@pytest.mark.asyncio
async def test_pipeline_schedules_sticker_without_qq_target(sandbox, monkeypatch):
    """Regression: an empty QQ target must not suppress desktop sticker delivery."""
    import asyncio
    from unittest.mock import AsyncMock

    from core.pipeline import Pipeline
    from core.write_envelope import stamp_user_chat

    class _Character:
        name = "Companion"

    calls = []

    async def _sticker(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("core.llm_client.detect_emotion", AsyncMock(return_value="happy"))
    monkeypatch.setattr("core.llm_client.chat", AsyncMock(return_value=""))
    monkeypatch.setattr("core.output.sticker.maybe_send_sticker", _sticker)
    monkeypatch.setattr("core.memory.mood_state.update", lambda *_args, **_kwargs: None)

    pipeline = Pipeline(_Character(), lore_engine=None)
    await pipeline.post_process(
        "desktop-owner", "你好", "<feel>很高兴</feel><say>见到你真好。</say>", target_id="", envelope=stamp_user_chat(),
    )
    await asyncio.sleep(0)

    assert calls
    args, kwargs = calls[0]
    assert args[1] is None
    assert kwargs["recipient_id"] == "desktop-owner"


@pytest.mark.asyncio
async def test_sticker_total_switch_prevents_all_side_effects(monkeypatch):
    from core.output import sticker

    monkeypatch.setattr(sticker, "get_config", lambda: {"sticker": {"enabled": False, "trigger_prob": 1.0}})
    monkeypatch.setattr(sticker, "_pick_sticker", lambda emotion: pytest.fail("disabled sticker must not select an image"))

    await sticker.maybe_send_sticker("reply", "owner-1", emotion="happy")


@pytest.mark.asyncio
async def test_sticker_zero_probability_never_sends(monkeypatch):
    from core.output import sticker

    monkeypatch.setattr(sticker, "get_config", lambda: {"sticker": {"enabled": True, "trigger_prob": 0.0}})
    monkeypatch.setattr(sticker.random, "random", lambda: 0.0)
    monkeypatch.setattr(sticker, "_pick_sticker", lambda emotion: pytest.fail("zero probability must not select an image"))

    await sticker.maybe_send_sticker("reply", "owner-1", emotion="happy")


@pytest.mark.asyncio
async def test_sticker_logs_selected_folder_when_probability_hits_without_image(monkeypatch, caplog):
    from core.output import sticker

    monkeypatch.setattr(sticker, "get_config", lambda: {"sticker": {"enabled": True, "trigger_prob": 1.0}})
    monkeypatch.setattr(sticker.random, "random", lambda: 0.0)
    monkeypatch.setattr(sticker, "_pick_sticker", lambda emotion, char_id=None: None)

    with caplog.at_level("WARNING", logger="core.output.sticker"):
        await sticker.maybe_send_sticker("reply", "owner-1", emotion="happy")

    assert "[sticker] 目录无可用图片" in caplog.text


@pytest.mark.asyncio
async def test_sticker_payload_reaches_desktop_ws_and_mobile_queue(sandbox, monkeypatch):
    from channels import desktop_ws
    from channels.mobile import MobileChannel

    payload = {"kind": "sticker", "emotion": "开心", "data_url": "data:image/png;base64,AA=="}
    sent = []

    async def _send_json(frame):
        sent.append(frame)
        return True

    monkeypatch.setattr(desktop_ws, "_send_json", _send_json)
    await desktop_ws.push_message("", msg_id="sticker-1", sticker=payload)
    assert sent == [{
        "type": "channel_message", "content": "", "msg_id": "sticker-1",
        "source": "reality", "sticker": payload,
    }]

    await MobileChannel().send("", "owner", msg_id="sticker-1", sticker=payload)
    import json
    queued = json.loads(sandbox.mobile_queue().read_text(encoding="utf-8"))
    assert queued[0]["sticker"] == payload
