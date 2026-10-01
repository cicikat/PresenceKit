"""工单 F：外部依赖与轻量判定失败降噪——降级但不静默，健康路径行为不变。"""
from __future__ import annotations

import asyncio
import logging
import types
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from core import runtime_signal_observability as obs
# Import everything that binds ``get_config`` at module level BEFORE any test patches it:
# a module first imported while ``core.config_loader.get_config`` is patched would keep the
# patched lambda for the rest of the session and leak into unrelated tests.
from core import llm_client  # noqa: F401
from core import tool_dispatcher  # noqa: F401
from core.embodiment import heart as _heart_mod  # noqa: F401
from core.scheduler import sensor_judge as _sensor_judge_mod  # noqa: F401
from core.tools import weather as weather_mod


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    obs._reset_for_tests()
    weather_mod._cache.clear()
    monkeypatch.setattr(weather_mod, "_weather_proxy", lambda: None)   # no config / proxy lookup
    yield
    obs._reset_for_tests()
    weather_mod._cache.clear()


def _signals():
    return {(s["category"], s["code"]): s["count"] for s in obs.snapshot()["signals"]}


# ── record_and_should_log ──────────────────────────────────────────────────────

def test_record_and_should_log_counts_all_but_logs_first_and_every_nth():
    flags = [obs.record_and_should_log(category="c", code="x", context={"reason": "t"}, every=5) for _ in range(11)]
    assert [i + 1 for i, flag in enumerate(flags) if flag] == [1, 5, 10]
    assert _signals()[("c", "x")] == 11
    # 另一个 context 重新从首条开始记
    assert obs.record_and_should_log(category="c", code="x", context={"reason": "other"}, every=5) is True


# ── F1 weather ─────────────────────────────────────────────────────────────────

def _fake_session(monkeypatch, *, text="杭州: 晴 20°C", status=200, error=None, json_body=None):
    response = MagicMock()
    response.status = status
    response.text = AsyncMock(return_value=text)
    response.json = AsyncMock(return_value=json_body)
    request = MagicMock()
    if error is not None:
        request.__aenter__ = AsyncMock(side_effect=error)
    else:
        request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = request
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(weather_mod.aiohttp, "ClientSession", MagicMock(return_value=context))


@pytest.fixture
def ledgers(monkeypatch):
    """Capture error.log writes (log_error) and api_call_log rows."""
    seen = types.SimpleNamespace(errors=[], api=[])
    monkeypatch.setattr(weather_mod, "log_error", lambda module, exc: seen.errors.append((module, exc)))
    monkeypatch.setattr("core.api_call_log.append", lambda **kw: seen.api.append(kw))
    return seen


@pytest.mark.asyncio
async def test_weather_cert_failure_is_not_an_error_log_entry_but_stays_observable(monkeypatch, ledgers, caplog):
    cert = aiohttp.ClientConnectorError(MagicMock(), OSError("certificate has expired"))
    _fake_session(monkeypatch, error=cert)
    with caplog.at_level(logging.WARNING):
        result = await weather_mod.get_weather("杭州")
    assert result == "天气查询出错"                                  # 既有 fallback 文案不变
    assert ledgers.errors == []                                      # 不再进 error.log
    assert ledgers.api[0]["caller"] == "weather" and ledgers.api[0]["ok"] is False
    assert ledgers.api[0]["error_category"] == "upstream_unavailable"
    assert _signals()[("third_party_upstream", "weather_unavailable")] == 1
    assert "wttr.in 暂不可用" in caplog.text


@pytest.mark.asyncio
async def test_weather_failure_streak_logs_once_then_every_20th(monkeypatch, ledgers, caplog):
    _fake_session(monkeypatch, error=aiohttp.ClientConnectionError("down"))
    with caplog.at_level(logging.WARNING):
        for _ in range(21):
            await weather_mod.get_weather("杭州")
    assert caplog.text.count("wttr.in 暂不可用") == 2                # 第 1 条 + 第 20 条
    assert _signals()[("third_party_upstream", "weather_unavailable")] == 21
    assert len(ledgers.api) == 21                                    # 每次失败仍进 api_call_log


@pytest.mark.asyncio
async def test_weather_returns_labelled_cache_when_upstream_fails(monkeypatch, ledgers):
    _fake_session(monkeypatch, text="杭州: ☀️ +20°C")
    assert await weather_mod.get_weather("杭州") == "杭州: ☀️ +20°C"
    # 25 分钟前的成功结果
    ts, text = weather_mod._cache["杭州"]
    weather_mod._cache["杭州"] = (ts - 25 * 60, text)
    _fake_session(monkeypatch, error=asyncio.TimeoutError())
    result = await weather_mod.get_weather("杭州")
    assert result.startswith("杭州: ☀️ +20°C")
    assert "约 25 分钟前" in result and "缓存" in result and "不是此刻的实况" in result
    _fake_session(monkeypatch, status=503)
    assert "约 25 分钟前" in await weather_mod.get_weather("杭州")   # HTTP 失败同样回缓存


@pytest.mark.asyncio
async def test_weather_expired_or_missing_cache_uses_the_existing_fallbacks(monkeypatch, ledgers):
    _fake_session(monkeypatch, error=asyncio.TimeoutError())
    assert await weather_mod.get_weather("杭州") == "天气查询超时，请稍后再试"
    weather_mod._cache["杭州"] = (weather_mod.time.time() - weather_mod.CACHE_TTL_SECONDS - 1, "旧数据")
    assert await weather_mod.get_weather("杭州") == "天气查询超时，请稍后再试"
    _fake_session(monkeypatch, status=500)
    assert await weather_mod.get_weather("北京") == "获取天气失败，HTTP 500"


@pytest.mark.asyncio
async def test_weather_real_bugs_still_reach_error_log(monkeypatch, ledgers):
    _fake_session(monkeypatch, error=ValueError("parser bug"))
    assert await weather_mod.get_weather("杭州") == "天气查询出错"
    assert [m for m, _ in ledgers.errors] == ["tool.weather"]
    _fake_session(monkeypatch, json_body={"unexpected": "shape"})
    assert await weather_mod.get_weather_detail("杭州") == {}
    assert [m for m, _ in ledgers.errors] == ["tool.weather", "tool.weather.detail"]


@pytest.mark.asyncio
async def test_weather_detail_network_failure_keeps_empty_dict_contract_quietly(monkeypatch, ledgers):
    _fake_session(monkeypatch, error=aiohttp.ClientConnectionError("down"))
    assert await weather_mod.get_weather_detail("杭州") == {}
    assert ledgers.errors == [] and ledgers.api[0]["purpose"] == "weather_detail"


# ── F2 detect_affection ────────────────────────────────────────────────────────

def _affection_env(monkeypatch, *, outcome=None, raises=None):
    from core import llm_client
    mc = types.SimpleNamespace(name="cheap-preset")
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat: mc)
    errors = []
    monkeypatch.setattr(llm_client, "log_error", lambda module, exc: errors.append(module))

    async def fake_execute(**_kwargs):
        if raises is not None:
            raise raises
        return outcome

    monkeypatch.setattr(llm_client, "execute_create", fake_execute)
    return llm_client, errors


def _outcome(*, ok, text="", skip_reason="", error_category="", error=None):
    return types.SimpleNamespace(ok=ok, skip_reason=skip_reason, error_category=error_category, error=error,
                                 value=types.SimpleNamespace(assistant_text=text))


@pytest.mark.asyncio
async def test_detect_affection_judges_yes_and_no_normally(monkeypatch):
    llm, errors = _affection_env(monkeypatch, outcome=_outcome(ok=True, text="Yes"))
    assert await llm.detect_affection_checked("我爱你") is True and await llm.detect_affection("我爱你") is True
    llm, _ = _affection_env(monkeypatch, outcome=_outcome(ok=True, text="no"))
    assert await llm.detect_affection_checked("今天下雨") is False
    assert errors == [] and _signals() == {}


@pytest.mark.asyncio
async def test_detect_affection_failure_is_counted_not_an_error_log_entry(monkeypatch, caplog):
    llm, errors = _affection_env(monkeypatch, outcome=_outcome(ok=False, skip_reason="breaker_open"))
    with caplog.at_level(logging.WARNING):
        for _ in range(21):
            assert await llm.detect_affection_checked("x") is None
    assert errors == []                                                # 不再写 error.log
    assert _signals()[("model_quality", "probe_failed")] == 21        # 但每次都计数
    assert caplog.text.count("probe failed") == 2                      # 首条 + 第 20 条
    assert "reason=breaker_open" in caplog.text and "preset=cheap-preset" in caplog.text
    assert await llm.detect_affection("x") is False                    # 旧接口仍 fail-open 返回 False


@pytest.mark.asyncio
async def test_detect_affection_exception_is_classified_by_type_only(monkeypatch, caplog):
    llm, errors = _affection_env(monkeypatch, raises=asyncio.TimeoutError("secret-detail"))
    with caplog.at_level(logging.WARNING):
        assert await llm.detect_affection_checked("x") is None
    assert errors == [] and "secret-detail" not in caplog.text and "TimeoutError" in caplog.text


# ── F2 heart: backoff + sampling ───────────────────────────────────────────────

@pytest.fixture
def heart(monkeypatch):
    from core.embodiment import heart as heart_mod
    for table in (heart_mod._LAST_SENT, heart_mod._FAIL_STREAK, heart_mod._BACKOFF_UNTIL):
        table.clear()
    state = types.SimpleNamespace(calls=0, verdict=None, pushed=[], cfg={"enabled": True, "cooldown_sec": 45})

    async def checked(_reply):
        state.calls += 1
        return state.verdict

    async def push(action):
        state.pushed.append(action)
        return "ok"

    monkeypatch.setattr(heart_mod.llm_client, "detect_affection_checked", checked)
    monkeypatch.setattr("core.tool_dispatcher._push_desktop_action", push)
    monkeypatch.setattr(heart_mod.config_loader, "get_config", lambda: {"embodiment": {"heart": state.cfg}})
    return heart_mod, state


@pytest.mark.asyncio
async def test_heart_default_calls_the_probe_every_reply_when_healthy(heart):
    mod, state = heart
    state.verdict = False
    for _ in range(5):
        await mod.maybe_draw_heart("reply", "c")
    assert state.calls == 5                                            # 默认采样 1.0：健康时行为不变


@pytest.mark.asyncio
async def test_heart_backs_off_after_probe_failures_and_recovers(heart, monkeypatch):
    mod, state = heart
    state.verdict = None
    await mod.maybe_draw_heart("reply", "c")
    assert state.calls == 1 and mod._FAIL_STREAK["c"] == 1
    for _ in range(4):                                                 # 退避窗口内不再打探针
        await mod.maybe_draw_heart("reply", "c")
    assert state.calls == 1
    assert _signals()[("model_quality", "heart_probe_skipped")] == 4
    mod._BACKOFF_UNTIL["c"] = 0                                        # 窗口过后再试，仍失败 -> 窗口翻倍
    await mod.maybe_draw_heart("reply", "c")
    assert mod._FAIL_STREAK["c"] == 2
    assert mod._BACKOFF_UNTIL["c"] - mod.time.time() > mod.BACKOFF_BASE_SECONDS
    mod._BACKOFF_UNTIL["c"] = 0
    state.verdict = True                                               # 一次成功判定：清零并正常画爱心
    await mod.maybe_draw_heart("reply", "c")
    assert "c" not in mod._FAIL_STREAK and "c" not in mod._BACKOFF_UNTIL
    assert state.pushed and mod._LAST_SENT["c"]


def test_heart_backoff_is_capped():
    from core.embodiment import heart as mod
    assert min(mod.BACKOFF_BASE_SECONDS * 2 ** 10, mod.BACKOFF_MAX_SECONDS) == mod.BACKOFF_MAX_SECONDS


@pytest.mark.asyncio
async def test_heart_sample_rate_is_configurable_and_validated(heart):
    mod, state = heart
    state.verdict = False
    state.cfg["sample_rate"] = 0.5
    rolls = iter([0.1, 0.9, 0.2, 0.7])
    mod._random = lambda: next(rolls)
    try:
        for _ in range(4):
            await mod.maybe_draw_heart("reply", "c")
    finally:
        import random
        mod._random = random.random
    assert state.calls == 2 and _signals()[("model_quality", "heart_probe_skipped")] == 2
    for bad in ("high", True, None, -3, 7):
        state.cfg["sample_rate"] = bad
        assert 0.0 <= mod._sample_rate(state.cfg) <= 1.0
    state.cfg["sample_rate"] = "high"
    assert mod._sample_rate(state.cfg) == 1.0


# ── sensor_judge / event_edge_proposer log aggregation ─────────────────────────

def test_sensor_judge_failure_logs_are_aggregated_per_event_and_category(caplog):
    from core.scheduler import sensor_judge as sj
    with caplog.at_level(logging.WARNING):
        for _ in range(21):
            sj._log_judge_failure("keyboard_idle", "timeout")
        sj._log_judge_failure("app_switch", "timeout")                  # 不同 event 各记首条
    assert caplog.text.count("LLM 调用失败") == 3
    assert _signals()[("model_quality", "sensor_judge_failed")] == 22

