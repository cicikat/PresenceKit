"""channels/ui_push — 把交互式下行 fan 到所有已连的 UI 客户端（桌宠 + 设备）。"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import re

from channels import desktop_ws, device_ws

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT_RE = re.compile(r"[^\n。！？…]*[\n。！？…]+")

# asyncio.sleep() 的真实调度粒度在不同平台差异很大——Windows 默认定时器精度约 15ms，
# 远小于此值的请求会被系统拉长到最近的 tick（见 pseudo_stream_push 内注释）。
_MIN_PRACTICAL_SLEEP_S = 0.01

_PSEUDO_STREAM_DEFAULTS: dict = {
    "enabled": True,
    "block_min_chars": 2,
    "block_max_chars": 6,
    "interval_min_ms": 30,
    "interval_max_ms": 80,
    "max_duration_ms": 4000,
}


def any_connected() -> bool:
    return desktop_ws.is_connected() or device_ws.is_connected()


async def push_stream_start(msg_id, **kw):
    if desktop_ws.is_connected():
        await desktop_ws.push_stream_start(msg_id, **kw)
    if device_ws.is_connected():
        await device_ws.push_stream_start(msg_id, **kw)


async def push_stream_delta(msg_id, delta):
    if desktop_ws.is_connected():
        await desktop_ws.push_stream_delta(msg_id, delta)
    if device_ws.is_connected():
        await device_ws.push_stream_delta(msg_id, delta)


async def push_stream_end(msg_id):
    if desktop_ws.is_connected():
        await desktop_ws.push_stream_end(msg_id)
    if device_ws.is_connected():
        await device_ws.push_stream_end(msg_id)


async def push_segments(content, segments, **kw):
    if desktop_ws.is_connected():
        await desktop_ws.push_segments(content, segments, **kw)
    if device_ws.is_connected():
        await device_ws.push_segments(content, segments, **kw)


def _pseudo_stream_settings(profile: str) -> dict:
    """读取 config `pseudo_stream:` 节（含 profile 覆盖）；读取异常 fail-open 为默认值。"""
    settings = dict(_PSEUDO_STREAM_DEFAULTS)
    try:
        from core.config_loader import get_config

        cfg = get_config().get("pseudo_stream", {}) or {}
        settings.update({k: v for k, v in cfg.items() if k in _PSEUDO_STREAM_DEFAULTS})
        if profile and profile != "default":
            profile_cfg = (cfg.get("profiles", {}) or {}).get(profile, {}) or {}
            settings.update(
                {k: v for k, v in profile_cfg.items() if k in _PSEUDO_STREAM_DEFAULTS}
            )
    except Exception:
        logger.debug("[ui_push] pseudo_stream 配置读取失败，使用默认值", exc_info=True)
    return settings


def _split_sentence(sentence: str, block_min: int, block_max: int) -> list[str]:
    blocks: list[str] = []
    i = 0
    n = len(sentence)
    while i < n:
        size = random.randint(block_min, block_max)
        blocks.append(sentence[i : i + size])
        i += size
    return blocks


def _split_into_blocks(text: str, block_min: int, block_max: int) -> list[str]:
    """按标点/换行切句，句内切 block_min~block_max 字一块；拼接结果与原文本相同。"""
    block_min = max(1, int(block_min))
    block_max = max(block_min, int(block_max))
    blocks: list[str] = []
    pos = 0
    for match in _SENTENCE_SPLIT_RE.finditer(text):
        blocks.extend(_split_sentence(match.group(0), block_min, block_max))
        pos = match.end()
    if pos < len(text):
        blocks.extend(_split_sentence(text[pos:], block_min, block_max))
    return blocks


async def pseudo_stream_push(
    text: str,
    *,
    msg_id: str,
    char_id: str = "",
    round_id: str = "",
    domain: str = "",
    profile: str = "default",
) -> None:
    """服务器端伪流式打字机回放（Brief 84）。

    复用 1v1 的 message_stream_* 帧契约：push_stream_start → 按块 push_stream_delta →
    push_stream_end。调用方仍需照旧发送 canonical push_message（同一个 msg_id）完成
    替换，本函数不发 canonical 帧。

    fail-open：未连接、配置关闭、文本过短（切不出多块）或推送过程中任何异常，都
    直接返回，绝不影响调用方后续的整段消息发送。
    """
    if not text or not msg_id:
        return
    try:
        settings = _pseudo_stream_settings(profile)
        if not settings.get("enabled", True):
            return
        if not any_connected():
            return
        blocks = _split_into_blocks(
            text, settings["block_min_chars"], settings["block_max_chars"]
        )
        if len(blocks) <= 1:
            return

        interval_min = max(0.0, float(settings["interval_min_ms"]) / 1000.0)
        interval_max = max(interval_min, float(settings["interval_max_ms"]) / 1000.0)
        max_duration = max(0.0, float(settings["max_duration_ms"]) / 1000.0)
        expected_total = len(blocks) * (interval_min + interval_max) / 2
        if max_duration > 0 and expected_total > max_duration:
            scale = max_duration / expected_total
            interval_min *= scale
            interval_max *= scale

        # 缩放后单块间隔若低于系统能可靠调度的 sleep 粒度，就把多块合并进一次
        # delta+sleep，保证每次 sleep 不小于这个下限——总耗时目标不变（合并后块数
        # 减半、每次 sleep 时长对应翻倍），代价是超长文本末尾的分块效果变粗，可接受。
        group_size = 1
        if 0 < interval_max < _MIN_PRACTICAL_SLEEP_S:
            group_size = max(1, math.ceil(_MIN_PRACTICAL_SLEEP_S / interval_max))

        kw: dict = {}
        if char_id:
            kw["char_id"] = char_id
        if round_id:
            kw["round_id"] = round_id
        if domain:
            kw["domain"] = domain

        started = False
        try:
            await push_stream_start(msg_id, **kw)
            started = True
            for i in range(0, len(blocks), group_size):
                chunk = "".join(blocks[i : i + group_size])
                await push_stream_delta(msg_id, chunk)
                if interval_max > 0:
                    await asyncio.sleep(random.uniform(interval_min, interval_max) * group_size)
        finally:
            if started:
                await push_stream_end(msg_id)
    except Exception:
        logger.debug(
            "[ui_push] pseudo_stream_push 失败，退化为调用方整段推送", exc_info=True
        )
