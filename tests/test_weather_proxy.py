"""Weather HTTP calls honor tools.weather.use_proxy (default: direct)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.tools import weather as weather_mod


PROXY_URL = "http://127.0.0.1:1080"


def _session(monkeypatch, body: str = "杭州: 晴 20°C", status: int = 200):
    response = MagicMock()
    response.status = status
    response.text = AsyncMock(return_value=body)
    response.json = AsyncMock(return_value={
        "current_condition": [{
            "temp_C": "20",
            "FeelsLikeC": "19",
            "humidity": "50",
            "precipMM": "0",
            "cloudcover": "10",
            "windspeedKmph": "8",
            "lang_zh": [{"value": "晴"}],
            "is_day": "yes",
            "uvIndex": "3",
        }],
    })
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.get.return_value = request
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(weather_mod.aiohttp, "ClientSession", MagicMock(return_value=context))
    return session


def test_weather_use_proxy_defaults_false(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", lambda: {})
    assert weather_mod.weather_use_proxy() is False
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"tools": {"weather": {"enabled": True}}})
    assert weather_mod.weather_use_proxy() is False


def test_weather_use_proxy_reads_config(monkeypatch):
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"tools": {"weather": {"enabled": True, "use_proxy": True}}},
    )
    assert weather_mod.weather_use_proxy() is True


@pytest.mark.asyncio
async def test_get_weather_skips_global_proxy_by_default(monkeypatch):
    session = _session(monkeypatch)
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"tools": {"weather": {"enabled": True}}})
    monkeypatch.setattr(weather_mod, "get_aiohttp_proxy", lambda: PROXY_URL)
    result = await weather_mod.get_weather("杭州")
    assert result == "杭州: 晴 20°C"
    kwargs = session.get.call_args.kwargs
    assert kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_get_weather_uses_global_proxy_when_enabled(monkeypatch):
    session = _session(monkeypatch)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"tools": {"weather": {"enabled": True, "use_proxy": True}}},
    )
    monkeypatch.setattr(weather_mod, "get_aiohttp_proxy", lambda: PROXY_URL)
    result = await weather_mod.get_weather("杭州")
    assert result == "杭州: 晴 20°C"
    assert session.get.call_args.kwargs["proxy"] == PROXY_URL


@pytest.mark.asyncio
async def test_get_weather_detail_skips_proxy_by_default(monkeypatch):
    session = _session(monkeypatch)
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"tools": {"weather": {"enabled": True}}})
    monkeypatch.setattr(weather_mod, "get_aiohttp_proxy", lambda: PROXY_URL)
    detail = await weather_mod.get_weather_detail("杭州")
    assert detail["temp_c"] == 20
    assert detail["desc"] == "晴"
    assert session.get.call_args.kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_get_weather_detail_uses_proxy_when_enabled(monkeypatch):
    session = _session(monkeypatch)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"tools": {"weather": {"enabled": True, "use_proxy": True}}},
    )
    monkeypatch.setattr(weather_mod, "get_aiohttp_proxy", lambda: PROXY_URL)
    detail = await weather_mod.get_weather_detail("杭州")
    assert detail["temp_c"] == 20
    assert session.get.call_args.kwargs["proxy"] == PROXY_URL
