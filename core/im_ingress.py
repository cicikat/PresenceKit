"""Transport-neutral IM admission and per-turn delivery contract.

Protocol decoding happens before this boundary. Business execution is injected
by the application owner; no adapter or network API is imported here.
"""
from __future__ import annotations

import time
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Awaitable, Callable

from core.write_envelope import WriteEnvelope

SendSegments = Callable[[str, list[str], bool], Awaitable[None]]


@dataclass(frozen=True)
class IMMessage:
    channel: str
    account_id: str
    external_sender_id: str
    canonical_uid: str
    conversation_id: str
    message_id: str
    timestamp: float
    text: str
    reply_address: str


@dataclass(frozen=True)
class IMContext:
    channel: str
    send_segments: SendSegments
    envelope: WriteEnvelope
    reply_address: str
    conversation_id: str
    allowed_tool_names: frozenset[str] | None = None
    lock_owned: bool = False


class IMIngress:
    """Bounded in-process redelivery suppression; no delivery replay queue.

    A receipt means admitted, not generated or delivered. Failed/unknown turns
    stay claimed until TTL expiry so redelivery cannot repeat side effects.
    """

    def __init__(self, handler, *, capacity: int = 2048, ttl: float = 86400):
        self.handler = handler
        self.capacity = capacity
        self.ttl = ttl
        self._seen: OrderedDict[tuple[str, ...], float] = OrderedDict()

    async def accept(self, message: IMMessage, context: IMContext) -> bool:
        if (not message.canonical_uid or not message.message_id
                or not message.text.strip() or not message.reply_address
                or message.channel != context.channel
                or message.reply_address != context.reply_address):
            return False
        now = time.monotonic()
        while self._seen and next(iter(self._seen.values())) <= now - self.ttl:
            self._seen.popitem(last=False)
        key = (message.channel, message.account_id, message.conversation_id, message.message_id)
        if key in self._seen:
            return False
        self._seen[key] = now
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)
        from core.conversation_gate import conversation_lock
        async with conversation_lock(message.canonical_uid):
            await self.handler({
                "user_id": message.canonical_uid,
                "content": message.text,
                "sender_name": message.canonical_uid,
                "timestamp": message.timestamp,
                "event_id": message.channel + ":" + hashlib.sha256(
                    json.dumps(key, ensure_ascii=False).encode("utf-8")
                ).hexdigest(),
            }, ingress=replace(context, lock_owned=True))
        return True
