"""Protocol isolation and local reference REST/WebSocket integration."""
import asyncio
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
import pytest

from core.wechat_service import WechatService, WechatSettings
from integrations.wechat_transport import DeliveryResult, TransportMessage
from integrations.wechat.openclaw_weixin import OpenClawWeixinTransport, decode_messages


def event(**changes):
    return replace(TransportMessage("event-1", "external_owner", "bot_account",
                                   "external_owner", time.time(), "hello"), **changes)


def configured_service(handler=None):
    service = WechatService(handler or AsyncMock())
    service.settings = WechatSettings(enabled=True, base_url="http://bridge.invalid",
                                     account_id="bot_account", owner_sender_id="external_owner")
    service.transport = type("FakeTransport", (), {
        "connected": True, "send_text": AsyncMock(return_value=DeliveryResult("accepted")),
        "close": AsyncMock(),
    })()
    return service


def test_decoder_rejects_invalid_identity_timestamps_and_media():
    message = event().__dict__
    assert decode_messages({"events": [message]}) == [TransportMessage(**message)]
    for invalid in ({**message, "kind": "unsupported"}, {**message, "timestamp": True},
                    {**message, "timestamp": 0}, {**message, "sender_id": ""},
                    {**message, "is_group": True}):
        assert not decode_messages({"events": [invalid]})


@pytest.mark.asyncio
async def test_local_bridge_receive_and_rest_send():
    captured = []
    async def sync(request):
        assert request.headers["Authorization"] == "Bearer fixture-key"
        assert not request.query
        return web.json_response({"connected": True, "events": [event().__dict__]})
    async def send(request):
        assert request.headers["Authorization"] == "Bearer fixture-key"
        captured.append(await request.json())
        return web.json_response({"status": "accepted"})
    app = web.Application()
    app.router.add_get("/events", sync)
    app.router.add_post("/send", send)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    transport = OpenClawWeixinTransport(f"http://127.0.0.1:{port}", "fixture-key")
    stream = transport.receive()
    try:
        incoming = await asyncio.wait_for(anext(stream), 3)
        assert incoming == event(timestamp=incoming.timestamp)
        assert (await transport.send_text(incoming.sender_id, "reply")).status == "accepted"
        assert captured == [{"address": "external_owner", "text": "reply"}]
    finally:
        await stream.aclose()
        await transport.close()
        await runner.cleanup()
    assert not transport.connected


@pytest.mark.asyncio
async def test_binding_filter_and_dedup_before_business():
    handler = AsyncMock()
    service = configured_service(handler)
    for bad in (event(sender_id="stranger"), event(recipient_id="other_account"),
                event(is_group=True), event(kind="unsupported"), event(timestamp=time.time()-3600)):
        assert not await service.admit(bad, generation=0, owner_uid="canonical_owner")
    handler.assert_not_awaited()
    assert await service.admit(event(), generation=0, owner_uid="canonical_owner")
    assert not await service.admit(event(), generation=0, owner_uid="canonical_owner")
    handler.assert_awaited_once()
    payload = handler.call_args.args[0]
    context = handler.call_args.kwargs["ingress"]
    assert payload["user_id"] == "canonical_owner"
    assert context.channel == "wechat" and context.envelope.source.value == "wechat"
    await context.send_segments("external_owner", ["reply"], False)
    service.transport.send_text.assert_awaited_once_with("external_owner", "reply")
    with pytest.raises(ValueError):
        await context.send_segments("other", ["reply"], False)


@pytest.mark.asyncio
async def test_unknown_delivery_stops_remaining_segments_without_retry():
    service = configured_service()
    service.transport.send_text.return_value = DeliveryResult("unknown")
    with pytest.raises(ConnectionError, match="delivery_unknown"):
        await service.send_segments("external_owner", ["one", "two"], generation=0)
    service.transport.send_text.assert_awaited_once()
    assert service.snapshot()["last_error"] == "delivery_unknown"
    assert not service.snapshot()["proactive_effective"]


@pytest.mark.asyncio
async def test_hot_disable_closes_receiver_and_stale_reply_is_rejected(monkeypatch):
    import core.wechat_service as module
    from core import config_loader
    handler = AsyncMock()
    service = configured_service(handler)
    old_transport = service.transport
    monkeypatch.setattr(module, "load_settings", lambda: WechatSettings())
    monkeypatch.setattr(config_loader, "get_config", lambda: {"scheduler": {"owner_id": "canonical_owner"}})
    await service.apply()
    old_transport.close.assert_awaited_once()
    assert service.snapshot()["effective_state"] == "disabled"
    assert not await service.admit(event(), generation=0, owner_uid="canonical_owner")
    with pytest.raises(ConnectionError):
        await service.send_segments("external_owner", ["reply"], generation=0)
    old_transport.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_outbound_blocks_both_transport_boundaries():
    from core.no_outbound import OutboundAttempted, recovery_no_outbound
    transport = OpenClawWeixinTransport("http://bridge.invalid", "fixture-key")
    with recovery_no_outbound() as guard:
        with pytest.raises(OutboundAttempted):
            await anext(transport.receive())
        with pytest.raises(OutboundAttempted):
            await transport.send_text("address", "hello")
        assert guard.attempts == ["wechat_receive", "wechat_send"]


def test_protocol_fields_stay_inside_reference_transport():
    root = Path(__file__).parents[1]
    for filename in ("core/im_ingress.py", "core/wechat_service.py", "main.py",
                     "core/pipeline.py", "core/turn_sink.py", "channels/wechat.py"):
        source = (root / filename).read_text(encoding="utf-8")
        for field in ("MsgItem", "FromUserName", "ToUserName", "GetSyncMsg", "SendTextMessage"):
            assert field not in source, (filename, field)


@pytest.mark.asyncio
async def test_wechat_uses_existing_tool_loop_and_turn_sink_with_canonical_identity(monkeypatch):
    import main
    import core.turn_sink as sink
    from core.conversation_gate import conversation_lock
    from core.memory.scope import MemoryScope
    from tests.test_qq_fast_path_tool_loop import (
        _make_pipeline, _patch_handle_message_dependencies, _SUCCESS_RESULT, _USER_ID,
    )
    original_reply = main._qq_reality_reply_adapter
    pipeline = _make_pipeline()
    _patch_handle_message_dependencies(monkeypatch, pipeline, _SUCCESS_RESULT)
    monkeypatch.setattr(main, "_qq_reality_reply_adapter", original_reply)
    def scope(uid):
        assert uid == _USER_ID and conversation_lock(uid).locked()
        return MemoryScope.reality_scope(uid, "test_char")
    pipeline._current_reality_scope.side_effect = scope
    record = AsyncMock()
    monkeypatch.setattr(sink, "record_assistant_turn", record)
    service = configured_service(main.handle_message)
    message = event(text="现在几点")
    assert await service.admit(message, generation=0, owner_uid=_USER_ID)
    assert not await service.admit(message, generation=0, owner_uid=_USER_ID)
    pipeline.fetch_context.assert_awaited_once()
    assert pipeline.build_prompt.call_args.kwargs["channel"] == "wechat"
    assert pipeline.run_agentic_loop.call_args.kwargs["uid"] == _USER_ID
    record.assert_awaited_once()
    assert record.call_args.kwargs["envelope"].source.value == "wechat"
    assert record.call_args.kwargs["event_channel"] == "wechat"
    assert record.call_args.kwargs["uid"] == _USER_ID
    assert record.call_args.kwargs["target_id"] == ""
    service.transport.send_text.assert_awaited_once_with("external_owner", "自然语言回复")


@pytest.mark.asyncio
async def test_output_channel_requires_opt_in_and_bound_owner(monkeypatch):
    from channels.wechat import WeChatChannel
    import core.config_loader as config_loader
    import core.character_name_provider as names
    monkeypatch.setattr(config_loader, "get_config", lambda: {"scheduler": {"owner_id": "canonical_owner"}})
    monkeypatch.setattr(names, "get_char_name", lambda char_id: "Fixture Companion")
    service = configured_service()
    channel = WeChatChannel(service)
    assert not channel.is_active
    await channel.send("hello", "canonical_owner")
    service.transport.send_text.assert_not_awaited()
    service.settings.proactive_enabled = True
    assert channel.is_active
    with pytest.raises(ValueError, match="not_bound"):
        await channel.send("hello", "stranger")
    await channel.send("hello", "canonical_owner", char_id="fixture_char")
    service.transport.send_text.assert_awaited_once_with("external_owner", "hello")


@pytest.mark.asyncio
async def test_supervisor_owns_receive_worker_and_closes_on_shutdown(monkeypatch):
    import core.wechat_service as module
    import core.config_loader as config_loader
    entered = asyncio.Event()
    release = asyncio.Event()
    class FakeTransport:
        connected = False
        closed = False
        async def receive(self):
            self.connected = True
            yield event()
            await release.wait()
        async def close(self):
            self.closed = True
            self.connected = False
    transport = FakeTransport()
    async def handler(payload, *, ingress):
        assert payload["user_id"] == "canonical_owner"
        entered.set()
    settings = WechatSettings(enabled=True, base_url="http://bridge.invalid",
                             account_id="bot_account", owner_sender_id="external_owner")
    monkeypatch.setattr(module, "load_settings", lambda: settings)
    monkeypatch.setattr(config_loader, "get_config", lambda: {"scheduler": {"owner_id": "canonical_owner"}})
    monkeypatch.setenv("WECHAT_TRANSPORT_TOKEN", "fixture-key")
    service = WechatService(handler, factory=lambda settings, key: transport)
    task = asyncio.create_task(service.run())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert service.snapshot()["connected"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert transport.closed and not service.running
