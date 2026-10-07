"""WeChatPadPro reference REST/WS profile (public 849 API family).

Reference: naiveclub/wechatpadpro, commit
1920f14a9f25eef0e8eac696e1160c522ca6dba, static/swagger/swagger.json.
Wire compatibility must be checked against the deployed bridge version.
"""
from __future__ import annotations

import asyncio
import json
import math
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from integrations.wechat_transport import DeliveryResult, TransportMessage


def _string(value) -> str:
    if isinstance(value, dict):
        value = value.get("string", value.get("String", ""))
    return value if isinstance(value, str) else ""


def decode_messages(frame: object) -> list[TransportMessage]:
    """Decode the sync envelope; ignore status, contact and unknown frames.

    Accept AddMsgs batches and individual sync records. No raw record crosses
    the transport boundary. Self/group/media filtering is enforced by ingress.
    """
    if not isinstance(frame, dict):
        return []
    data = frame.get("Data", frame.get("data", frame))
    if isinstance(data, dict):
        items = data.get("AddMsgs", data.get("addMsgs"))
        if items is None:
            items = [data] if "MsgType" in data else []
    else:
        items = data if isinstance(data, list) else []
    result = []
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        sender = _string(item.get("FromUserName"))
        recipient = _string(item.get("ToUserName"))
        msg_id = item.get("NewMsgId") or item.get("MsgId")
        stamp = item.get("CreateTime")
        if (not sender or not recipient or not msg_id
                or isinstance(msg_id, bool) or not isinstance(msg_id, (str, int))
                or isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                or not math.isfinite(stamp) or stamp <= 0):
            continue
        text = _string(item.get("Content"))
        group = sender.endswith("@chatroom") or recipient.endswith("@chatroom")
        result.append(TransportMessage(
            str(msg_id), sender, recipient, sender, float(stamp), text,
            "text" if item.get("MsgType") == 1 else "unsupported", group,
        ))
    return result


def classify_delivery(response: object) -> DeliveryResult:
    # An HTTP 200 or a generic success envelope is not enough: require the
    # per-message protocol acknowledgement before declaring acceptance.
    if not isinstance(response, dict):
        return DeliveryResult("unknown")
    code = response.get("Code")
    if isinstance(code, int) and not isinstance(code, bool) and code not in (0, 200):
        return DeliveryResult("rejected")
    data = response.get("Data")
    items = data if isinstance(data, list) else (data or {}).get("List", []) if isinstance(data, dict) else []
    if len(items) != 1 or not isinstance(items[0], dict):
        return DeliveryResult("unknown")
    ret = items[0].get("Ret")
    if not isinstance(ret, int) or isinstance(ret, bool):
        return DeliveryResult("unknown")
    return DeliveryResult("accepted" if ret == 0 else "rejected")


class PadProTransport:
    def __init__(self, base_url: str, token: str, *, session=None):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._session = session
        self._owns_session = session is None
        self._ws = None
        self.connected = False

    def _client(self):
        if self._session is None:
            self._session = aiohttp.ClientSession(
                trust_env=False, timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def receive(self):
        from core.no_outbound import assert_outbound_allowed
        assert_outbound_allowed("wechat_receive")
        parsed = urlsplit(self.base_url)
        url = urlunsplit(("wss" if parsed.scheme == "https" else "ws",
                         parsed.netloc, parsed.path + "/ws/GetSyncMsg", "", ""))
        try:
            async with self._client().ws_connect(
                url, params={"key": self._token}, heartbeat=20,
                max_msg_size=1024 * 1024,
            ) as ws:
                self._ws = ws
                self.connected = True
                async for frame in ws:
                    if frame.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(frame.data)
                        except (ValueError, TypeError):
                            continue
                        for message in decode_messages(data):
                            yield message
                    elif frame.type == aiohttp.WSMsgType.ERROR:
                        raise ConnectionError("transport_receive_failed")
        finally:
            self.connected = False
            self._ws = None

    async def send_text(self, address: str, text: str) -> DeliveryResult:
        from core.no_outbound import assert_outbound_allowed
        assert_outbound_allowed("wechat_send")
        if not self.connected:
            return DeliveryResult("rejected")
        try:
            async with self._client().post(
                self.base_url + "/message/SendTextMessage",
                params={"key": self._token}, allow_redirects=False,
                json={"MsgItem": [{"ToUserName": address, "TextContent": text,
                                    "MsgType": 1, "AtWxIDList": []}]},
            ) as response:
                if response.status != 200:
                    return DeliveryResult("unknown" if response.status >= 500 else "rejected")
                return classify_delivery(await response.json())
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            # Never expose exception strings: aiohttp embeds credential URLs.
            return DeliveryResult("unknown")

    async def close(self):
        self.connected = False
        if self._ws is not None:
            await self._ws.close()
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None
