"""Bounded primary→fallback executor contracts (Brief 261 A)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from core.llm_failover import (
    FAILOVER_ELIGIBLE,
    PreparedAttempt,
    classify_exception,
    clear_breakers,
    execute_create,
    execute_stream,
    reset_shutdown,
)


def _mc(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        provider_kind="openai",
        model=name,
        api_protocol="chat_completions",
        prompt_style="narrative",
        params={},
        tool_call_mode="function_calling",
        request_timeout_s=10.0,
    )


def _ok(text="ok"):
    return SimpleNamespace(assistant_text=text, tool_calls=[], continuation_items=[])


def _timeout(msg="timed out"):
    err = TimeoutError(msg)
    err.status_code = None
    return err


@pytest.fixture(autouse=True)
def _reset_failover(monkeypatch):
    clear_breakers()
    reset_shutdown()
    yield
    clear_breakers()
    reset_shutdown()


class TestClassifyException:
    def test_timeout_and_connection_are_eligible(self):
        category, reason = classify_exception(asyncio.TimeoutError())
        assert reason in FAILOVER_ELIGIBLE
        assert category == "timeout"
        connect = httpx.ConnectError("refused")
        category, reason = classify_exception(connect)
        assert reason == "connection_error"

    def test_plain_403_is_not_geo_blocked(self):
        err = RuntimeError("forbidden")
        err.status_code = 403
        category, reason = classify_exception(err)
        assert reason == ""
        assert category == "upstream_auth_failed"

    def test_explicit_geo_blocked_is_eligible(self):
        err = RuntimeError("not available in your region")
        err.status_code = 403
        category, reason = classify_exception(err)
        assert reason == "geo_blocked"

    def test_content_filter_is_not_eligible(self):
        err = RuntimeError("content_filter: safety system refused to respond")
        err.status_code = 400
        category, reason = classify_exception(err)
        assert reason == ""
        assert category == "content_filtered"

    def test_429_and_5xx_are_eligible(self):
        limited = RuntimeError("too many requests")
        limited.status_code = 429
        _, reason = classify_exception(limited)
        assert reason == "upstream_rate_limited"
        boom = RuntimeError("bad gateway")
        boom.status_code = 502
        _, reason = classify_exception(boom)
        assert reason == "upstream_unavailable"


@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback(monkeypatch):
    from core import llm_failover as fo

    primary = _mc("primary")
    fallback = _mc("fallback")
    seen = []

    async def create(mc, messages, **kwargs):
        seen.append(mc.name)
        return _ok(mc.name)

    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(fo, "get_model_client", lambda *a, **k: fallback if k.get("failover") else primary)
    monkeypatch.setattr(
        fo,
        "resolve_fallback_client",
        lambda *a, **k: (fallback, ""),
    )
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))

    outcome = await execute_create(
        call_category="chat",
        prepare=lambda mc: PreparedAttempt(messages=[{"role": "user", "content": "hi"}]),
        caller="test",
        primary_mc=primary,
    )
    assert outcome.ok is True
    assert outcome.preset == "primary"
    assert seen == ["primary"]
    assert [r["route_role"] for r in rows] == ["primary"]
    assert rows[0]["ok"] is True
    assert rows[0]["logical_final"] is True


@pytest.mark.asyncio
async def test_primary_timeout_fallback_success_keeps_primary_failure(monkeypatch):
    from core import llm_failover as fo

    primary = _mc("primary")
    fallback = _mc("fallback")
    seen = []

    async def create(mc, messages, **kwargs):
        seen.append(mc.name)
        if mc.name == "primary":
            raise _timeout()
        return _ok("recovered")

    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))

    outcome = await execute_create(
        call_category="chat",
        prepare=lambda mc: PreparedAttempt(messages=[{"role": "user", "content": "hi"}]),
        caller="test",
        primary_mc=primary,
    )
    assert outcome.ok is True
    assert outcome.switched is True
    assert outcome.preset == "fallback"
    assert seen == ["primary", "fallback"]
    assert [r["ok"] for r in rows] == [False, True]
    assert rows[0]["route_role"] == "primary"
    assert rows[1]["route_role"] == "fallback"
    assert rows[1]["switch_reason"] == "timeout"
    assert rows[1]["sdk_retry_policy"] == "zero"


@pytest.mark.asyncio
async def test_auth_failure_does_not_switch(monkeypatch):
    from core import llm_failover as fo

    primary = _mc("primary")
    fallback = _mc("fallback")
    seen = []

    async def create(mc, messages, **kwargs):
        seen.append(mc.name)
        err = RuntimeError("unauthorized")
        err.status_code = 401
        raise err

    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))

    outcome = await execute_create(
        call_category="chat",
        prepare=lambda mc: PreparedAttempt(messages=[{"role": "user", "content": "hi"}]),
        caller="test",
        primary_mc=primary,
    )
    assert outcome.ok is False
    assert seen == ["primary"]
    skip_rows = [r for r in rows if r.get("skip_reason") == "not_eligible"]
    assert skip_rows


@pytest.mark.asyncio
async def test_explicit_preset_skips_profile_fallback(monkeypatch):
    from core import llm_failover as fo

    primary = _mc("primary")
    fallback = _mc("fallback")
    seen = []

    async def create(mc, messages, **kwargs):
        seen.append(mc.name)
        raise _timeout()

    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(
        fo,
        "resolve_fallback_client",
        lambda *a, **k: (None, "explicit_preset") if k.get("explicit_preset") else (fallback, ""),
    )
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))

    outcome = await execute_create(
        call_category="chat",
        prepare=lambda mc: PreparedAttempt(messages=[{"role": "user", "content": "hi"}]),
        caller="test",
        primary_mc=primary,
        explicit_preset=True,
    )
    assert outcome.ok is False
    assert seen == ["primary"]
    assert any(r.get("skip_reason") == "explicit_preset" for r in rows)


@pytest.mark.asyncio
async def test_incompatible_tools_refuse_without_silent_downgrade(monkeypatch):
    primary = _mc("primary")
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))
    monkeypatch.setattr(
        "core.llm_failover.resolve_fallback_client",
        lambda *a, **k: (_mc("fallback"), ""),
    )

    outcome = await execute_create(
        call_category="chat",
        prepare=lambda mc: PreparedAttempt(messages=[], refuse_reason="tool_mode_incompatible"),
        caller="test",
        primary_mc=primary,
    )
    assert outcome.ok is False
    assert outcome.skip_reason == "tool_mode_incompatible"
    assert any(r.get("skip_reason") == "tool_mode_incompatible" for r in rows)


@pytest.mark.asyncio
async def test_open_primary_breaker_goes_straight_to_fallback(monkeypatch):
    from core import llm_failover as fo

    primary = _mc("primary")
    fallback = _mc("fallback")
    for _ in range(3):
        fo.breaker_record("primary", "chat", "upstream_unavailable", ok=False)
    assert fo.breaker_permits("primary", "chat") is False

    seen = []

    async def create(mc, messages, **kwargs):
        seen.append(mc.name)
        return _ok("from-fallback")

    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))

    outcome = await execute_create(
        call_category="chat",
        prepare=lambda mc: PreparedAttempt(messages=[{"role": "user", "content": "hi"}]),
        caller="test",
        primary_mc=primary,
    )
    assert outcome.ok is True
    assert seen == ["fallback"]
    assert any(r.get("skip_reason") == "breaker_open" for r in rows)


@pytest.mark.asyncio
async def test_stream_switches_only_before_any_yield(monkeypatch):
    from core import llm_failover as fo

    primary = _mc("primary")
    fallback = _mc("fallback")

    async def stream(mc, messages, **kwargs):
        if mc.name == "primary":
            raise _timeout()
        yield "ok"

    monkeypatch.setattr("core.llm_protocol.stream_text", stream)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))

    pieces = []
    async for piece in execute_stream(
        call_category="chat",
        prepare=lambda mc: PreparedAttempt(messages=[{"role": "user", "content": "hi"}]),
        caller="test",
        primary_mc=primary,
    ):
        pieces.append(piece)
    assert pieces == ["ok"]
    assert any(r.get("route_role") == "fallback" and r.get("ok") for r in rows)


@pytest.mark.asyncio
async def test_stream_already_emitted_does_not_switch(monkeypatch):
    from core import llm_failover as fo

    primary = _mc("primary")
    fallback = _mc("fallback")
    seen = []

    async def stream(mc, messages, **kwargs):
        seen.append(mc.name)
        yield "hello"
        raise _timeout()

    monkeypatch.setattr("core.llm_protocol.stream_text", stream)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))

    pieces = []
    with pytest.raises(TimeoutError):
        async for piece in execute_stream(
            call_category="chat",
            prepare=lambda mc: PreparedAttempt(messages=[{"role": "user", "content": "hi"}]),
            caller="test",
            primary_mc=primary,
        ):
            pieces.append(piece)
    assert pieces == ["hello"]
    assert seen == ["primary"]
    assert any(r.get("skip_reason") == "stream_already_emitted" for r in rows)


def test_failover_stats_keep_primary_failure_and_denominators(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from core import api_call_log

    ledger = tmp_path / "api_calls.jsonl"
    monkeypatch.setattr(api_call_log, "get_paths", lambda: NS(api_call_log=lambda: ledger))
    api_call_log.append(
        caller="llm_client", purpose="chat", provider="openai", model="p",
        duration_ms=10, ok=False, logical_call_id="abc", attempt_id="abc:p",
        route_role="primary", error_category="timeout",
    )
    api_call_log.append(
        caller="llm_client", purpose="chat", provider="openai", model="f",
        duration_ms=12, ok=True, logical_call_id="abc", attempt_id="abc:f",
        route_role="fallback", switch_reason="timeout", logical_final=True,
    )
    stats = api_call_log.failover_stats(window_hours=24)
    assert stats["attempts"]["total"] == 2
    assert stats["attempts"]["failed"] == 1
    assert stats["logical"]["total"] == 1
    assert stats["logical"]["failed"] == 0
    assert stats["fallback"]["issued"] == 1
    assert stats["fallback"]["succeeded"] == 1
    assert stats["notes"]["bodies_stored"] is False
    assert "prompt" not in str(stats)


@pytest.mark.asyncio
async def test_stream_budget_skip_does_not_switch_to_fallback(monkeypatch):
    from core import llm_failover as fo

    primary = _mc("primary")
    fallback = _mc("fallback")
    seen = []

    async def stream(mc, messages, **kwargs):
        seen.append(mc.name)
        yield "late"

    monkeypatch.setattr("core.llm_protocol.stream_text", stream)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    monkeypatch.setattr(fo, "total_wall", lambda _category: 0.0)
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))

    with pytest.raises(fo.FailoverSkip, match="budget_exhausted"):
        async for _piece in execute_stream(
            call_category="chat",
            prepare=lambda mc: PreparedAttempt(messages=[{"role": "user", "content": "hi"}]),
            caller="test",
            primary_mc=primary,
        ):
            pass
    assert seen == []
    assert not any(r.get("route_role") == "fallback" for r in rows)


@pytest.mark.asyncio
async def test_shutdown_beats_fallback(monkeypatch):
    from core import llm_failover as fo

    primary = _mc("primary")
    fallback = _mc("fallback")
    seen = []

    async def create(mc, messages, **kwargs):
        seen.append(mc.name)
        raise _timeout()

    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    monkeypatch.setattr("core.api_call_log.append", lambda **row: None)
    fo.mark_shutdown()
    with pytest.raises(asyncio.CancelledError):
        await execute_create(
            call_category="chat",
            prepare=lambda mc: PreparedAttempt(messages=[{"role": "user", "content": "hi"}]),
            caller="test",
            primary_mc=primary,
        )
    assert seen == []


def test_plain_403_without_geo_marker_is_not_eligible():
    err = RuntimeError("HTTP 403")
    err.status_code = 403
    category, reason = classify_exception(err)
    assert category == "upstream_auth_failed"
    assert reason == ""
    assert "geo_blocked" not in FAILOVER_ELIGIBLE or reason not in FAILOVER_ELIGIBLE
