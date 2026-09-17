"""
channels/device — 设备通道（ESP32 等具身硬件）。

MVP：只走 WebSocket，不做文件兜底。WS 未连接时 send() 直接返回。
"""

import logging

from channels.base import BaseChannel

logger = logging.getLogger(__name__)


class DeviceChannel(BaseChannel):
    @property
    def name(self) -> str:
        return "device"

    @property
    def is_active(self) -> bool:
        from channels import device_ws
        return device_ws.is_connected()

    def set_active(self, active: bool) -> None:
        # 设备端无文件队列兜底，活跃状态完全由 WS 连接状态决定；这里只记日志。
        logger.info(f"[device_channel] WS 活跃状态: {active}")

    async def send(
        self,
        content: str,
        user_id: str,
        behavior: dict | None = None,
        msg_id: str | None = None,
        *,
        char_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        from channels import device_ws
        if not device_ws.is_connected():
            return
        push_kwargs = {"msg_id": msg_id}
        if char_id is not None:
            push_kwargs["char_id"] = char_id
        if request_id is not None:
            push_kwargs["request_id"] = request_id
        ok = await device_ws.push_message(content, **push_kwargs)
        if ok and behavior:
            action_ok, err = await device_ws.push_action_and_wait(behavior, timeout=5.0)
            if not action_ok:
                logger.warning(f"[device_channel] WS action 失败: {err}")
