"""tests/test_stage_pseudo_stream.py — Brief 84 §2: Stage deliver() pseudo-stream wiring.

core.stage.runtime.run_reality_stage_turn()'s deliver() now:
1. fans a pseudo-stream typewriter replay (channels.ui_push.pseudo_stream_push) before
   the canonical push, sharing one msg_id between the two so desktop/device can
   correlate the replacement bubble against the streamed one;
2. still sends the canonical channel_message to desktop_ws and non-desktop channels
   exactly as before (regression coverage for the pre-existing contract);
3. shares the same msg_id with the "device" channel specifically (its firmware
   finalizes a stream envelope by matching msg_id — see firmware/ws_client.cpp),
   while other non-desktop channels (mobile/QQ) keep their own auto msg_id, matching
   pre-Brief-84 behavior since they never receive stream frames.
4. never lets a pseudo-stream failure block the canonical delivery (fail-open).
"""
from __future__ import annotations

import pytest


def _settings():
    from core.stage.models import StageSettings

    return StageSettings(min_responders=1, max_responders=1)


async def _record_async(bucket, value):
    bucket.append(value)


@pytest.mark.asyncio
async def test_deliver_shares_msg_id_between_pseudo_stream_and_canonical_push(
    sandbox, monkeypatch
):
    from core.stage.models import Stage
    from core.stage.runner import StageTurnResult
    from core.stage.runtime import run_reality_stage_turn

    stage = Stage("runtime-ps-1", "actual-owner", ("yexuan",), settings=_settings())

    async def fake_run(group_id, owner_content, *, generate_reply, deliver_reply, turn_id):
        await deliver_reply("yexuan", "今天天气不错，我们去散步吧！", "t")
        return StageTurnResult(group_id, "t", (), 0)

    monkeypatch.setattr("core.stage.runtime.load_stage", lambda group_id: stage)
    monkeypatch.setattr("core.stage.runtime.run_owner_turn", fake_run)
    monkeypatch.setattr(
        "core.stage.runtime.enqueue_reality_projection",
        lambda group_id: _record_async([], group_id),
    )

    ws_sent = []

    async def fake_send_json(payload):
        ws_sent.append(payload)
        return True

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr("channels.desktop_ws._send_json", fake_send_json)
    monkeypatch.setattr("channels.desktop_ws._current_ws", object())
    monkeypatch.setattr("channels.ui_push.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("channels.registry.get_active", lambda: [])

    await run_reality_stage_turn("runtime-ps-1", "hello")

    types = [f["type"] for f in ws_sent]
    assert "message_stream_start" in types
    assert "message_stream_end" in types
    assert "channel_message" in types

    canonical = next(f for f in ws_sent if f["type"] == "channel_message")
    stream_start = next(f for f in ws_sent if f["type"] == "message_stream_start")
    stream_end = next(f for f in ws_sent if f["type"] == "message_stream_end")

    # All three frames belong to the same replay: shared msg_id, and the
    # pseudo-stream frames precede the canonical replacement.
    assert stream_start["msg_id"] == canonical["msg_id"] == stream_end["msg_id"]
    assert ws_sent.index(stream_end) < ws_sent.index(canonical)
    assert stream_start["char_id"] == "yexuan"
    assert canonical["char_id"] == "yexuan"


@pytest.mark.asyncio
async def test_deliver_device_channel_shares_msg_id_with_stream_frames(sandbox, monkeypatch):
    """device 通道走 firmware 的 msg_id 匹配逻辑：canonical 必须和 stream 帧同一个 msg_id。"""
    from core.stage.models import Stage
    from core.stage.runner import StageTurnResult
    from core.stage.runtime import run_reality_stage_turn

    stage = Stage("runtime-ps-2", "actual-owner", ("yexuan",), settings=_settings())

    async def fake_run(group_id, owner_content, *, generate_reply, deliver_reply, turn_id):
        await deliver_reply("yexuan", "今天天气不错，我们去散步吧！", "t")
        return StageTurnResult(group_id, "t", (), 0)

    monkeypatch.setattr("core.stage.runtime.load_stage", lambda group_id: stage)
    monkeypatch.setattr("core.stage.runtime.run_owner_turn", fake_run)
    monkeypatch.setattr(
        "core.stage.runtime.enqueue_reality_projection",
        lambda group_id: _record_async([], group_id),
    )
    # desktop stays disconnected — only device is "connected" for this test.
    monkeypatch.setattr("channels.desktop_ws._current_ws", None)
    monkeypatch.setattr("channels.device_ws._current_ws", object())

    device_stream_frames = []

    def fake_enqueue(payload):
        device_stream_frames.append(payload)
        return True

    monkeypatch.setattr("channels.device_ws.enqueue_json", fake_enqueue)

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr("channels.ui_push.asyncio.sleep", fake_sleep)

    device_send_calls = []

    class FakeDeviceChannel:
        name = "device"
        is_active = True

        async def send(self, content, uid, behavior=None, msg_id=None, *, char_id=None):
            device_send_calls.append((content, uid, char_id, msg_id))

    monkeypatch.setattr("channels.registry.get_active", lambda: [FakeDeviceChannel()])

    await run_reality_stage_turn("runtime-ps-2", "hello")

    assert device_send_calls, "device channel should have received the canonical push"
    _content, _uid, _char_id, canonical_msg_id = device_send_calls[0]
    assert canonical_msg_id is not None

    stream_msg_ids = {
        f["msg_id"] for f in device_stream_frames if f["type"].startswith("message_stream")
    }
    assert stream_msg_ids == {canonical_msg_id}, (
        "device pseudo-stream frames and canonical push must share one msg_id "
        "so firmware ws_client.cpp can finalize the streamed bubble"
    )


@pytest.mark.asyncio
async def test_deliver_mobile_channel_unaffected_no_msg_id_passed(sandbox, monkeypatch):
    """非 desktop/device 通道（mobile/QQ）保持 Brief 84 之前的调用签名：不传 msg_id。"""
    from core.stage.models import Stage
    from core.stage.runner import StageTurnResult
    from core.stage.runtime import run_reality_stage_turn

    stage = Stage("runtime-ps-3", "actual-owner", ("yexuan",), settings=_settings())

    async def fake_run(group_id, owner_content, *, generate_reply, deliver_reply, turn_id):
        await deliver_reply("yexuan", "reply", "t")
        return StageTurnResult(group_id, "t", (), 0)

    monkeypatch.setattr("core.stage.runtime.load_stage", lambda group_id: stage)
    monkeypatch.setattr("core.stage.runtime.run_owner_turn", fake_run)
    monkeypatch.setattr(
        "core.stage.runtime.enqueue_reality_projection",
        lambda group_id: _record_async([], group_id),
    )
    monkeypatch.setattr("channels.desktop_ws._current_ws", None)
    monkeypatch.setattr("channels.device_ws._current_ws", None)

    non_desktop_sent = []

    class FakeMobileChannel:
        name = "mobile"
        is_active = True

        async def send(self, content, uid, behavior=None, *, char_id=None, **kw):
            non_desktop_sent.append((content, uid, char_id, kw))

    monkeypatch.setattr("channels.registry.get_active", lambda: [FakeMobileChannel()])

    await run_reality_stage_turn("runtime-ps-3", "hello")

    assert non_desktop_sent == [("reply", "actual-owner", "yexuan", {})]


@pytest.mark.asyncio
async def test_deliver_pseudo_stream_failure_does_not_block_canonical_push(
    sandbox, monkeypatch
):
    from core.stage.models import Stage
    from core.stage.runner import StageTurnResult
    from core.stage.runtime import run_reality_stage_turn

    stage = Stage("runtime-ps-4", "actual-owner", ("yexuan",), settings=_settings())

    async def fake_run(group_id, owner_content, *, generate_reply, deliver_reply, turn_id):
        await deliver_reply("yexuan", "reply text", "t")
        return StageTurnResult(group_id, "t", (), 0)

    monkeypatch.setattr("core.stage.runtime.load_stage", lambda group_id: stage)
    monkeypatch.setattr("core.stage.runtime.run_owner_turn", fake_run)
    monkeypatch.setattr(
        "core.stage.runtime.enqueue_reality_projection",
        lambda group_id: _record_async([], group_id),
    )

    ws_sent = []

    async def fake_send_json(payload):
        ws_sent.append(payload)
        return True

    monkeypatch.setattr("channels.desktop_ws._send_json", fake_send_json)
    monkeypatch.setattr("channels.desktop_ws._current_ws", object())
    monkeypatch.setattr("channels.registry.get_active", lambda: [])

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated pseudo-stream failure")

    monkeypatch.setattr("channels.ui_push.pseudo_stream_push", boom)

    # Must not raise, and the canonical push must still land.
    await run_reality_stage_turn("runtime-ps-4", "hello")

    types = [f["type"] for f in ws_sent]
    assert types == ["group_round_start", "channel_message", "group_round_end"]
