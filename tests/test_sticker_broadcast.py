from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_sticker_keeps_qq_send_and_broadcasts_self_contained_payload(tmp_path, monkeypatch):
    from core.output import sticker

    image = tmp_path / "sticker.png"
    image.write_bytes(b"png-bytes")
    monkeypatch.setattr(sticker, "_pick_sticker", lambda emotion: str(image))
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
    monkeypatch.setattr(sticker, "_pick_sticker", lambda emotion: None)

    with caplog.at_level("INFO", logger="core.output.sticker"):
        await sticker.maybe_send_sticker("reply", "owner-1", emotion="happy")

    assert "[sticker] 目录无可用图片:" in caplog.text


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
