"""Bounded HTTP adapter; no Minecraft implementation or process ownership here."""
from __future__ import annotations

import json
from typing import Protocol

import httpx


class BridgeError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Bridge(Protocol):
    async def request(self, method: str, path: str, payload: dict | None = None) -> dict: ...


class HttpBridge:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    async def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=4, trust_env=False, follow_redirects=False) as client:
                async with client.stream(method, self.url + path, json=payload,
                                         headers={"Authorization": f"Bearer {self.token}"}) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 65536:
                            raise BridgeError("bridge_response_too_large")
                    data = json.loads(raw)
                    if response.status_code != 200:
                        # Do not propagate arbitrary external exception text, URLs or tokens.
                        code = data.get("error", "bridge_rejected") if isinstance(data, dict) else "bridge_rejected"
                        raise BridgeError(code if isinstance(code, str) and code.replace("_", "").isalnum() and len(code) < 60 else "bridge_rejected")
                    if not isinstance(data, dict):
                        raise BridgeError("invalid_bridge_response")
                    return data
        except BridgeError:
            raise
        except (httpx.HTTPError, ValueError):
            raise BridgeError("bridge_unavailable") from None
