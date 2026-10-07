"""Owner binding and lifecycle for normalized WeChat transport events.

No third-party wire fields or API calls belong in this module. A process-local
bounded queue decouples socket reading from business turns; it is not durable.
"""
from __future__ import annotations

import asyncio
import math
import os
import time
from collections import Counter
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

from core.im_ingress import IMContext, IMIngress, IMMessage
from core.write_envelope import stamp_wechat
from integrations.wechat_transport import WechatTransport


class WechatSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool = False
    proactive_enabled: StrictBool = False
    transport: str = "wechatpadpro"
    base_url: str = ""
    account_id: str = ""
    owner_sender_id: str = ""

    @field_validator("transport")
    @classmethod
    def supported_transport(cls, value):
        if value != "wechatpadpro":
            raise ValueError("unsupported_transport")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        if not value:
            return value
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.query or url.fragment):
            raise ValueError("base_url_requires_http_without_credentials_or_query")
        return value.rstrip("/")

    @field_validator("account_id", "owner_sender_id")
    @classmethod
    def validate_identity(cls, value):
        if len(value) > 128 or any(c.isspace() for c in value) or "@chatroom" in value:
            raise ValueError("invalid_private_identity")
        return value


def load_settings() -> WechatSettings:
    from core.config_loader import get_config
    return WechatSettings.model_validate(get_config().get("wechat") or {})


class WechatService:
    def __init__(self, handler, *, factory=None):
        if factory is None:
            from integrations.wechat.factory import create_transport
            factory = create_transport
        self.factory = factory
        self.ingress = IMIngress(handler)
        self.transport: WechatTransport | None = None
        self.settings = WechatSettings()
        self._signature = None
        self._generation = 0
        self._receiver = None
        self._worker = None
        self._queue = asyncio.Queue(maxsize=64)
        self._apply_lock = asyncio.Lock()
        self.running = False
        self.blocking_reason = "runtime_not_started"
        self.last_error = ""
        self.counters = Counter()

    def snapshot(self):
        connected = bool(self.transport and self.transport.connected)
        state = ("invalid_configuration" if self.blocking_reason == "invalid_configuration" else
                 "disabled" if not self.settings.enabled else
                 self.blocking_reason or ("connected" if connected else "connecting"))
        return {"effective_state": state, "connected": connected,
                "proactive_effective": bool(connected and self.settings.enabled
                                             and self.settings.proactive_enabled),
                "last_error": self.last_error, "counters": dict(self.counters),
                "queue_depth": self._queue.qsize(), "runtime_started": self.running}

    async def apply(self):
        from core.config_loader import get_config
        async with self._apply_lock:
            try:
                settings = load_settings()
            except (ValueError, TypeError):
                self.blocking_reason = "invalid_configuration"
                await self._stop_receiver()
                self._signature = None
                return
            owner_uid = str(get_config().get("scheduler", {}).get("owner_id") or "")
            token = os.environ.get("WECHAT_TRANSPORT_TOKEN", "")
            signature = (settings.model_dump_json(), owner_uid, token)
            if signature == self._signature:
                return
            self._signature = signature
            self.settings = settings
            await self._stop_receiver()
            self.blocking_reason = ""
            self.last_error = ""
            if not settings.enabled:
                return
            if not self.running:
                self.blocking_reason = "runtime_not_started"
            elif not all((settings.base_url, settings.account_id, settings.owner_sender_id, owner_uid, token)):
                self.blocking_reason = "missing_connection_or_owner_binding"
            elif settings.account_id == settings.owner_sender_id:
                self.blocking_reason = "owner_binding_is_self"
            else:
                self.transport = self.factory(settings, token)
                self._receiver = asyncio.create_task(self._receive_loop(self.transport, self._generation, owner_uid))

    async def _stop_receiver(self):
        self._generation += 1
        if self._receiver:
            self._receiver.cancel()
            await asyncio.gather(self._receiver, return_exceptions=True)
            self._receiver = None
        if self.transport:
            await self.transport.close()
            self.transport = None

    async def _receive_loop(self, transport, generation, owner_uid):
        delay = 1
        while generation == self._generation:
            try:
                async for event in transport.receive():
                    self.counters["received"] += 1
                    try:
                        self._queue.put_nowait((generation, owner_uid, event))
                    except asyncio.QueueFull:
                        self.counters["queue_full"] += 1
                self.last_error = "connection_closed"
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never log third-party exception text or credential-bearing URLs.
                self.last_error = "connection_failed"
            self.counters["reconnects"] += 1
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def admit(self, event, *, generation, owner_uid):
        settings = self.settings
        if generation != self._generation or not settings.enabled:
            self.counters["stale_generation"] += 1
            return False
        # Confirm current server-side identity policy before any activity or LLM.
        if (event.is_group or event.kind != "text"
                or event.sender_id != settings.owner_sender_id
                or event.recipient_id != settings.account_id
                or not event.message_id or not event.text.strip()
                or len(event.text) > 16000
                or not math.isfinite(event.timestamp)
                or not -30 <= time.time() - event.timestamp <= 300):
            self.counters["rejected"] += 1
            return False
        address = event.sender_id
        async def send_segments(target, segments, is_group):
            if is_group or target != address:
                raise ValueError("invalid_reply_route")
            await self.send_segments(address, segments, generation=generation)
        context = IMContext("wechat", send_segments, stamp_wechat(), address, event.conversation_id)
        message = IMMessage("wechat", settings.account_id, event.sender_id, owner_uid,
                            event.conversation_id, event.message_id, event.timestamp, event.text, address)
        accepted = await self.ingress.accept(message, context)
        self.counters["accepted" if accepted else "duplicate_or_invalid"] += 1
        return accepted

    async def send_segments(self, address, segments, *, generation):
        for segment in segments:
            if not segment.strip():
                continue
            if (generation != self._generation or not self.settings.enabled
                    or not self.transport or not self.transport.connected):
                self.last_error = "delivery_unavailable"
                self.counters["send_rejected"] += 1
                raise ConnectionError("delivery_unavailable")
            result = await self.transport.send_text(address, segment)
            self.counters["send_" + result.status] += 1
            if result.status != "accepted":
                self.last_error = "delivery_" + result.status
                raise ConnectionError(self.last_error)

    async def _work_loop(self):
        while True:
            generation, owner_uid, event = await self._queue.get()
            try:
                await self.admit(event, generation=generation, owner_uid=owner_uid)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.counters["turn_failed"] += 1
                self.last_error = "turn_failed"
            finally:
                self._queue.task_done()

    async def run(self):
        self.running = True
        self._signature = None
        self._worker = asyncio.create_task(self._work_loop())
        try:
            while True:
                await self.apply()
                await asyncio.sleep(1)
        finally:
            self.running = False
            await self._stop_receiver()
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self.blocking_reason = "runtime_not_started"


_service: WechatService | None = None


def install_service(handler) -> WechatService:
    """Called only by the application startup owner."""
    global _service
    _service = WechatService(handler)
    return _service


def get_service() -> WechatService | None:
    return _service
