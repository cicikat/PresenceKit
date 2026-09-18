"""
天气查询工具
调用 wttr.in 免费天气 API，返回城市当前天气文本。
"""

import asyncio

import aiohttp

from core.error_handler import log_error
from core.proxy_config import get_aiohttp_proxy


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


async def get_weather(city: str) -> str:
    """查询指定城市的当前天气，返回一行天气描述文本"""
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed("weather")
    url = f"https://wttr.in/{city}?format=3&lang=zh"
    proxy = _weather_proxy()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
                proxy=proxy,
            ) as resp:
                if resp.status == 200:
                    return (await resp.text()).strip()
                return f"获取天气失败，HTTP {resp.status}"
    except asyncio.TimeoutError:
        return "天气查询超时，请稍后再试"
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
    失败时返回空字典。
    """
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed("weather")
    url = f"https://wttr.in/{city}?format=j1&lang=zh"
    proxy = _weather_proxy()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
                proxy=proxy,
            ) as resp:
                if resp.status != 200:
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
    except Exception as e:
        log_error("tool.weather.detail", e)
        return {}
