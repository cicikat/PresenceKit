import asyncio
import json
import time

import pytest


pytestmark = pytest.mark.usefixtures("enabled_client_channels")


@pytest.mark.parametrize("failed_file", ["mobile_queue", "mobile_queue_seq"])
@pytest.mark.parametrize("failure_mode", ["false", "exception"])
@pytest.mark.parametrize("send_method", ["send", "send_with_behavior"])
async def test_send_reports_persistence_failure_and_recovers(
    sandbox, monkeypatch, failed_file, failure_mode, send_method
):
    import channels.mobile as mobile_module

    signals = []
    monkeypatch.setattr(mobile_module, "schedule_signal_publish", signals.append)
    channel = mobile_module.MobileChannel()
    await channel.send("existing", "owner", msg_id="turn-existing")
    original_queue = sandbox.mobile_queue().read_bytes()
    signals.clear()
    real_write = mobile_module.safe_write_json

    def fail_write(path, data):
        if path == getattr(sandbox, failed_file)():
            if failure_mode == "exception":
                raise OSError("injected persistence failure")
            return False
        return real_write(path, data)

    monkeypatch.setattr(mobile_module, "safe_write_json", fail_write)
    with pytest.raises(OSError, match="persist"):
        await getattr(channel, send_method)(
            "failed", "owner", behavior={"action_type": "notify"}, msg_id="turn-failed"
        )

    assert sandbox.mobile_queue().read_bytes() == original_queue
    assert signals == []

    monkeypatch.setattr(mobile_module, "safe_write_json", real_write)
    await channel.send("recovered", "owner", msg_id="turn-recovered")
    messages = await channel.poll()
    assert [item["id"] for item in messages] == ["turn-existing", "turn-recovered"]
    assert messages[1]["seq"] > messages[0]["seq"]
    assert messages[1]["turn_id"] == "turn-recovered"
    assert signals == [messages[1]]


async def test_relay_scheduling_failure_does_not_fail_persisted_send(sandbox, monkeypatch):
    import channels.mobile as mobile_module

    def fail_signal(_item):
        raise RuntimeError("injected relay scheduling failure")

    monkeypatch.setattr(mobile_module, "schedule_signal_publish", fail_signal)
    channel = mobile_module.MobileChannel()
    await channel.send("persisted", "owner", msg_id="turn-persisted")

    messages = await channel.poll()
    assert [item["id"] for item in messages] == ["turn-persisted"]


async def _push(channel, count: int) -> None:
    for index in range(count):
        await channel.send(f"message-{index + 1}", "owner", msg_id=f"turn-{index + 1}")


async def test_poll_retains_until_ack_and_ack_removes_prefix(sandbox):
    from channels.mobile import MobileChannel

    channel = MobileChannel()
    await _push(channel, 3)

    messages = await channel.poll(after=None, limit=20)
    stored = json.loads(sandbox.mobile_queue().read_text(encoding="utf-8"))

    assert len(messages) == 3
    assert len(stored) == 3
    assert [item["seq"] for item in messages] == sorted(item["seq"] for item in messages)

    remaining = await channel.ack(messages[1]["seq"])
    after_ack = await channel.poll(after=None, limit=20)

    assert remaining == 1
    assert [item["id"] for item in after_ack] == ["turn-3"]


async def test_poll_after_returns_only_newer_items(sandbox):
    from channels.mobile import MobileChannel

    channel = MobileChannel()
    await _push(channel, 3)
    messages = await channel.poll(after=None, limit=20)

    newer = await channel.poll(after=messages[1]["seq"], limit=20)

    assert [item["id"] for item in newer] == ["turn-3"]


async def test_concurrent_push_assigns_unique_monotonic_sequences(sandbox):
    from channels.mobile import MobileChannel

    channel = MobileChannel()
    await asyncio.gather(
        *(channel.send(f"message-{index}", "owner", msg_id=f"turn-{index}") for index in range(40))
    )

    messages = await channel.poll(after=None, limit=50)
    sequences = [item["seq"] for item in messages]

    assert len(sequences) == 40
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == 40


async def test_sequence_remains_monotonic_after_full_ack(sandbox):
    from channels.mobile import MobileChannel

    channel = MobileChannel()
    await channel.send("first", "owner", msg_id="turn-first")
    first = (await channel.poll(after=None, limit=20))[0]
    await channel.ack(first["seq"])

    await channel.send("second", "owner", msg_id="turn-second")
    second = (await channel.poll(after=None, limit=20))[0]

    assert second["seq"] > first["seq"]


async def test_legacy_queue_items_receive_sequences_before_poll(sandbox):
    from channels.mobile import MobileChannel
    from core.safe_write import safe_write_json

    safe_write_json(
        sandbox.mobile_queue(),
        [
            {"id": "legacy-1", "content": "one", "user_id": "owner", "timestamp": time.time()},
            {"id": "legacy-2", "content": "two", "user_id": "owner", "timestamp": time.time()},
        ],
    )
    channel = MobileChannel()

    messages = await channel.poll(after=None, limit=20)
    await channel.ack(messages[-1]["seq"])
    await channel.send("new", "owner", msg_id="turn-new")
    new_message = (await channel.poll(after=None, limit=20))[0]

    assert [item["seq"] for item in messages] == [1, 2]
    assert new_message["seq"] == 3


async def test_queue_prunes_by_ttl_and_capacity(sandbox, monkeypatch):
    import channels.mobile as mobile_module
    from channels.mobile import MobileChannel
    from core.safe_write import safe_write_json

    monkeypatch.setattr(mobile_module, "_QUEUE_MAX_ITEMS", 2)
    safe_write_json(
        sandbox.mobile_queue(),
        [
            {
                "id": "expired",
                "seq": 1,
                "content": "expired",
                "user_id": "owner",
                "timestamp": time.time() - mobile_module._QUEUE_MAX_AGE_SECONDS - 1,
            }
        ],
    )
    safe_write_json(sandbox.mobile_queue_seq(), {"next_seq": 2})
    channel = MobileChannel()

    await _push(channel, 3)
    messages = await channel.poll(after=None, limit=20)

    assert [item["id"] for item in messages] == ["turn-2", "turn-3"]


async def test_mobile_router_poll_cursor_and_ack_remaining(sandbox, monkeypatch):
    from admin.routers import mobile as mobile_router
    from channels.mobile import MobileChannel

    channel = MobileChannel()
    await _push(channel, 3)
    monkeypatch.setattr(mobile_router, "_get_mobile_channel", lambda: channel)

    response = await mobile_router.mobile_poll(after=None, limit=20, wait=0, auth=True)
    ack_response = await mobile_router.mobile_ack(
        {"ack_seq": response["messages"][1]["seq"]},
        auth=True,
    )

    assert response["cursor"] == response["messages"][-1]["seq"]
    assert response["ok"] is True
    assert response["active"] is True
    assert all("seq" in item for item in response["messages"])
    assert ack_response == {"ok": True, "remaining": 1}


async def test_mobile_router_unregistered_channel_has_explicit_business_failure(monkeypatch):
    from admin.routers import mobile as mobile_router

    monkeypatch.setattr(mobile_router, "_get_mobile_channel", lambda: None)

    activation = await mobile_router.mobile_activate(auth=True)
    deactivation = await mobile_router.mobile_deactivate(auth=True)
    poll = await mobile_router.mobile_poll(after=9, limit=20, wait=0, auth=True)

    assert activation == {
        "ok": False,
        "active": False,
        "error": "mobile channel 未注册",
    }
    assert deactivation == activation
    assert poll == {
        "ok": False,
        "active": False,
        "error": "mobile channel 未注册",
        "messages": [],
        "count": 0,
        "cursor": 9,
    }
