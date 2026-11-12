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
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

_current_ws: WebSocket | None = None
_generation = 0
_pending_acks: dict[str, tuple[int, asyncio.Future]] = {}
_last_pong: float = 0.0
_connect_time: float = 0.0  # epoch when the current WS session was accepted
_last_ack_at: float = 0.0
_heartbeat_task: asyncio.Task | None = None


def is_connected() -> bool:
    return _current_ws is not None


def get_connect_time() -> float:
    """Return the epoch timestamp when the current WS session was accepted (0 if not connected)."""
    return _connect_time if _current_ws is not None else 0.0


def get_last_ack_time() -> float | None:
    return _last_ack_at or None


def _new_msg_id() -> str:
    return uuid4().hex


# Bound every desktop send. A half-open client makes send_text hang on the
# shared asyncio loop and freezes HTTP too, including /mobile/activate.
_SEND_TIMEOUT_S = 0.5
_TOOL_STATUS_SEND_TIMEOUT_S = _SEND_TIMEOUT_S  # alias kept for existing tests


def _owns_connection(ws: WebSocket, generation: int) -> bool:
    return _current_ws is ws and _generation == generation


def _retire_connection(ws: WebSocket, generation: int, error: str) -> None:
    """Validate and retire the owner in one event-loop turn."""
    global _current_ws, _connect_time, _heartbeat_task, _last_ack_at
    if not _owns_connection(ws, generation):
        return
    _current_ws = None
    _connect_time = 0.0
    _last_ack_at = 0.0
    for owner, future in _pending_acks.values():
        if owner == generation and not future.done():
            future.set_result({"ok": False, "error": error})
    if _heartbeat_task is not None and _heartbeat_task is not asyncio.current_task():
        _heartbeat_task.cancel()
    _heartbeat_task = None
    from channels.registry import get as get_channel
    from core.video_call_invite import disconnect as disconnect_video_invites
    disconnect_video_invites()
    channel = get_channel("desktop")
    if channel is not None:
        channel.set_active(False)


async def _close(ws: WebSocket, code: int, reason: str) -> None:
    try:
        await asyncio.wait_for(
            ws.close(code=code, reason=reason),
            timeout=min(0.2, _SEND_TIMEOUT_S),
        )
    except Exception:
        pass


async def _send_json(payload: dict, *, owner: tuple[WebSocket, int] | None = None) -> bool:
    from core.client_channels import enabled
    ws, generation = owner if owner is not None else (_current_ws, _generation)
    if not enabled("desktop") or ws is None or not _owns_connection(ws, generation):
        return False
    try:
        await asyncio.wait_for(
            ws.send_text(json.dumps(payload, ensure_ascii=False)),
            timeout=_SEND_TIMEOUT_S,
        )
        return True
    except asyncio.TimeoutError:
        logger.warning("[desktop_ws] send timed out; closing stalled socket")
        _retire_connection(ws, generation, "disconnected")
        await _close(ws, 1001, "send timeout")
        return False
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
    artifacts: list[dict] | None = None,
    request_id: str | None = None,
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
    if artifacts:
        payload["artifacts"] = artifacts
    if request_id is not None:
        payload["request_id"] = request_id
    return await _send_json(payload)


async def push_segments(
    content: str,
    segments: list,
    msg_id: str | None = None,
    *,
    char_id: str | None = None,
    domain: str | None = None,
    request_id: str | None = None,
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
    if request_id is not None:
        payload["request_id"] = request_id
    return await _send_json(payload)


async def push_tool_status(event) -> bool:
    """Push an in-memory tool status for the desktop NOW panel.

    This is deliberately not a channel_message: it has no chat correlation,
    no acknowledgement, and no durable fallback. The client must discard it at
    its TTL instead of replaying a stale "working" state after reconnect.
    """
    if not event.should_deliver():
        return False
    payload = {
        "type": "tool_status",
        "status_id": event.status_id,
        "kind": event.kind,
        "label": event.ui_label,
        "index": event.index,
        "total": event.total,
        "attempt": event.attempt,
        "ttl_ms": int(event.ttl_s * 1000),
    }
    return await _send_json(payload)


async def push_stream_start(
    msg_id: str,
    *,
    char_id: str | None = None,
    round_id: str | None = None,
    domain: str | None = None,
    request_id: str | None = None,
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
    if request_id is not None:
        payload["request_id"] = request_id
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
    ws, generation = _current_ws, _generation
    if ws is None:
        return False, "桌宠端 WS 未连接"
    msg_id = _new_msg_id()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _pending_acks[msg_id] = (generation, fut)
    try:
        sent = await _send_json({
            "type": "action",
            "action": action,
            "msg_id": msg_id,
        }, owner=(ws, generation))
        if not sent and not fut.done():
            return False, "发送失败"
        ack = await asyncio.wait_for(fut, timeout=timeout)
        return ack.get("ok", False), ack.get("error")
    except asyncio.TimeoutError:
        return False, "timeout"
    finally:
        _pending_acks.pop(msg_id, None)
        fut.cancel()


async def push_video_call_invite(invite_id: str, char_id: str, expires_in_seconds: int) -> bool:
    """Invite notification only; the user's decision returns on the scoped HTTP endpoint."""
    return await _send_json({
        "type": "video_call_invite",
        "invite_id": invite_id,
        "char_id": char_id,
        "expires_in_seconds": expires_in_seconds,
    })


async def handle_connection(ws: WebSocket) -> None:
    """处理一个新 WS 连接的完整生命周期。由路由层调用。"""
    global _current_ws, _generation, _last_pong, _connect_time, _heartbeat_task
    await ws.accept()

    # Publish ownership, channel state and heartbeat without yielding.
    previous = _current_ws
    if previous is not None:
        _retire_connection(previous, _generation, "connection_replaced")
    _generation += 1
    generation = _generation
    _current_ws = ws
    _last_pong = _connect_time = time.time()
    from channels.registry import get as get_channel
    ch = get_channel("desktop")
    if ch is not None:
        ch.set_active(True)

    _heartbeat_task = asyncio.create_task(_heartbeat_loop(ws, generation))

    logger.info("[desktop_ws] 桌宠端已连接")

    try:
        if previous is not None:
            await _close(previous, 1000, "replaced by new connection")
        while _owns_connection(ws, generation):
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
            except Exception:
                continue
            if isinstance(msg, dict):
                await _handle_message(ws, generation, msg)
    except WebSocketDisconnect:
        logger.info("[desktop_ws] 桌宠端断开")
    except Exception as e:
        logger.warning(f"[desktop_ws] 连接异常: {e}")
    finally:
        _retire_connection(ws, generation, "disconnected")
        logger.info("[desktop_ws] 连接已清理")


async def _handle_message(ws: WebSocket, generation: int, msg: dict) -> None:
    global _last_pong, _last_ack_at
    if not _owns_connection(ws, generation):
        return
    mtype = msg.get("type")
    if mtype == "hello":
        await _send_json({"type": "hello_ack", "server_version": "1.0"}, owner=(ws, generation))
    elif mtype == "pong":
        _last_pong = time.time()
    elif mtype == "ack":
        _last_ack_at = time.time()
        msg_id = msg.get("msg_id")
        pending = _pending_acks.get(msg_id) if isinstance(msg_id, str) else None
        if pending is not None:
            owner, future = pending
            if owner == generation and not future.done():
                future.set_result(msg)
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


_HEARTBEAT_INTERVAL_S = 20


async def _heartbeat_loop(ws: WebSocket, generation: int) -> None:
    """每 20s 发 ping；若上次 pong 距今 > 70s 则判定超时，强制断开。"""
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
        if not _owns_connection(ws, generation):
            return
        if time.time() - _last_pong > 70:
            logger.warning("[desktop_ws] pong 超时，强制断开")
            _retire_connection(ws, generation, "disconnected")
            await _close(ws, 1001, "heartbeat timeout")
            return
        await _send_json({"type": "ping", "source": "server", "ts": time.time()}, owner=(ws, generation))
