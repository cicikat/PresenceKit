"""Model transport follows DNS changes without changing the global proxy policy."""

import socket

import httpx
import pytest

from core import model_network


def _address(ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 443))]


@pytest.mark.asyncio
async def test_auto_route_rechecks_dns_on_every_request(monkeypatch):
    selected = []

    class FakeTransport:
        def __init__(self, label):
            self.label = label

        async def handle_async_request(self, request):
            selected.append(self.label)
            return httpx.Response(200, request=request)

        async def aclose(self):
            pass

    answers = iter([_address("198.18.0.4"), _address("203.0.113.8")])
    monkeypatch.setattr(model_network.socket, "getaddrinfo", lambda *a, **kw: next(answers))
    transport = model_network.AutoModelTransport("http://localhost:7897")
    transport._direct = FakeTransport("direct")
    transport._proxy = FakeTransport("proxy")
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        await client.get("https://model.example/v1/models")
        await client.get("https://model.example/v1/models")
    assert selected == ["proxy", "direct"]


@pytest.mark.asyncio
async def test_auto_route_uses_proxy_when_dns_fails(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("unavailable")

    monkeypatch.setattr(model_network.socket, "getaddrinfo", fail)
    assert await model_network._needs_proxy("model.example", 443) is True


@pytest.mark.asyncio
async def test_auto_route_uses_direct_for_public_address(monkeypatch):
    monkeypatch.setattr(model_network.socket, "getaddrinfo", lambda *a, **kw: _address("203.0.113.8"))
    assert await model_network._needs_proxy("model.example", 443) is False


@pytest.mark.asyncio
async def test_auto_route_uses_proxy_when_any_answer_is_fake_ip(monkeypatch):
    mixed = _address("203.0.113.8") + _address("198.18.1.1")
    monkeypatch.setattr(model_network.socket, "getaddrinfo", lambda *a, **kw: mixed)
    assert await model_network._needs_proxy("model.example", 443) is True


@pytest.mark.asyncio
async def test_auto_route_uses_proxy_when_address_is_unparseable(monkeypatch):
    monkeypatch.setattr(
        model_network.socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 443))],
    )
    assert await model_network._needs_proxy("model.example", 443) is True


@pytest.mark.parametrize("mode,enabled,expected", [
    (None, False, None),
    (None, True, "http://localhost:7897"),
    ("direct", True, None),
    ("proxy", False, "http://localhost:7897"),
    ("auto", False, "http://localhost:7897"),
    ("auto", True, "http://localhost:7897"),
    ("unknown", True, "http://localhost:7897"),
])
def test_model_mode_keeps_legacy_default(monkeypatch, mode, enabled, expected):
    cfg = {"enabled": enabled, "http": "http://localhost:7897"}
    if mode is not None:
        cfg["model_connection_mode"] = mode
    monkeypatch.setattr(model_network, "get_config", lambda: {"proxy": cfg})
    assert model_network.model_proxy_settings()[1] == expected


def test_model_mode_auto_without_proxy_url_stays_direct(monkeypatch):
    monkeypatch.setattr(
        model_network,
        "get_config",
        lambda: {"proxy": {"enabled": False, "model_connection_mode": "auto", "http": ""}},
    )
    mode, url = model_network.model_proxy_settings()
    assert mode == "auto"
    assert url is None
    client = model_network.make_model_http_client(url, httpx.Timeout(1.0))
    assert not isinstance(client._transport, model_network.AutoModelTransport)


def test_make_model_http_client_uses_auto_transport(monkeypatch):
    monkeypatch.setattr(
        model_network,
        "get_config",
        lambda: {"proxy": {"enabled": False, "model_connection_mode": "auto", "http": "http://localhost:7897"}},
    )
    client = model_network.make_model_http_client("http://localhost:7897", httpx.Timeout(1.0))
    assert isinstance(client._transport, model_network.AutoModelTransport)


def test_make_model_http_client_follow_global_uses_plain_proxy(monkeypatch):
    monkeypatch.setattr(
        model_network,
        "get_config",
        lambda: {"proxy": {"enabled": True, "http": "http://localhost:7897"}},
    )
    client = model_network.make_model_http_client("http://localhost:7897", httpx.Timeout(1.0))
    assert not isinstance(client._transport, model_network.AutoModelTransport)


def test_normalize_unknown_mode_falls_back_to_follow_global():
    assert model_network.normalize_model_connection_mode(None) == "follow_global"
    assert model_network.normalize_model_connection_mode("bogus") == "follow_global"
    assert model_network.normalize_model_connection_mode("auto") == "auto"
