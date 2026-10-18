from tests.fixtures.public_assets import TEST_THIRD_CHAR_ID
import json

import pytest


pytestmark = pytest.mark.usefixtures("enabled_client_channels")


async def test_desktop_ws_envelopes_include_optional_char_id(monkeypatch):
    from channels import desktop_ws

    sent = []

    async def fake_send(payload):
        sent.append(payload)
        return True

    monkeypatch.setattr(desktop_ws, "_send_json", fake_send)

    await desktop_ws.push_message("hello", msg_id="turn-1", char_id=TEST_THIRD_CHAR_ID)
    await desktop_ws.push_segments(
        "hello",
        [{"type": "say", "text": "hello"}],
        msg_id="turn-1",
        char_id=TEST_THIRD_CHAR_ID,
    )

    assert [item["char_id"] for item in sent] == [TEST_THIRD_CHAR_ID, TEST_THIRD_CHAR_ID]


async def test_desktop_ws_envelopes_omit_char_id_when_unspecified(monkeypatch):
    from channels import desktop_ws

    sent = []

    async def fake_send(payload):
        sent.append(payload)
        return True

    monkeypatch.setattr(desktop_ws, "_send_json", fake_send)

    await desktop_ws.push_message("hello", msg_id="turn-1")
    await desktop_ws.push_segments("hello", [], msg_id="turn-1")

    assert all("char_id" not in item for item in sent)


async def test_mobile_queue_includes_optional_char_id(sandbox):
    from channels.mobile import MobileChannel

    await MobileChannel().send("hello", "owner", msg_id="turn-1", char_id=TEST_THIRD_CHAR_ID)

    queue = json.loads(sandbox.mobile_queue().read_text(encoding="utf-8"))
    assert queue[0]["char_id"] == TEST_THIRD_CHAR_ID


async def test_desktop_fallback_queue_includes_optional_char_id(sandbox, monkeypatch):
    from channels.desktop import DesktopChannel

    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: False)
    await DesktopChannel().send("hello", "owner", char_id=TEST_THIRD_CHAR_ID)

    queue = json.loads(sandbox.channel_queue().read_text(encoding="utf-8"))
    assert queue[0]["char_id"] == TEST_THIRD_CHAR_ID


async def test_registry_broadcast_passes_optional_char_id():
    from channels import registry

    class Channel:
        name = "test"
        is_active = True

        def __init__(self):
            self.char_id = None

        async def send(self, content, user_id, behavior=None, *, char_id=None):
            self.char_id = char_id

    registry._channels = {}
    channel = Channel()
    registry.register(channel)

    await registry.broadcast("hello", "owner", char_id=TEST_THIRD_CHAR_ID)

    assert channel.char_id == TEST_THIRD_CHAR_ID


async def test_turn_sink_fanout_passes_explicit_char_id():
    from core.turn_sink import _fanout

    class Channel:
        name = "test"
        is_active = True

        def __init__(self):
            self.char_id = None

        async def send(self, content, user_id, behavior=None, *, char_id=None):
            self.char_id = char_id

    from channels import registry

    registry._channels = {}
    channel = Channel()
    registry.register(channel)

    await _fanout(
        assistant_text="hello",
        uid="owner",
        fanout="all",
        behavior=None,
        char_id=TEST_THIRD_CHAR_ID,
    )

    assert channel.char_id == TEST_THIRD_CHAR_ID


async def test_turn_sink_fanout_uses_pipeline_active_char_id():
    from core.turn_sink import TurnSource, record_assistant_turn

    class Channel:
        name = "test"
        is_active = True

        def __init__(self):
            self.char_id = None

        async def send(self, content, user_id, behavior=None, *, char_id=None):
            self.char_id = char_id

    class Pipeline:
        _active_character_id = TEST_THIRD_CHAR_ID

        async def post_process_critical(self, uid, content, reply, **kwargs):
            return {"turn_id": "turn-1", "critical_written": True}

        async def post_process_slow(self, uid, content, reply, critical_result, **kwargs):
            return {"emotion": "neutral", "turn_id": critical_result.get("turn_id")}

    from channels import registry

    registry._channels = {}
    channel = Channel()
    registry.register(channel)

    await record_assistant_turn(
        assistant_text="hello",
        uid="owner",
        source=TurnSource.TRIGGER,
        trigger_name="test",
        fanout="all",
        pipeline=Pipeline(),
    )

    assert channel.char_id == TEST_THIRD_CHAR_ID
