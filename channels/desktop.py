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
        self._fallback_active = active
        logger.info(f"[desktop_channel] fallback 活跃状态: {active}")

    async def send(self, content: str, user_id: str) -> None:
        from channels import desktop_ws
        # 路径 1：WS 实时推送
        if desktop_ws.is_connected():
            ok = await desktop_ws.push_message(content)
            if ok:
                return
            logger.warning("[desktop_channel] WS push 失败，降级到文件")
        # 路径 2：文件队列 fallback
        await self._write_to_queue(content)

    async def _write_to_queue(self, content: str) -> None:
        try:
            async with _queue_lock:
                q_file = get_paths().channel_queue()
                q_file.parent.mkdir(parents=True, exist_ok=True)
                queue = []
                if q_file.exists():
                    queue = json.loads(q_file.read_text(encoding="utf-8"))
                queue.append({
                    "content": content,
                    "timestamp": time.time(),
                })
                q_file.write_text(
                    json.dumps(queue, ensure_ascii=False), encoding="utf-8"
                )
        except Exception as e:
            logger.warning(f"[desktop_channel] 写入队列失败: {e}")
