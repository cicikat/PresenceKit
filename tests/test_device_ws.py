from tests.fixtures.public_assets import TEST_THIRD_CHAR_ID
import asyncio
import collections
import importlib

import pytest

_real_asyncio_sleep = asyncio.sleep  # captured before any test monkeypatches asyncio.sleep


async def test_device_ws_envelopes_match_desktop_format(monkeypatch):
    """device_ws push_* 帧格式应与 desktop_ws 完全一致（板子/PC 客户端解析逻辑一致）。
    device_ws 的 push_* 现在只入队（enqueue_json），不直接 send；用出站队列内容比对。
    """
    from channels import desktop_ws, device_ws

    dsent = []

    async def fake_dsend(payload):
        dsent.append(payload)
        return True

    monkeypatch.setattr(desktop_ws, "_send_json", fake_dsend)
    monkeypatch.setattr(device_ws, "_out_queue", collections.deque())
    monkeypatch.setattr(device_ws, "_out_queue_event", asyncio.Event())

    await desktop_ws.push_message("hello", msg_id="turn-1", char_id=TEST_THIRD_CHAR_ID)
    await device_ws.push_message("hello", msg_id="turn-1", char_id=TEST_THIRD_CHAR_ID)
    vsent = list(device_ws._out_queue)
    assert dsent[0] == vsent[0]

    await desktop_ws.push_segments(
        "hello", [{"type": "say", "text": "hello"}], msg_id="turn-1", char_id=TEST_THIRD_CHAR_ID
    )
    await device_ws.push_segments(
        "hello", [{"type": "say", "text": "hello"}], msg_id="turn-1", char_id=TEST_THIRD_CHAR_ID
    )
    vsent = list(device_ws._out_queue)
    assert dsent[1] == vsent[1]

    assert vsent[0]["type"] == "channel_message"
    assert vsent[1]["type"] == "message_segments"


async def test_device_ws_action_ack_resolves_future(monkeypatch):
    """push_action_and_wait 发出的 msg_id 收到匹配 ack 后应 resolve，返回 (ok, error)。"""
    from channels import device_ws

    device_ws._current_ws = object()  # is_connected() only checks not-None
    sent = []

    async def fake_send(payload):
        sent.append(payload)
        return True

    monkeypatch.setattr(device_ws, "_send_json", fake_send)

    async def _respond_ack():
        # give push_action_and_wait a tick to register the pending future
        await asyncio.sleep(0)
        msg_id = sent[0]["msg_id"]
        await device_ws._handle_message({"type": "ack", "msg_id": msg_id, "ok": True})

    try:
        _, (ok, err) = await asyncio.gather(
            _respond_ack(), device_ws.push_action_and_wait({"type": "show_heart"}, timeout=2.0)
        )
    finally:
        device_ws._current_ws = None

    assert sent[0]["type"] == "action"
    assert sent[0]["action"] == {"type": "show_heart"}
    assert ok is True
    assert err is None


async def test_device_ws_action_ack_timeout(monkeypatch):
    from channels import device_ws

    device_ws._current_ws = object()

    async def fake_send(payload):
        return True

    monkeypatch.setattr(device_ws, "_send_json", fake_send)

    try:
        ok, err = await device_ws.push_action_and_wait({"type": "show_heart"}, timeout=0.05)
    finally:
        device_ws._current_ws = None

    assert ok is False
    assert err == "timeout"


@pytest.mark.parametrize("module_name", ["channels.desktop_ws", "channels.device_ws"])
async def test_concurrent_actions_have_distinct_ack_ids(module_name, monkeypatch):
    """同一毫秒发出的 action 不能覆盖彼此的 pending future。"""
    ws_module = importlib.import_module(module_name)
    ws_module._current_ws = object()
    ws_module._pending_acks.clear()
    sent = []

    async def fake_send(payload, **kwargs):
        sent.append(payload)
        return True

    monkeypatch.setattr(ws_module, "_send_json", fake_send)
    first = asyncio.create_task(ws_module.push_action_and_wait({"type": "first"}, timeout=1))
    second = asyncio.create_task(ws_module.push_action_and_wait({"type": "second"}, timeout=1))
    try:
        for _ in range(10):
            if len(sent) == 2:
                break
            await asyncio.sleep(0)

        ids = [payload["msg_id"] for payload in sent]
        assert len(ids) == 2
        assert len(set(ids)) == 2
        assert all(len(msg_id) == 32 for msg_id in ids)

        for msg_id in ids:
            frame = {"type": "ack", "msg_id": msg_id, "ok": True}
            if module_name == "channels.desktop_ws":
                await ws_module._handle_message(ws_module._current_ws, ws_module._generation, frame)
            else:
                await ws_module._handle_message(frame)
        assert await asyncio.gather(first, second) == [(True, None), (True, None)]
    finally:
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        ws_module._pending_acks.clear()
        ws_module._current_ws = None


async def test_device_ws_hello_and_pong_handled(monkeypatch):
    from channels import device_ws

    sent = []

    async def fake_send(payload):
        sent.append(payload)
        return True

    monkeypatch.setattr(device_ws, "_send_json", fake_send)

    await device_ws._handle_message({"type": "hello"})
    assert sent[-1] == {"type": "hello_ack", "server_version": "1.0"}

    before = device_ws._last_pong
    await asyncio.sleep(0.01)
    await device_ws._handle_message({"type": "pong"})
    assert device_ws._last_pong > before


async def test_device_and_desktop_ws_are_independent_singletons():
    """独立单例：device_ws 的连接状态不应受 desktop_ws 影响，反之亦然。"""
    from channels import desktop_ws, device_ws

    assert desktop_ws._current_ws is not device_ws._current_ws or (
        desktop_ws._current_ws is None and device_ws._current_ws is None
    )
    assert desktop_ws._pending_acks is not device_ws._pending_acks

    device_ws._current_ws = object()
    try:
        assert device_ws.is_connected() is True
        assert desktop_ws.is_connected() is False
    finally:
        device_ws._current_ws = None


def test_enqueue_json_returns_false_without_queue():
    """未连接（_out_queue 为 None）时 enqueue_json 直接返回 False，不报错。"""
    from channels import device_ws

    assert device_ws._out_queue is None
    assert device_ws.enqueue_json({"type": "channel_message"}) is False


def test_enqueue_json_merges_delta_on_full_queue(monkeypatch):
    """队满时同 msg_id 的 message_stream_delta 原地合并，不丢字。"""
    from channels import device_ws

    monkeypatch.setattr(device_ws, "_out_queue", collections.deque())
    monkeypatch.setattr(device_ws, "_out_queue_event", asyncio.Event())
    monkeypatch.setattr(device_ws, "_OUT_QUEUE_MAXSIZE", 2)

    assert device_ws.enqueue_json({"type": "channel_message", "msg_id": "a"}) is True
    assert device_ws.enqueue_json(
        {"type": "message_stream_delta", "msg_id": "x", "delta": "hel"}
    ) is True
    assert len(device_ws._out_queue) == 2

    # 队满，新 delta 与队尾同 msg_id -> 合并，不新增队列长度
    ok = device_ws.enqueue_json(
        {"type": "message_stream_delta", "msg_id": "x", "delta": "lo"}
    )
    assert ok is True
    assert len(device_ws._out_queue) == 2
    assert device_ws._out_queue[-1]["delta"] == "hello"


def test_enqueue_json_drops_and_warns_on_full_non_mergeable(monkeypatch, caplog):
    """队满且新帧不可合并（类型不同或 msg_id 不同）时丢弃并 WARN。"""
    from channels import device_ws

    monkeypatch.setattr(device_ws, "_out_queue", collections.deque())
    monkeypatch.setattr(device_ws, "_out_queue_event", asyncio.Event())
    monkeypatch.setattr(device_ws, "_OUT_QUEUE_MAXSIZE", 1)

    assert device_ws.enqueue_json({"type": "channel_message", "msg_id": "a"}) is True
    with caplog.at_level("WARNING"):
        ok = device_ws.enqueue_json({"type": "channel_message", "msg_id": "b"})
    assert ok is False
    assert len(device_ws._out_queue) == 1
    assert "出站队列已满" in caplog.text


async def test_writer_loop_drains_queue_in_order(monkeypatch):
    """writer 任务按入队顺序把帧发给 _send_json（非 delta 帧不做聚合等待）。"""
    from channels import device_ws

    monkeypatch.setattr(device_ws, "_out_queue", collections.deque())
    monkeypatch.setattr(device_ws, "_out_queue_event", asyncio.Event())

    sent = []

    async def fake_send(payload):
        sent.append(payload)
        return True

    monkeypatch.setattr(device_ws, "_send_json", fake_send)

    device_ws.enqueue_json({"type": "channel_message", "msg_id": "1"})
    device_ws.enqueue_json({"type": "channel_message", "msg_id": "2"})

    task = asyncio.create_task(device_ws._writer_loop())
    try:
        await asyncio.wait_for(_wait_until(lambda: len(sent) == 2), timeout=1.0)
    finally:
        task.cancel()

    assert [p["msg_id"] for p in sent] == ["1", "2"]


async def test_writer_loop_aggregates_consecutive_same_msg_deltas(monkeypatch):
    """连续同 msg_id 的 delta 帧在 writer 侧被聚合为一帧再发送，减少设备端刷新频率。"""
    from channels import device_ws

    monkeypatch.setattr(device_ws, "_out_queue", collections.deque())
    monkeypatch.setattr(device_ws, "_out_queue_event", asyncio.Event())
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    sent = []

    async def fake_send(payload):
        sent.append(payload)
        return True

    monkeypatch.setattr(device_ws, "_send_json", fake_send)

    device_ws.enqueue_json({"type": "message_stream_delta", "msg_id": "x", "delta": "he"})
    device_ws.enqueue_json({"type": "message_stream_delta", "msg_id": "x", "delta": "l"})
    device_ws.enqueue_json({"type": "message_stream_delta", "msg_id": "x", "delta": "lo"})

    task = asyncio.create_task(device_ws._writer_loop())
    try:
        await asyncio.wait_for(_wait_until(lambda: len(sent) == 1), timeout=1.0)
    finally:
        task.cancel()

    assert sent[0]["delta"] == "hello"


async def _wait_until(cond, interval: float = 0.005) -> None:
    while not cond():
        await asyncio.sleep(interval)


async def _fake_sleep(_seconds):
    """跳过 writer 的 100ms 聚合等待，让测试快速通过（不改变聚合逻辑本身）。"""
    await _real_asyncio_sleep(0)
