"""
channels/device_ws — 设备端（ESP32 等具身硬件）WebSocket 连接管理器（单例）。

端点：ws://<host>:8080/ws/device
- 单连接：新连接进来时旧连接强制断开
- channel_message 不等 ack，fire-and-forget
- action 推送后等 ack（最多 timeout 秒），超时返回 (False, "timeout")
- 心跳：服务端每 20s 发 ping，客户端须回 pong；> 70s 未收到 pong 则强制断开

独立的模块级单例，与 channels/desktop_ws.py 不共享状态，避免设备端和桌宠端互相踢线。
协议帧格式与 desktop_ws 保持完全一致（板子和 PC 客户端解析逻辑一致，省事）。

出站帧走队列 + 单 writer 任务（CC-18 问题 B）：ESP32 走 WiFi 且渲染时不读 socket，
若逐帧 await send_text 会被慢客户端的 TCP 缓冲阻塞。enqueue_json() 只做内存入队，
不 await 网络；真正的 send_text 全部收敛到 _writer_loop() 一个协程里做。
"""

import asyncio
import collections
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

_current_ws: WebSocket | None = None
_lock = asyncio.Lock()
_pending_acks: dict[str, asyncio.Future] = {}
_last_pong: float = 0.0
_connect_time: float = 0.0  # epoch when the current WS session was accepted
_heartbeat_task: asyncio.Task | None = None

_OUT_QUEUE_MAXSIZE = 64
_OUT_QUEUE_DELTA_AGG_BYTES = 512  # writer 侧聚合单帧上限，防止固件解析超长帧
_out_queue: collections.deque | None = None
_out_queue_event: asyncio.Event | None = None
_writer_task: asyncio.Task | None = None


def is_connected() -> bool:
    return _current_ws is not None


def get_connect_time() -> float:
    """Return the epoch timestamp when the current WS session was accepted (0 if not connected)."""
    return _connect_time if _current_ws is not None else 0.0


def _new_msg_id() -> str:
    return str(int(time.time() * 1000))


async def _send_json(payload: dict) -> bool:
    global _current_ws
    if _current_ws is None:
        return False
    try:
        await _current_ws.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as e:
        logger.warning(f"[device_ws] 发送失败: {e}")
        return False


def enqueue_json(payload: dict) -> bool:
    """非阻塞入队，不 await 网络；实际发送由 _writer_loop() 单独完成。

    队满（_OUT_QUEUE_MAXSIZE）时：若新帧与队尾同为 message_stream_delta 且
    msg_id 相同，原地合并 delta 字符串（无损）；否则丢弃并 WARN——
    stream_start/end、channel_message、segments 不该在 64 深度下丢，
    真丢了说明设备端已经死了，心跳会在 70s 内踢掉。
    """
    if _out_queue is None:
        return False
    if len(_out_queue) < _OUT_QUEUE_MAXSIZE:
        _out_queue.append(payload)
        if _out_queue_event is not None:
            _out_queue_event.set()
        return True
    last = _out_queue[-1]
    if (
        payload.get("type") == "message_stream_delta"
        and last.get("type") == "message_stream_delta"
        and last.get("msg_id") == payload.get("msg_id")
    ):
        last["delta"] = last.get("delta", "") + payload.get("delta", "")
        last["ts"] = payload.get("ts", last.get("ts"))
        return True
    logger.warning(f"[device_ws] 出站队列已满，丢弃帧: type={payload.get('type')!r}")
    return False


async def _writer_loop() -> None:
    """单 writer 任务：串行从出站队列取帧 send_text，设备端慢不再拖慢入队方。

    对连续同 msg_id 的 delta 帧做 ~100ms 聚合再发送，设备屏幕不需要逐 token 刷新；
    聚合帧长度不超过 _OUT_QUEUE_DELTA_AGG_BYTES，避免固件单帧解析超限。
    """
    while True:
        while not _out_queue:
            if _out_queue_event is None:
                return
            _out_queue_event.clear()
            await _out_queue_event.wait()
        item = _out_queue.popleft()
        if item.get("type") == "message_stream_delta":
            await asyncio.sleep(0.1)
            while (
                _out_queue
                and _out_queue[0].get("type") == "message_stream_delta"
                and _out_queue[0].get("msg_id") == item.get("msg_id")
                and len(item.get("delta", "").encode("utf-8")) < _OUT_QUEUE_DELTA_AGG_BYTES
            ):
                nxt = _out_queue.popleft()
                item = dict(item)
                item["delta"] = item.get("delta", "") + nxt.get("delta", "")
                item["ts"] = nxt.get("ts", item.get("ts"))
        ok = await _send_json(item)
        if not ok and _current_ws is not None:
            try:
                await _current_ws.close(code=1001, reason="writer send failed")
            except Exception:
                pass
            return


async def push_message(
    content: str,
    msg_id: str | None = None,
    *,
    char_id: str | None = None,
    round_id: str | None = None,
    domain: str | None = None,
) -> bool:
    """推送普通消息，走出站队列，fire-and-forget，不等 ack。
    msg_id 可由调用方预先生成（用于与 message_segments 共享），省略时自动生成。
    source 固定为 "reality"（历史字段，保留兼容）；domain 见 desktop_ws.push_message
    同名参数——v1 群聊梦境不 fanout 到 device（Brief 100 §3），本参数目前只是让
    ui_push 的 **kw 转发不因缺参数报错，暂无调用方会传非 None 值。
    """
    if msg_id is None:
        msg_id = _new_msg_id()
    payload: dict = {
        "type": "channel_message",
        "content": content,
        "msg_id": msg_id,
        "source": "reality",
    }
    if char_id is not None:
        payload["char_id"] = char_id
    if round_id is not None:
        payload["round_id"] = round_id
    if domain is not None:
        payload["domain"] = domain
    return enqueue_json(payload)


async def push_segments(
    content: str,
    segments: list,
    msg_id: str | None = None,
    *,
    char_id: str | None = None,
    domain: str | None = None,
) -> bool:
    """推送 narrative segments envelope，走出站队列，fire-and-forget，不等 ack。
    与 channel_message 并行发送；老客户端可安全忽略此消息类型。
    source 固定为 "reality"，与 push_message 保持一致。
    """
    if msg_id is None:
        msg_id = _new_msg_id()
    payload = {
        "type": "message_segments",
        "content": content,
        "segments": segments,
        "msg_id": msg_id,
        "source": "reality",
    }
    if char_id is not None:
        payload["char_id"] = char_id
    if domain is not None:
        payload["domain"] = domain
    return enqueue_json(payload)


async def push_stream_start(
    msg_id: str,
    *,
    char_id: str | None = None,
    round_id: str | None = None,
    domain: str | None = None,
) -> bool:
    """流式开始标记，走出站队列。前端创建空的临时气泡。"""
    payload: dict = {
        "type": "message_stream_start",
        "msg_id": msg_id,
        "source": "reality",
        "ts": time.time(),
    }
    if char_id is not None:
        payload["char_id"] = char_id
    if round_id is not None:
        payload["round_id"] = round_id
    if domain is not None:
        payload["domain"] = domain
    return enqueue_json(payload)


async def push_stream_delta(msg_id: str, delta: str) -> bool:
    """流式增量，走出站队列，fire-and-forget，不等 ack。"""
    return enqueue_json({
        "type": "message_stream_delta",
        "msg_id": msg_id,
        "delta": delta,
        "ts": time.time(),
    })


async def push_stream_end(msg_id: str) -> bool:
    """流式结束标记，走出站队列。随后 push_message(同 msg_id) 推送 scrub 后的干净版。"""
    return enqueue_json({
        "type": "message_stream_end",
        "msg_id": msg_id,
        "ts": time.time(),
    })


async def push_action_and_wait(
    action: dict, timeout: float = 5.0
) -> tuple[bool, str | None]:
    """推送设备动作并等 ack。返回 (ok, error)。"""
    if not is_connected():
        return False, "设备端 WS 未连接"
    msg_id = _new_msg_id()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _pending_acks[msg_id] = fut
    sent = await _send_json({
        "type": "action",
        "action": action,
        "msg_id": msg_id,
    })
    if not sent:
        _pending_acks.pop(msg_id, None)
        return False, "发送失败"
    try:
        ack = await asyncio.wait_for(fut, timeout=timeout)
        return ack.get("ok", False), ack.get("error")
    except asyncio.TimeoutError:
        return False, "timeout"
    finally:
        _pending_acks.pop(msg_id, None)


async def handle_connection(ws: WebSocket) -> None:
    """处理一个新 WS 连接的完整生命周期。由路由层调用。"""
    global _current_ws, _last_pong, _connect_time, _heartbeat_task
    global _out_queue, _out_queue_event, _writer_task
    await ws.accept()

    async with _lock:
        if _current_ws is not None:
            try:
                await _current_ws.close(code=1000, reason="replaced by new connection")
            except Exception:
                pass
            # 旧连接被顶替：旧连接自己的 finally 不会跑（_current_ws 已经指向新连接），
            # writer/队列必须在这里显式收尾，否则旧 writer 任务永远悬着。
            if _writer_task is not None:
                _writer_task.cancel()
                _writer_task = None
        _current_ws = ws
        _last_pong = time.time()
        _connect_time = time.time()
        _out_queue = collections.deque()
        _out_queue_event = asyncio.Event()

    # 通知 channel 抽象层设备上线
    from channels.registry import get as get_channel
    ch = get_channel("device")
    if ch is not None:
        ch.set_active(True)

    if _heartbeat_task is None or _heartbeat_task.done():
        _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    _writer_task = asyncio.create_task(_writer_loop())

    logger.info("[device_ws] 设备端已连接")

    try:
        while True:
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
            except Exception:
                continue
            await _handle_message(msg)
    except WebSocketDisconnect:
        logger.info("[device_ws] 设备端断开")
    except Exception as e:
        logger.warning(f"[device_ws] 连接异常: {e}")
    finally:
        async with _lock:
            if _current_ws is ws:
                _current_ws = None
                _connect_time = 0.0
                if _writer_task is not None:
                    _writer_task.cancel()
                    _writer_task = None
                _out_queue = None
                _out_queue_event = None
                if ch is not None:
                    ch.set_active(False)
        logger.info("[device_ws] 连接已清理")


async def _handle_message(msg: dict) -> None:
    global _last_pong
    mtype = msg.get("type")
    if mtype == "hello":
        await _send_json({"type": "hello_ack", "server_version": "1.0"})
    elif mtype == "pong":
        _last_pong = time.time()
    elif mtype == "ack":
        msg_id = msg.get("msg_id")
        fut = _pending_acks.get(msg_id)
        if fut and not fut.done():
            fut.set_result(msg)
    else:
        logger.debug(f"[device_ws] 未知消息类型: {mtype}")


async def push_group_round_start(round_id: str, group_id: str) -> bool:
    """群聊回合开始标记。前端锁输入框，显示「成员陆续回应中…」。"""
    return await _send_json({
        "type": "group_round_start",
        "round_id": round_id,
        "group_id": group_id,
    })


async def push_group_round_end(round_id: str, group_id: str) -> bool:
    """群聊回合结束标记。前端解锁输入框。"""
    return await _send_json({
        "type": "group_round_end",
        "round_id": round_id,
        "group_id": group_id,
    })


async def _heartbeat_loop() -> None:
    """每 20s 发 ping；若上次 pong 距今 > 70s 则判定超时，强制断开。"""
    global _current_ws
    while True:
        await asyncio.sleep(20)
        if _current_ws is None:
            return
        await _send_json({"type": "ping", "source": "server", "ts": time.time()})
        if time.time() - _last_pong > 70:
            logger.warning("[device_ws] pong 超时，强制断开")
            try:
                await _current_ws.close(code=1001, reason="heartbeat timeout")
            except Exception:
                pass
            return
