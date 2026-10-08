"""Authenticated local bridge for Tencent's openclaw-weixin API modules."""
import asyncio
import math

import aiohttp

from integrations.wechat_transport import DeliveryResult, TransportMessage


def decode_messages(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return []
    events = []
    for item in payload["events"][:64]:
        if not isinstance(item, dict):
            continue
        required = ("message_id", "sender_id", "recipient_id", "conversation_id", "text")
        if any(not isinstance(item.get(key), str) or not item[key] for key in required):
            continue
        stamp = item.get("timestamp")
        if (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                or not math.isfinite(stamp) or stamp <= 0):
            continue
        if item.get("kind") != "text" or item.get("is_group") is not False:
            continue
        if len(item["text"]) > 16000:
            continue
        events.append(TransportMessage(**{key: item[key] for key in required}, timestamp=stamp))
    return events


class OpenClawWeixinTransport:
    def __init__(self, base_url, token, *, session=None):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._session = session
        self._owns_session = session is None
        self.connected = False
        self._closed = False

    def _client(self):
        if self._session is None:
            self._session = aiohttp.ClientSession(
                trust_env=False, timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    def _headers(self):
        return {"Authorization": "Bearer " + self._token}

    async def receive(self):
        from core.no_outbound import assert_outbound_allowed
        assert_outbound_allowed("wechat_receive")
        try:
            while not self._closed:
                async with self._client().get(
                    self.base_url + "/events", headers=self._headers(), allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise ConnectionError("transport_receive_failed")
                    payload = await response.json()
                    self.connected = isinstance(payload, dict) and payload.get("connected") is True
                    if not self.connected:
                        raise ConnectionError("transport_not_connected")
                    for event in decode_messages(payload):
                        yield event
                await asyncio.sleep(0.5)
        finally:
            self.connected = False

    async def send_text(self, address, text):
        from core.no_outbound import assert_outbound_allowed
        assert_outbound_allowed("wechat_send")
        if not self.connected or self._closed:
            return DeliveryResult("rejected")
        try:
            async with self._client().post(
                self.base_url + "/send", headers=self._headers(), allow_redirects=False,
                json={"address": address, "text": text},
            ) as response:
                if response.status != 200:
                    return DeliveryResult("unknown" if response.status >= 500 else "rejected")
                result = await response.json()
                status = result.get("status") if isinstance(result, dict) else None
                return DeliveryResult(status if status in {"accepted", "rejected", "unknown"} else "unknown")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return DeliveryResult("unknown")

    async def close(self):
        self._closed = True
        self.connected = False
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None
