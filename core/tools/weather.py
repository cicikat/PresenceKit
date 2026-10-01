"""
天气查询工具
调用 wttr.in 免费天气 API，返回城市当前天气文本。

wttr.in 是不可控的第三方免费服务（证书过期、连接失败、超时都发生过）。这类网络/证书
故障不是本系统的错误：降为聚合 WARNING + api_call_log，不再灌进 error.log；成功结果在
内存里缓存，失败时回带「多久前」标注的缓存值，让模型知道那不是实时天气。解析异常等
真正的代码问题仍走 log_error。
"""

import asyncio
import logging
import time

import aiohttp

from core.error_handler import log_error
from core.proxy_config import get_aiohttp_proxy

logger = logging.getLogger(__name__)

# 天气数据 30–60 分钟量级内仍有参考价值；纯内存缓存（重启清零即可，没有落盘价值）。
CACHE_TTL_SECONDS = 45 * 60
_NETWORK_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, OSError)
_cache: dict[str, tuple[float, str]] = {}


def weather_use_proxy() -> bool:
    """Whether weather HTTP calls should inherit the global proxy.

    Default is direct (False): wttr.in often fails through a local ladder.
    Admin can turn the proxy back on via tools.weather.use_proxy.
    """
    from core.config_loader import get_config

    block = get_config().get("tools", {}).get("weather", {})
    if isinstance(block, dict):
        return bool(block.get("use_proxy", False))
    return False


def _weather_proxy() -> str | None:
    return get_aiohttp_proxy() if weather_use_proxy() else None


def _age_label(age_seconds: float) -> str:
    minutes = int(age_seconds // 60)
    if minutes < 1:
        return "不到 1 分钟前"
    if minutes < 90:
        return f"约 {minutes} 分钟前"
    return f"约 {round(minutes / 60)} 小时前"


def _cached_with_note(city: str) -> str | None:
    """Last good text for ``city`` while still inside the TTL, labelled with its age."""
    entry = _cache.get(city.strip().lower())
    if entry is None:
        return None
    age = time.time() - entry[0]
    if age > CACHE_TTL_SECONDS:
        return None
    return f"{entry[1]}（这是{_age_label(age)}的缓存天气，实时查询暂时失败，不是此刻的实况）"


def _note_upstream_failure(purpose: str, error: BaseException | str, started: float) -> None:
    """Keep the fact queryable (api_call_log + counters) without an ERROR traceback."""
    kind = error if isinstance(error, str) else type(error).__name__
    try:
        from core.api_call_log import append
        append(caller="weather", purpose=purpose, provider="wttr.in", model="",
               duration_ms=int((time.monotonic() - started) * 1000), ok=False,
               output_hint=kind, error_category="upstream_unavailable")
        from core.runtime_signal_observability import record_and_should_log
        if record_and_should_log(category="third_party_upstream", code="weather_unavailable",
                                 context={"purpose": purpose, "reason": kind[:60]}):
            logger.warning("[weather] wttr.in 暂不可用（第三方服务，已降级）: purpose=%s reason=%s", purpose, kind)
    except Exception:  # noqa: BLE001 - observability must not break the tool
        pass


async def get_weather(city: str) -> str:
    """查询指定城市的当前天气，返回一行天气描述文本"""
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed("weather")
    url = f"https://wttr.in/{city}?format=3&lang=zh"
    proxy = _weather_proxy()
    started = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
                proxy=proxy,
            ) as resp:
                if resp.status == 200:
                    text = (await resp.text()).strip()
                    if text:
                        _cache[city.strip().lower()] = (time.time(), text)
                    return text
                _note_upstream_failure("weather", f"HTTP{resp.status}", started)
                return _cached_with_note(city) or f"获取天气失败，HTTP {resp.status}"
    except asyncio.TimeoutError as e:
        _note_upstream_failure("weather", e, started)
        return _cached_with_note(city) or "天气查询超时，请稍后再试"
    except _NETWORK_ERRORS as e:
        _note_upstream_failure("weather", e, started)
        return _cached_with_note(city) or "天气查询出错"
    except Exception as e:
        log_error("tool.weather", e)
        return "天气查询出错"


async def get_weather_detail(city: str) -> dict:
    """
    查询详细天气数据，返回结构化字典。
    返回格式：
    {
        "temp_c": int,          # 当前温度
        "feels_like": int,      # 体感温度
        "humidity": int,        # 湿度%
        "precip_mm": float,     # 降水量mm
        "cloud_cover": int,     # 云量%
        "wind_kmph": int,       # 风速km/h
        "desc": str,            # 天气描述（中文）
        "is_day": bool,         # 是否白天
        "uv_index": int,        # 紫外线指数
    }
    失败时返回空字典（调用方 scheduler 自己保留上一次成功值，这里不再叠加缓存）。
    """
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed("weather")
    url = f"https://wttr.in/{city}?format=j1&lang=zh"
    proxy = _weather_proxy()
    started = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
                proxy=proxy,
            ) as resp:
                if resp.status != 200:
                    _note_upstream_failure("weather_detail", f"HTTP{resp.status}", started)
                    return {}
                data = await resp.json(content_type=None)

        current = data["current_condition"][0]
        desc_list = current.get("lang_zh", current.get("weatherDesc", [{}]))
        desc = desc_list[0].get("value", "") if desc_list else ""

        return {
            "temp_c":      int(current.get("temp_C", 0)),
            "feels_like":  int(current.get("FeelsLikeC", 0)),
            "humidity":    int(current.get("humidity", 0)),
            "precip_mm":   float(current.get("precipMM", 0)),
            "cloud_cover": int(current.get("cloudcover", 0)),
            "wind_kmph":   int(current.get("windspeedKmph", 0)),
            "desc":        desc,
            "is_day":      current.get("is_day", "yes") == "yes",
            "uv_index":    int(current.get("uvIndex", 0)),
        }
    except _NETWORK_ERRORS as e:
        _note_upstream_failure("weather_detail", e, started)
        return {}
    except Exception as e:
        log_error("tool.weather.detail", e)
        return {}
