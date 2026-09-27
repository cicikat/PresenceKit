"""Network routing for model requests. No endpoint or proxy address is embedded here."""

import asyncio
import ipaddress
import socket

import httpx

from core.config_loader import get_config


MODES = frozenset({"follow_global", "auto", "direct", "proxy"})
_FAKE_IP_RANGE = ipaddress.ip_network("198.18.0.0/15")


def normalize_model_connection_mode(value: object) -> str:
    mode = str(value or "follow_global").strip()
    return mode if mode in MODES else "follow_global"


def model_proxy_settings() -> tuple[str, str | None]:
    cfg = get_config().get("proxy", {}) or {}
    mode = normalize_model_connection_mode(cfg.get("model_connection_mode", "follow_global"))
    url = str(cfg.get("http") or "").strip() or None
    if mode == "direct" or (mode == "follow_global" and not cfg.get("enabled", False)):
        return mode, None
    return mode, url


async def _needs_proxy(host: str, port: int) -> bool:
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    except OSError:
        # A local DNS failure does not prevent the HTTP proxy from resolving the host.
        return True
    try:
        return any(ipaddress.ip_address(item[4][0]) in _FAKE_IP_RANGE for item in addresses)
    except ValueError:
        return True


class AutoModelTransport(httpx.AsyncBaseTransport):
    """Select a route for each request, so DNS changes do not require a restart."""

    def __init__(self, proxy_url: str):
        self._direct = httpx.AsyncHTTPTransport()
        self._proxy = httpx.AsyncHTTPTransport(proxy=proxy_url)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        route = self._proxy if await _needs_proxy(host, port) else self._direct
        return await route.handle_async_request(request)

    async def aclose(self) -> None:
        await self._direct.aclose()
        await self._proxy.aclose()


def make_model_http_client(proxy_url: str | None, timeout: httpx.Timeout) -> httpx.AsyncClient:
    mode, _ = model_proxy_settings()
    if mode == "auto" and proxy_url:
        return httpx.AsyncClient(transport=AutoModelTransport(proxy_url), trust_env=False, timeout=timeout)
    if proxy_url:
        return httpx.AsyncClient(proxy=proxy_url, trust_env=False, timeout=timeout)
    return httpx.AsyncClient(trust_env=False, timeout=timeout)
