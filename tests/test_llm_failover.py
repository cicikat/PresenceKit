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


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery, tool_history, expected", [
    ("off", False, ["primary"]),
    ("fallback", False, ["primary", "fallback"]),
    ("nonstream_then_fallback", False, ["primary", "primary", "fallback"]),
    ("nonstream_then_fallback", True, ["primary"]),
])
async def test_protocol_recovery_is_bounded_and_does_not_replay_tools(monkeypatch, recovery, tool_history, expected):
    """Malformed tool-bearing responses remain fail-closed; transport retries are separate."""
    from core import llm_failover as fo
    from core.llm_protocol import UpstreamResponseFormatError
    primary, fallback = _mc("primary"), _mc("fallback")
    primary.force_stream = True
    primary.protocol_recovery = recovery
    seen, rows = [], []
    async def create(mc, messages, **kwargs):
        seen.append(mc.name)
        if mc.name == "primary":
            if len(seen) == 2:
                assert mc.force_stream is False
            raise UpstreamResponseFormatError("synthetic invalid completion")
        return _ok("recovered")
    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))
    messages = [{"role": "user", "content": "write a document"}]
    if tool_history:
        messages += [{"role": "tool", "tool_call_id": "completed-write", "content": "written"}]
    outcome = await execute_create(call_category="chat", primary_mc=primary,
        prepare=lambda mc: PreparedAttempt(messages=messages), caller="test")
    assert seen == expected
    assert outcome.ok is (expected[-1] == "fallback")
    assert rows[0]["error_category"] == "protocol_incompatible"
    if tool_history:
        assert outcome.skip_reason == "tool_history_protocol_incompatible"


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_failures", [0, 2])
async def test_protocol_nonstream_success_does_not_use_fallback(monkeypatch, prior_failures):
    from core import llm_failover as fo
    from core.llm_protocol import UpstreamResponseFormatError
    primary = _mc("primary")
    primary.force_stream = True
    primary.protocol_recovery = "nonstream_then_fallback"
    for _ in range(prior_failures):
        fo.breaker_record(primary.name, "chat", "protocol_incompatible", ok=False, protocol_recovery=True)
    seen, rows = [], []
    async def create(mc, messages, **kwargs):
        seen.append(mc.force_stream)
        if mc.force_stream:
            raise UpstreamResponseFormatError("synthetic bad stream")
        return _ok("document ready")
    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (_mc("fallback"), ""))
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))
    outcome = await execute_create(call_category="chat", primary_mc=primary,
        prepare=lambda mc: PreparedAttempt(messages=[]), caller="test")
    assert outcome.ok and outcome.value.assistant_text == "document ready"
    assert seen == [True, False]
    assert primary.force_stream is True
    assert [(r["route_role"], r["ok"]) for r in rows] == [("primary", False), ("recovery", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("messages, marked", [
    ([{"role": "system", "content": "result after conversion"}], True),
    ([{"role": "system", "content": "result", "_layer": "autonomy_tool_result"}], False),
    ([{"type": "function_call_output", "call_id": "done", "output": "written"}], False),
    ([{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "done", "content": "written"}]}], False),
])
async def test_protocol_recovery_preserves_converted_tool_history(monkeypatch, messages, marked):
    """Native and converted tool receipts must all prevent automatic replay."""
    from core import llm_failover as fo
    from core.llm_protocol import UpstreamResponseFormatError
    primary = _mc("primary")
    primary.force_stream = True
    primary.protocol_recovery = "nonstream_then_fallback"
    seen = []
    async def create(mc, messages, **kwargs):
        seen.append(mc.name)
        if mc.name == "primary":
            raise UpstreamResponseFormatError("synthetic bad stream")
        return _ok("must not recover")
    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (_mc("fallback"), ""))
    outcome = await execute_create(call_category="chat", primary_mc=primary,
        prepare=lambda mc: PreparedAttempt(messages=messages, has_tool_results=marked), caller="test")
    assert not outcome.ok and outcome.skip_reason == "tool_history_protocol_incompatible"
    assert seen == ["primary"]


@pytest.mark.asyncio
async def test_repeated_protocol_failures_open_breaker_and_use_fallback(monkeypatch):
    """Repeated invalid streams stop spending primary requests during cooldown."""
    from core import llm_failover as fo
    from core.llm_protocol import UpstreamResponseFormatError
    primary = _mc("primary")
    primary.protocol_recovery = "fallback"
    primary_requests = []
    async def create(mc, messages, **kwargs):
        if mc.name == "primary":
            primary_requests.append(mc.name)
            raise UpstreamResponseFormatError("synthetic bad stream")
        return _ok("recovered")
    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (_mc("fallback"), ""))
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **row: rows.append(row))
    for _ in range(4):
        outcome = await execute_create(call_category="chat", primary_mc=primary,
            prepare=lambda mc: PreparedAttempt(messages=[]), caller="test")
        assert outcome.ok and outcome.value.assistant_text == "recovered"
    assert len(primary_requests) == 3
    assert any(row.get("skip_reason") == "breaker_open" for row in rows)


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
    api_call_log.append(caller="stt", purpose="transcribe_remote", provider="backup", model="whisper",
                        duration_ms=12, ok=True, logical_call_id="stt-example", route_role="fallback")
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


@pytest.mark.asyncio
@pytest.mark.parametrize("breaker_open", [False, True])
async def test_completed_tool_receipt_allows_transport_failover(monkeypatch, breaker_open):
    """C/D: only repeat failed inference; completed tool receipts are read-only input."""
    from core import llm_failover as fo
    primary, fallback = _mc("primary"), _mc("fallback")
    history = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "write-1", "type": "function", "function": {"name": "save", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "write-1", "content": "saved_and_verified"},
    ]
    seen = []
    async def create(mc, messages, **kwargs):
        seen.append(mc.name)
        assert messages == history
        if mc.name == "primary":
            raise _timeout()
        return _ok("saved")
    monkeypatch.setattr("core.llm_protocol.create", create)
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    monkeypatch.setattr(fo, "breaker_permits", lambda name, cat: name != "primary" or not breaker_open)
    monkeypatch.setattr("core.api_call_log.append", lambda **row: None)
    outcome = await execute_create(call_category="chat", primary_mc=primary,
        prepare=lambda mc: PreparedAttempt(messages=history), caller="test")
    assert outcome.ok, (outcome.error_category, outcome.skip_reason)
    assert seen == (["fallback"] if breaker_open else ["primary", "fallback"])


@pytest.mark.asyncio
async def test_turn_route_isolates_concurrency_and_resets_after_cancellation(monkeypatch):
    """I/J/K: state lives in the logical turn, including wait_for child tasks."""
    from core import llm_failover as fo
    from core.llm_turn_route import scoped_tool_turn, current_route
    primary, fallback = _mc("primary"), _mc("fallback")
    seen = {}
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    async def create(mc, messages, **kwargs):
        key = messages[0]["content"]
        seen.setdefault(key, []).append(mc.name)
        await asyncio.sleep(0)
        if key == "failed" and mc is primary:
            raise _timeout()
        return _ok()
    monkeypatch.setattr("core.llm_protocol.create", create)
    @scoped_tool_turn
    async def run(key):
        state = current_route()
        for _ in range(2):
            result = await asyncio.wait_for(execute_create(call_category="chat", primary_mc=primary,
                prepare=lambda mc: PreparedAttempt(messages=[{"role": "user", "content": key}]),
                caller="test", char_id=key), timeout=2)
            assert result.ok
        return state
    failed, healthy = await asyncio.gather(run("failed"), run("healthy"))
    assert seen == {"failed": ["primary", "fallback", "fallback"], "healthy": ["primary", "primary"]}
    assert failed.turn_id != healthy.turn_id and failed.closed and healthy.closed
    assert current_route() is None
    captured = []
    ready = asyncio.Event()
    @scoped_tool_turn
    async def blocked():
        captured.append(current_route())
        ready.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(blocked())
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert captured[0].closed and current_route() is None
    # Independent turn resolves primary again, regardless of the previous winner.
    await run("new")
    assert seen["new"] == ["primary", "primary"]


@pytest.mark.asyncio
async def test_turn_route_lazy_stream_close_and_failed_fallback_are_bounded(monkeypatch):
    """G/J: failed fallbacks cannot stick; a deferred stream retains and closes its own state."""
    from core import llm_failover as fo
    from core.llm_turn_route import scoped_tool_turn, current_route
    primary, fallback = _mc("primary"), _mc("fallback")
    monkeypatch.setattr(fo, "resolve_fallback_client", lambda *a, **k: (fallback, ""))
    calls = []
    async def create(mc, messages, **kwargs):
        calls.append(mc.name)
        raise _timeout()
    monkeypatch.setattr("core.llm_protocol.create", create)
    captured = []
    @scoped_tool_turn
    async def failed():
        state = current_route()
        outcome = await execute_create(call_category="chat", primary_mc=primary,
            prepare=lambda mc: PreparedAttempt(messages=[]), caller="test")
        assert not outcome.ok and state.effective is None
    await failed()
    assert calls == ["primary", "fallback"]
    @scoped_tool_turn
    async def deferred():
        captured.append(current_route())
        async def source():
            assert current_route() is captured[0]
            yield "one"
            yield "two"
        return source()
    stream = await deferred()
    assert current_route() is None
    assert await anext(stream) == "one"
    assert current_route() is None
    await asyncio.create_task(stream.aclose())
    assert captured[0].closed and current_route() is None
