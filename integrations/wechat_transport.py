"""PresenceKit's independent WeChat transport boundary.

Implementations return normalized messages and delivery outcomes only. Third
party wire fields, URLs and authentication belong to implementations.
"""
from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class TransportMessage:
    message_id: str
    sender_id: str
    recipient_id: str
    conversation_id: str
    timestamp: float
    text: str
    kind: str = "text"
    is_group: bool = False


@dataclass(frozen=True)
class DeliveryResult:
    status: str  # accepted | rejected | unknown


class WechatTransport(Protocol):
    connected: bool

    def receive(self) -> AsyncIterator[TransportMessage]: ...
    async def send_text(self, address: str, text: str) -> DeliveryResult: ...
    async def close(self) -> None: ...
