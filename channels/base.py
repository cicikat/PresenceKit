"""
channels/base — 通道基类。
所有输出通道继承此类，实现send方法。
"""

from abc import ABC, abstractmethod


class BaseChannel(ABC):
    """输出通道基类。"""

    @abstractmethod
    async def send(
        self,
        content: str,
        user_id: str,
        behavior: dict | None = None,
        *,
        char_id: str | None = None,
        sticker: dict | None = None,
    ) -> None:
        """发送消息到此通道。"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """通道名称。"""
        pass

    @property
    def is_active(self) -> bool:
        """通道是否活跃，默认True。"""
        return True
