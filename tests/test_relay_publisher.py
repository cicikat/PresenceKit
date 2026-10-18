from tests.fixtures.public_assets import TEST_CHAR_ID
import asyncio
import json

import httpx

import pytest


pytestmark = pytest.mark.usefixtures("enabled_client_channels")


class _MockAsyncClient:
    def __init__(self, handler, **_kwargs):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, **kwargs):
        request = httpx.Request(
            "POST",
            url,
            headers=kwargs.get("headers"),
            json=kwargs.get("json"),
        )
        return self._handler(request)


async def _wait_for_publish_tasks(relay_publisher):
    await asyncio.sleep(0)
    tasks = tuple(relay_publisher._publish_tasks)
    if tasks:
        await asyncio.gather(*tasks)


async def test_enqueue_publishes_signal_without_private_message_fields(
    sandbox,
    monkeypatch,
):
    import channels.relay_publisher as relay_publisher
    from channels.mobile import MobileChannel

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, request=request)

    monkeypatch.setattr(
        relay_publisher,
        "get_config",
        lambda: {
            "relay_base_url": "https://relay.example",
            "relay_topic": f'{TEST_CHAR_ID}/owner/device',
            "relay_token": "publish-secret",
        },
    )
    monkeypatch.setattr(
        relay_publisher.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(handler, **kwargs),
    )

    channel = MobileChannel()
    await channel.send(
        "private message",
        "owner",
        behavior={"kind": "overlay_message"},
        msg_id="turn-1",
    )
    await _wait_for_publish_tasks(relay_publisher)

    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body == {
        "id": "turn-1",
        "seq": 1,
        "user_id": "owner",
        "timestamp": body["timestamp"],
        "signal": "new_message",
    }
    assert "content" not in body
    assert "behavior" not in body
    assert requests[0].headers["authorization"] == "Bearer publish-secret"


async def test_relay_5xx_retries_three_times_without_affecting_queue(
    sandbox,
    monkeypatch,
):
    import channels.relay_publisher as relay_publisher
    from channels.mobile import MobileChannel

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503, request=request)

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(
        relay_publisher,
        "get_config",
        lambda: {
            "relay_base_url": "https://relay.example",
            "relay_topic": f'{TEST_CHAR_ID}/owner/device',
            "relay_token": "publish-secret",
        },
    )
    monkeypatch.setattr(
        relay_publisher.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(handler, **kwargs),
    )
    monkeypatch.setattr(relay_publisher.asyncio, "sleep", no_delay)

    channel = MobileChannel()
    await channel.send("still persisted", "owner", msg_id="turn-retry")
    await _wait_for_publish_tasks(relay_publisher)

    assert len(requests) == 3
    stored = json.loads(sandbox.mobile_queue().read_text(encoding="utf-8"))
    assert stored[0]["content"] == "still persisted"
    assert stored[0]["id"] == "turn-retry"


async def test_missing_relay_config_does_not_post(sandbox, monkeypatch):
    import channels.relay_publisher as relay_publisher
    from channels.mobile import MobileChannel

    client_created = False

    def unexpected_client(**_kwargs):
        nonlocal client_created
        client_created = True
        raise AssertionError("relay client must not be created")

    monkeypatch.setattr(relay_publisher, "get_config", lambda: {})
    monkeypatch.setattr(relay_publisher.httpx, "AsyncClient", unexpected_client)

    channel = MobileChannel()
    await channel.send("queue only", "owner", msg_id="turn-no-relay")
    await _wait_for_publish_tasks(relay_publisher)

    assert client_created is False
    stored = json.loads(sandbox.mobile_queue().read_text(encoding="utf-8"))
    assert stored[0]["content"] == "queue only"
