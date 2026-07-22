"""
channels/registry — 通道注册表，管理所有活跃通道。
"""

import logging
from channels.base import BaseChannel

logger = logging.getLogger(__name__)

_channels: dict[str, BaseChannel] = {}


def register(channel: BaseChannel) -> None:
    """注册一个通道。"""
    _channels[channel.name] = channel
    logger.info(f"[channel_registry] 注册通道: {channel.name}")


def get(name: str) -> BaseChannel | None:
    return _channels.get(name)


def get_active() -> list[BaseChannel]:
    """返回所有活跃通道。"""
    return [c for c in _channels.values() if c.is_active]


async def broadcast(
    content: str,
    user_id: str,
    behavior: dict | None = None,
    *,
    char_id: str | None = None,
    sticker: dict | None = None,
    exclude_channels: set[str] | None = None,
) -> dict[str, str]:
    """广播到所有活跃通道。返回失败通道到错误文本的映射。"""
    excluded = exclude_channels or set()
    active = [channel for channel in get_active() if channel.name not in excluded]
    if not active:
        logger.warning("[channel_registry] 无活跃通道，消息丢弃")
        return {}
    failures: dict[str, str] = {}
    for channel in active:
        try:
            kwargs = {"behavior": behavior}
            if char_id is not None:
                kwargs["char_id"] = char_id
            if sticker is not None:
                kwargs["sticker"] = sticker
            await channel.send(content, user_id, **kwargs)
        except Exception as e:
            failures[channel.name] = str(e)
            logger.warning(f"[channel_registry] 通道发送失败: {channel.name}: {e}")
    return failures
