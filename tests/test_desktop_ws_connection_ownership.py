import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from channels import desktop_ws


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = asyncio.Queue()
        self.reading = asyncio.Event()
        self.closed = []

    async def accept(self):
        pass

    async def receive_text(self):
        self.reading.set()
        frame = await self.incoming.get()
        if frame is None:
            raise WebSocketDisconnect()
        return json.dumps(frame)

    async def send_text(self, text):
        self.sent.put_nowait(json.loads(text))

    async def close(self, **kwargs):
        self.closed.append(kwargs["reason"])
        # Frames already delivered by the transport can still be read.

    def receive(self, **frame):
        self.incoming.put_nowait(frame)


@asynccontextmanager
async def connection_tasks():
    tasks = []

    def start(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    try:
        yield start
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def bounded(awaitable):
    return await asyncio.wait_for(awaitable, 1)


@pytest.fixture
def desktop(monkeypatch):
    channel = SimpleNamespace(active=False)
    channel.set_active = lambda value: setattr(channel, "active", value)
    monkeypatch.setattr("channels.registry.get", lambda name: channel)
    monkeypatch.setattr("core.client_channels.enabled", lambda name: True)
    monkeypatch.setattr("core.video_call_invite.disconnect", lambda: None)
    return channel


@pytest.mark.parametrize("late_type", ["pong", "ack", "hello"])
async def test_late_frames_and_finally_cannot_affect_new_connection(desktop, monkeypatch, late_type):
    old, new = Socket(), Socket()
    now = 100.0
    monkeypatch.setattr(desktop_ws, "time", SimpleNamespace(time=lambda: now))
    async with connection_tasks() as tasks:
        first = tasks(desktop_ws.handle_connection(old))
        await bounded(old.reading.wait())
        old_action = tasks(desktop_ws.push_action_and_wait({"type": "old"}))
        await bounded(old.sent.get())
        second = tasks(desktop_ws.handle_connection(new))
        await bounded(new.reading.wait())
        new_action = tasks(desktop_ws.push_action_and_wait({"type": "new"}))
        action = await bounded(new.sent.get())
        pong_before = desktop_ws._last_pong
        ack_before = desktop_ws.get_last_ack_time()
        connected_at = desktop_ws.get_connect_time()
        now += 1
        old.receive(type=late_type, msg_id=action["msg_id"], ok=False)
        old.incoming.put_nowait(None)
        await bounded(first)
        assert desktop_ws._last_pong == pong_before
        assert desktop_ws.get_last_ack_time() == ack_before
        assert new.sent.empty()
        assert desktop.active and desktop_ws.get_connect_time() == connected_at
        assert new.closed == []
        assert await bounded(old_action) == (False, "connection_replaced")

        new.receive(type="pong")
        new.receive(type="ack", msg_id=action["msg_id"], ok=True)
        assert await bounded(new_action) == (True, None)
        assert desktop_ws._last_pong > pong_before
        assert await desktop_ws.push_message("reply", msg_id="canonical")
        assert (await bounded(new.sent.get()))["msg_id"] == "canonical"
        pending = tasks(desktop_ws.push_action_and_wait({"type": "disconnect"}))
        await bounded(new.sent.get())
        new.incoming.put_nowait(None)
        await bounded(second)
        assert await bounded(pending) == (False, "disconnected")
        assert not desktop.active and not desktop_ws.is_connected()


async def test_heartbeat_timeout_close_in_flight_during_reconnect(desktop, monkeypatch):
    old, new = Socket(), Socket()
    closing, finish_close = asyncio.Event(), asyncio.Event()
    now = 100.0

    async def close_old(*, code, reason):
        assert code == 1001 and reason == "heartbeat timeout"
        closing.set()
        await finish_close.wait()
        old.incoming.put_nowait(None)

    monkeypatch.setattr(old, "close", close_old)
    monkeypatch.setattr(desktop_ws, "time", SimpleNamespace(time=lambda: now))
    monkeypatch.setattr(desktop_ws, "_HEARTBEAT_INTERVAL_S", 0)
    async with connection_tasks() as tasks:
        first = tasks(desktop_ws.handle_connection(old))
        await bounded(old.reading.wait())
        now += 71
        await bounded(closing.wait())
        monkeypatch.setattr(desktop_ws, "_HEARTBEAT_INTERVAL_S", 20)
        second = tasks(desktop_ws.handle_connection(new))
        await bounded(new.reading.wait())
        finish_close.set()
        await bounded(first)
        assert new.closed == []
        assert desktop.active and desktop_ws.is_connected()
        assert await desktop_ws.push_message("after timeout")
        assert (await bounded(new.sent.get()))["content"] == "after timeout"
        new.incoming.put_nowait(None)
        await bounded(second)
