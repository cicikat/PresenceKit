"""
channels/desktop — 桌宠通道（双轨并行）。

优先走 WebSocket 实时推送；WS 未连接或推送失败时降级到文件队列
（桌宠端轮询 data/channel_queue.json）。
"""

import asyncio
import json
import time
import logging

from channels.base import BaseChannel
from core.sandbox import get_paths
from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)

_queue_lock = asyncio.Lock()


class DesktopChannel(BaseChannel):
    def __init__(self):
        self._fallback_active = False  # 文件通道兜底活跃标志，由 set_active 控制

    @property
    def name(self) -> str:
        return "desktop"

    @property
    def is_active(self) -> bool:
        from channels import desktop_ws
        if desktop_ws.is_connected():
            return True
        return self._fallback_active

    def set_active(self, active: bool) -> None:
        # 每条聊天回复发送时都会重新 set_active(True)（见 admin/routers/chat.py），
        # 桌宠已在线时这是重复电平，不是状态转换；只在真正变化时打 INFO
        # （Brief 54-C：记边沿，不记电平）。
        changed = self._fallback_active != active
        self._fallback_active = active
        if changed:
            logger.info(f"[desktop_channel] fallback 活跃状态: {active}")
        else:
            logger.debug(f"[desktop_channel] fallback 活跃状态保持: {active}")

    async def send(
        self,
        content: str,
        user_id: str,
        behavior: dict | None = None,
        msg_id: str | None = None,
        *,
        char_id: str | None = None,
        sticker: dict | None = None,
    ) -> None:
        from channels import desktop_ws
        # 路径 1：WS 实时推送
        if desktop_ws.is_connected():
            push_kwargs = {"msg_id": msg_id}
            if char_id is not None:
                push_kwargs["char_id"] = char_id
            if sticker is not None:
                push_kwargs["sticker"] = sticker
            ok = await desktop_ws.push_message(content, **push_kwargs)
            if ok:
                if behavior:
                    action_ok, err = await desktop_ws.push_action_and_wait(behavior, timeout=5.0)
                    if not action_ok:
                        logger.warning(f"[desktop_channel] WS action 失败，降级到文件: {err}")
                        await self._write_action_to_queue(behavior)
                return
            logger.warning("[desktop_channel] WS push 失败，降级到文件")
        # 路径 2：文件队列 fallback
        await self._write_to_queue(content, char_id=char_id, sticker=sticker)
        if behavior:
            await self._write_action_to_queue(behavior)

    async def _write_to_queue(
        self,
        content: str,
        *,
        char_id: str | None = None,
        sticker: dict | None = None,
    ) -> None:
        try:
            async with _queue_lock:
                q_file = get_paths().channel_queue()
                q_file.parent.mkdir(parents=True, exist_ok=True)
                queue = []
                if q_file.exists():
                    queue = json.loads(q_file.read_text(encoding="utf-8"))
                item = {
                    "content": content,
                    "timestamp": time.time(),
                }
                if char_id is not None:
                    item["char_id"] = char_id
                if sticker is not None:
                    item["sticker"] = sticker
                queue.append(item)
                safe_write_json(q_file, queue)
        except Exception as e:
            logger.warning(f"[desktop_channel] 写入队列失败: {e}")

    async def _write_action_to_queue(self, behavior: dict) -> None:
        try:
            async with _queue_lock:
                action_file = get_paths().agent_actions()
                action_file.parent.mkdir(parents=True, exist_ok=True)
                queue = []
                if action_file.exists():
                    queue = json.loads(action_file.read_text(encoding="utf-8"))
                    if not isinstance(queue, list):
                        queue = []
                queue.append(behavior)
                safe_write_json(action_file, queue)
        except Exception as e:
            logger.warning(f"[desktop_channel] 写入动作队列失败: {e}")
