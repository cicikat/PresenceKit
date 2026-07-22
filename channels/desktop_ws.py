"""
channels/desktop_ws — 桌宠端 WebSocket 连接管理器（单例）。

端点：ws://127.0.0.1:8080/ws/desktop
- 单连接：新连接进来时旧连接强制断开
- channel_message 不等 ack，fire-and-forget
- action 推送后等 ack（最多 timeout 秒），超时返回 (False, "timeout")
- 心跳：服务端每 20s 发 ping，客户端须回 pong；> 70s 未收到 pong 则强制断开
"""

import asyncio
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
        logger.warning(f"[desktop_ws] 发送失败: {e}")
        return False


async def push_message(
    content: str,
    msg_id: str | None = None,
    *,
    char_id: str | None = None,
    round_id: str | None = None,
    domain: str | None = None,
    sticker: dict | None = None,
) -> bool:
    """推送普通消息，fire-and-forget，不等 ack。
    msg_id 可由调用方预先生成（用于与 message_segments 共享），省略时自动生成。
    source 固定为 "reality"（历史字段，保留兼容）；domain 缺省不发送 = 前端按 reality
    处理，群聊梦境（core.stage.dream_runtime）传 domain="dream" 供前端路由到梦境窗口，
    见 Brief 100 §3。
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
    if sticker is not None:
        payload["sticker"] = sticker
    return await _send_json(payload)


async def push_segments(
    content: str,
    segments: list,
    msg_id: str | None = None,
    *,
    char_id: str | None = None,
    domain: str | None = None,
) -> bool:
    """推送 narrative segments envelope，fire-and-forget，不等 ack。
    与 channel_message 并行发送；老客户端可安全忽略此消息类型。
    source 固定为 "reality"，与 push_message 保持一致；domain 同 push_message。
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
    return await _send_json(payload)


async def push_stream_start(
    msg_id: str,
    *,
    char_id: str | None = None,
    round_id: str | None = None,
    domain: str | None = None,
) -> bool:
    """流式开始标记。前端创建空的临时气泡。"""
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
    return await _send_json(payload)


async def push_stream_delta(msg_id: str, delta: str) -> bool:
    """流式增量。fire-and-forget，不等 ack。"""
    return await _send_json({
        "type": "message_stream_delta",
        "msg_id": msg_id,
        "delta": delta,
        "ts": time.time(),
    })


async def push_stream_end(msg_id: str) -> bool:
    """流式结束标记。随后 push_message(同 msg_id) 推送 scrub 后的干净版。"""
    return await _send_json({
        "type": "message_stream_end",
        "msg_id": msg_id,
        "ts": time.time(),
    })


async def push_action_and_wait(
    action: dict, timeout: float = 5.0
) -> tuple[bool, str | None]:
    """推送桌面动作并等 ack。返回 (ok, error)。"""
    if not is_connected():
        return False, "桌宠端 WS 未连接"
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
    await ws.accept()

    async with _lock:
        if _current_ws is not None:
            try:
                await _current_ws.close(code=1000, reason="replaced by new connection")
            except Exception:
                pass
        _current_ws = ws
        _last_pong = time.time()
        _connect_time = time.time()

    # 通知 channel 抽象层桌宠上线
    from channels.registry import get as get_channel
    ch = get_channel("desktop")
    if ch is not None:
        ch.set_active(True)

    if _heartbeat_task is None or _heartbeat_task.done():
        _heartbeat_task = asyncio.create_task(_heartbeat_loop())

    logger.info("[desktop_ws] 桌宠端已连接")

    try:
        while True:
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
            except Exception:
                continue
            await _handle_message(msg)
    except WebSocketDisconnect:
        logger.info("[desktop_ws] 桌宠端断开")
    except Exception as e:
        logger.warning(f"[desktop_ws] 连接异常: {e}")
    finally:
        async with _lock:
            if _current_ws is ws:
                _current_ws = None
                _connect_time = 0.0
                if ch is not None:
                    ch.set_active(False)
        logger.info("[desktop_ws] 连接已清理")


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
        logger.debug(f"[desktop_ws] 未知消息类型: {mtype}")


async def push_group_round_start(round_id: str, group_id: str, *, domain: str | None = None) -> bool:
    """群聊回合开始标记。前端锁输入框，显示「成员陆续回应中…」。

    domain: 可选 "reality" | "dream"（Brief 100 §3）。省略时前端按旧行为
    视为 reality —— 旧客户端忽略未知字段，零破坏。
    """
    payload: dict = {
        "type": "group_round_start",
        "round_id": round_id,
        "group_id": group_id,
    }
    if domain is not None:
        payload["domain"] = domain
    return await _send_json(payload)


async def push_group_round_end(round_id: str, group_id: str, *, domain: str | None = None) -> bool:
    """群聊回合结束标记。前端解锁输入框。domain 语义同 push_group_round_start。"""
    payload: dict = {
        "type": "group_round_end",
        "round_id": round_id,
        "group_id": group_id,
    }
    if domain is not None:
        payload["domain"] = domain
    return await _send_json(payload)


async def _heartbeat_loop() -> None:
    """每 20s 发 ping；若上次 pong 距今 > 70s 则判定超时，强制断开。"""
    global _current_ws
    while True:
        await asyncio.sleep(20)
        if _current_ws is None:
            return
        await _send_json({"type": "ping", "source": "server", "ts": time.time()})
        if time.time() - _last_pong > 70:
            logger.warning("[desktop_ws] pong 超时，强制断开")
            try:
                await _current_ws.close(code=1001, reason="heartbeat timeout")
            except Exception:
                pass
            return
