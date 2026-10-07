import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.im_ingress import IMContext, IMIngress, IMMessage
from core.write_envelope import stamp_user_chat


def message(**changes):
    base = IMMessage("im_test", "account", "external", "owner", "conversation", "event", 1, "hello", "address")
    return replace(base, **changes)


def context():
    return IMContext("im_test", AsyncMock(), stamp_user_chat(), "address", "conversation")


@pytest.mark.asyncio
async def test_redelivery_claimed_before_handler_and_uses_canonical_lock():
    from core.conversation_gate import conversation_lock
    entered = asyncio.Event()
    release = asyncio.Event()
    seen = []

    async def handler(payload, *, ingress):
        assert conversation_lock("owner").locked()
        assert ingress.lock_owned
        seen.append(payload)
        entered.set()
        await release.wait()

    ingress = IMIngress(handler)
    first = asyncio.create_task(ingress.accept(message(), context()))
    await entered.wait()
    assert not await ingress.accept(message(), context())
    release.set()
    assert await first
    assert seen[0]["user_id"] == "owner"
    assert "external" not in seen[0]
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_invalid_and_channel_collision():
    handler = AsyncMock()
    ingress = IMIngress(handler, capacity=2)
    assert not await ingress.accept(message(canonical_uid=""), context())
    assert not await ingress.accept(message(channel="other"), context())
    assert await ingress.accept(message(), context())
    assert await ingress.accept(message(account_id="other"), context())
    assert len(ingress._seen) == 2


@pytest.mark.asyncio
async def test_reply_keeps_external_address_out_of_qq_media_and_records_first(monkeypatch):
    import main
    import core.turn_sink as sink
    import core.output.text_output as output
    order = []
    async def record(**kwargs):
        order.append("record")
        assert kwargs["uid"] == "owner"
        assert kwargs["target_id"] == ""
        assert kwargs["event_channel"] == "im_test"
        assert kwargs["fanout"] == []
    async def send(address, segments, is_group):
        order.append("send")
        assert address == "address"
        assert not is_group
    monkeypatch.setattr(sink, "record_assistant_turn", record)
    qq_send = AsyncMock()
    monkeypatch.setattr(output, "send", qq_send)
    await main._qq_reality_reply_adapter(["hello"], "owner", "hi", "address", False,
                                        MagicMock(), ingress=replace(context(), send_segments=send))
    assert order == ["record", "send"]
    qq_send.assert_not_awaited()
