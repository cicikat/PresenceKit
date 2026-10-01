"""爱意探针 → 板子画爱心。gate + 冷却，fail-open。

探针（detect_affection）每条回复都可能被调用，且只有「判定为是并画成」才会进入冷却。
上游不稳时它会连续打空炮，所以这里加两道止损（工单 F）：
  * 失败退避：探针连续失败 n 次后，跳过接下来 min(60·2^(n-1), 900) 秒的调用；一次成功判定即清零。
  * 可选采样：embodiment.heart.sample_rate（0–1，默认 1.0 = 每轮都判，健康时行为不变）。
被跳过的次数进 runtime_signal_observability（model_quality / heart_probe_skipped）。
"""
from __future__ import annotations

import logging
import random
import time

from core import config_loader, llm_client

logger = logging.getLogger(__name__)

_LAST_SENT: dict[str, float] = {}   # char_id → epoch
_FAIL_STREAK: dict[str, int] = {}
_BACKOFF_UNTIL: dict[str, float] = {}
BACKOFF_BASE_SECONDS = 60.0
BACKOFF_MAX_SECONDS = 900.0
_random = random.random              # 测试可注入


def _sample_rate(cfg: dict) -> float:
    value = cfg.get("sample_rate", 1.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    return min(1.0, max(0.0, float(value)))


def _skipped(reason: str) -> None:
    try:
        from core.runtime_signal_observability import record
        record(category="model_quality", code="heart_probe_skipped", status="ok", context={"reason": reason})
    except Exception:  # noqa: BLE001 - observability only
        pass


async def maybe_draw_heart(reply: str, char_id: str) -> None:
    cfg = config_loader.get_config().get("embodiment", {}).get("heart", {})
    if not cfg.get("enabled", False):
        return
    cooldown = float(cfg.get("cooldown_sec", 45))
    now = time.time()
    if now - _LAST_SENT.get(char_id, 0.0) < cooldown:
        return
    if not reply or not reply.strip():
        return
    if now < _BACKOFF_UNTIL.get(char_id, 0.0):
        _skipped("backoff")
        return
    rate = _sample_rate(cfg)
    if rate < 1.0 and _random() >= rate:
        _skipped("sampled_out")
        return
    try:
        verdict = await llm_client.detect_affection_checked(reply)
        if verdict is None:
            streak = _FAIL_STREAK.get(char_id, 0) + 1
            _FAIL_STREAK[char_id] = streak
            _BACKOFF_UNTIL[char_id] = now + min(BACKOFF_BASE_SECONDS * 2 ** (streak - 1), BACKOFF_MAX_SECONDS)
            return
        _FAIL_STREAK.pop(char_id, None)
        _BACKOFF_UNTIL.pop(char_id, None)
        if not verdict:
            return
        from core.tool_dispatcher import _push_desktop_action
        result = await _push_desktop_action({
            "type": "show_heart",
            "duration_ms": int(cfg.get("duration_ms", 4000)),
        })
        if result == "ok":
            _LAST_SENT[char_id] = now
            logger.info("[heart] 爱意命中，设备已确认画爱心 char=%s", char_id)
        else:
            logger.debug("[heart] 爱意命中但设备动作未确认: %s", result)
    except Exception as e:
        logger.debug("[heart] skipped: %s", e)
