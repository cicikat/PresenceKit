"""Bounded primary→fallback executor for text-model calls.

Missing-preset routing stays in ``model_registry._resolve_preset_name``.
This module only switches after a completed primary attempt (including that
preset's existing SDK retries) fails with an allowlisted transport class.

Ledger rows keep every attempt. A later fallback success never overwrites the
primary failure. SDK-internal retries are not separate rows.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.llm_protocol import UpstreamResponseFormatError, error_category_for_exception
from core.model_registry import ModelClient, get_model_client

logger = logging.getLogger(__name__)

# Per-request HTTP timeout (seconds). Matches llm_client._CALL_TIMEOUTS.
CATEGORY_TIMEOUTS: dict[str, float] = {
    "probe": 15.0,
    "intent": 10.0,
    "detect_emotion": 10.0,
    "summary": 30.0,
    "consolidation": 30.0,
    "chat": 90.0,
    "vision": 30.0,
    "perform": 10.0,
    "monologue": 10.0,
    "scenario_reconcile": 8.0,
    "event_edge_proposer": 30.0,
    "rpg_kp": 30.0,
    "sensor_judge": 10.0,
    "ime_judge": 10.0,
    "food_extract": 20.0,
}
DEFAULT_CALL_TIMEOUT = 90.0

# Total wall includes primary HTTP + SDK waits + one fallback. Not infinite.
TOTAL_WALL: dict[str, float] = {
    "sensor_judge": 20.0,
    "ime_judge": 20.0,
    "food_extract": 20.0,
    "monologue": 20.0,
    "detect_emotion": 20.0,
    "perform": 20.0,
    "intent": 20.0,
    "probe": 30.0,
    "summary": 60.0,
    "consolidation": 60.0,
    "chat": 180.0,
    "scenario_reconcile": 16.0,
    "event_edge_proposer": 60.0,
    "rpg_kp": 60.0,
}

FAILOVER_ELIGIBLE = frozenset({
    "connection_error",
    "timeout",
    "upstream_rate_limited",
    "upstream_unavailable",
    "geo_blocked",
})

_GEO_MARKERS = (
    "geo-blocked",
    "geoblocked",
    "geo_blocked",
    "geographic restriction",
    "not available in your region",
    "not available in your country",
    "unsupported_country",
    "unsupported region",
    "region_blocked",
    "territory restricted",
    "country not supported",
)

_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_S = 60.0
_BREAKER_TRIP = frozenset({"auth_or_forbidden", "upstream_5xx", "upstream_auth_failed", "upstream_unavailable"})

_SHUTTING_DOWN = False


@dataclass
class _Breaker:
    failures: int = 0
    open_until: float = 0.0
    half_open_in_flight: bool = False


_BREAKERS: dict[tuple[str, str], _Breaker] = {}


@dataclass
class AttemptOutcome:
    ok: bool
    value: Any = None
    error: BaseException | None = None
    mc: ModelClient | None = None
    role: str = "primary"
    preset: str = ""
    error_category: str = ""
    failover_reason: str = ""
    skip_reason: str = ""
    duration_ms: int = 0
    logical_call_id: str = ""
    attempt_id: str = ""
    switched: bool = False
    logical_ok: bool = False


@dataclass
class PreparedAttempt:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | None = None
    gen_kwargs: dict[str, Any] = field(default_factory=dict)
    refuse_reason: str = ""


PrepareFn = Callable[[ModelClient], PreparedAttempt | Awaitable[PreparedAttempt]]
ValidateFn = Callable[[Any], None]


def mark_shutdown() -> None:
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True


def reset_shutdown() -> None:
    """Test helper; production shutdown is one-way."""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = False


def is_shutting_down() -> bool:
    return _SHUTTING_DOWN


def category_timeout(call_category: str) -> float:
    return float(CATEGORY_TIMEOUTS.get(call_category, DEFAULT_CALL_TIMEOUT))


def total_wall(call_category: str) -> float:
    timeout = category_timeout(call_category)
    return float(TOTAL_WALL.get(call_category, timeout * 2.0))


def _status_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    if isinstance(exc, UpstreamResponseFormatError) and isinstance(exc.http_status, int):
        return exc.http_status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _is_explicit_geo_blocked(exc: BaseException, status: int | None) -> bool:
    text = str(exc).lower()
    if not any(marker in text for marker in _GEO_MARKERS):
        return False
    if status in (401,):
        return False
    return True


_CONTENT_FILTER_MARKERS = (
    "content_filter",
    "content-filter",
    "content_policy",
    "content policy",
    "contentpolicy",
    "safety system",
    "refused to respond",
    "responsibleai",
)


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """Return (ledger_or_breaker_category, failover_reason_or_empty)."""
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
        return "cancelled", ""
    if isinstance(exc, FailoverSkip):
        return exc.category, ""

    status = _status_of(exc)
    text = str(exc).lower()
    type_name = type(exc).__name__.lower()
    if _is_explicit_geo_blocked(exc, status):
        return "geo_blocked", "geo_blocked"

    if any(marker in text for marker in _CONTENT_FILTER_MARKERS) and status not in (401, 429):
        if not (isinstance(status, int) and status >= 500):
            return "content_filtered", ""

    if (
        isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException))
        or "timeout" in type_name
        or "timeout" in text
    ):
        return "timeout", "timeout"
    if (
        isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError))
        or "connect" in type_name
    ):
        return "connection_error", "connection_error"
    if isinstance(exc, httpx.TransportError) and status is None:
        return "connection_error", "connection_error"

    if status == 429 or "rate limit" in text or "too many requests" in text:
        return "upstream_rate_limited", "upstream_rate_limited"
    if isinstance(status, int) and status >= 500:
        return "upstream_unavailable", "upstream_unavailable"
    if status in (401, 403) or "unauthorized" in text:
        return "upstream_auth_failed", ""
    if "forbidden" in text and status is None:
        return "upstream_auth_failed", ""

    ledger = error_category_for_exception(exc)
    return ledger, ""


def breaker_category_for(error_category: str) -> str:
    if error_category in {"upstream_auth_failed", "auth_or_forbidden"}:
        return "auth_or_forbidden"
    if error_category in {"upstream_unavailable", "upstream_5xx"}:
        return "upstream_5xx"
    return error_category


def _breaker_key(preset: str, call_category: str) -> tuple[str, str]:
    return (preset, call_category)


def breaker_permits(preset: str, call_category: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    key = _breaker_key(preset, call_category)
    breaker = _BREAKERS.setdefault(key, _Breaker())
    if breaker.open_until <= now:
        if breaker.open_until and breaker.half_open_in_flight:
            return False
        if breaker.open_until:
            breaker.half_open_in_flight = True
        return True
    return False


def breaker_record(
    preset: str,
    call_category: str,
    category: str,
    *,
    ok: bool,
    now: float | None = None,
) -> None:
    now = time.monotonic() if now is None else now
    key = _breaker_key(preset, call_category)
    breaker = _BREAKERS.setdefault(key, _Breaker())
    breaker.half_open_in_flight = False
    if ok:
        breaker.failures = 0
        breaker.open_until = 0.0
        return
    trip = breaker_category_for(category)
    if trip in _BREAKER_TRIP:
        breaker.failures += 1
        if breaker.failures >= _BREAKER_THRESHOLD:
            breaker.open_until = now + _BREAKER_COOLDOWN_S


def confirm_business_success(preset: str, call_category: str) -> None:
    breaker_record(preset, call_category, "", ok=True)


def confirm_format_failure(preset: str, call_category: str) -> None:
    """HTTP succeeded but the business validator rejected the body.

    Do not reset health, and do not treat this as a 5xx trip.
    """
    key = _breaker_key(preset, call_category)
    breaker = _BREAKERS.setdefault(key, _Breaker())
    breaker.half_open_in_flight = False


def clear_breakers() -> None:
    _BREAKERS.clear()


class FailoverSkip(Exception):
    def __init__(self, reason: str, category: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.category = category or reason


def new_logical_call_id() -> str:
    return uuid.uuid4().hex


def _record_row(
    *,
    caller: str,
    purpose: str,
    provider: str,
    model: str,
    duration_ms: int,
    ok: bool,
    protocol: str = "",
    error_category: str = "",
    output_hint: str = "",
    logical_call_id: str = "",
    attempt_id: str = "",
    route_role: str = "",
    switch_reason: str = "",
    skip_reason: str = "",
    logical_final: bool = False,
    sdk_retry_policy: str = "",
) -> None:
    from core.api_call_log import append

    append(
        caller=caller,
        purpose=purpose,
        provider=provider,
        model=model,
        duration_ms=duration_ms,
        ok=ok,
        protocol=protocol,
        error_category=error_category,
        output_hint=output_hint,
        logical_call_id=logical_call_id,
        attempt_id=attempt_id,
        route_role=route_role,
        switch_reason=switch_reason,
        skip_reason=skip_reason,
        logical_final=logical_final,
        sdk_retry_policy=sdk_retry_policy,
    )


def _row_from_mc(
    mc: ModelClient | None,
    *,
    caller: str,
    purpose: str,
    duration_ms: int,
    ok: bool,
    logical_call_id: str,
    attempt_id: str,
    route_role: str,
    error_category: str = "",
    switch_reason: str = "",
    skip_reason: str = "",
    logical_final: bool = False,
    output_hint: str = "",
    failover: bool = False,
) -> None:
    _record_row(
        caller=caller,
        purpose=purpose,
        provider=str(getattr(mc, "provider_kind", "") or "unknown"),
        model=str(getattr(mc, "model", "") or "unknown"),
        duration_ms=duration_ms,
        ok=ok,
        protocol=str(getattr(mc, "api_protocol", "") or ""),
        error_category=error_category,
        output_hint=output_hint,
        logical_call_id=logical_call_id,
        attempt_id=attempt_id,
        route_role=route_role,
        switch_reason=switch_reason,
        skip_reason=skip_reason,
        logical_final=logical_final,
        sdk_retry_policy="zero" if failover or route_role == "fallback" else "preset",
    )


def resolve_fallback_client(
    call_category: str,
    *,
    char_id: str | None,
    primary: ModelClient,
    explicit_preset: bool,
) -> tuple[ModelClient | None, str]:
    """Return (fallback_client, skip_reason). Empty skip_reason means usable."""
    if explicit_preset:
        return None, "explicit_preset"
    from core.model_registry import resolve_fallback_route

    info = resolve_fallback_route(
        call_category, char_id=char_id, primary_preset=primary.name,
    )
    if info.get("refused_reason"):
        return None, str(info["refused_reason"])
    name = str(info.get("preset") or "")
    if not name:
        return None, "not_configured"
    try:
        client = get_model_client(
            call_category, char_id=char_id, preset_name=name, failover=True,
        )
    except Exception as exc:
        logger.warning(
            "[llm_failover] fallback preset %r for %s refused: %s",
            name, call_category, type(exc).__name__,
        )
        return None, "invalid_config"
    if client.name == primary.name:
        return None, "same_as_primary"
    return client, ""


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


async def _invoke_prepare(prepare: PrepareFn, mc: ModelClient) -> PreparedAttempt:
    prepared = prepare(mc)
    if asyncio.iscoroutine(prepared):
        prepared = await prepared
    return prepared


async def execute_create(
    *,
    call_category: str,
    prepare: PrepareFn,
    caller: str,
    char_id: str | None = None,
    primary_mc: ModelClient | None = None,
    explicit_preset: bool = False,
    validate: ValidateFn | None = None,
    reset_breaker_on_http_success: bool = True,
    purpose: str | None = None,
) -> AttemptOutcome:
    """Run primary then at most one fallback create() call."""
    from core.llm_protocol import create as create_protocol_response

    purpose = purpose or call_category
    logical_id = new_logical_call_id()
    deadline = time.monotonic() + total_wall(call_category)
    if primary_mc is None:
        primary_mc = get_model_client(call_category, char_id=char_id)

    async def _one(
        mc: ModelClient,
        *,
        role: str,
        attempt_id: str,
        switch_reason: str = "",
        logical_final: bool,
    ) -> AttemptOutcome:
        started = time.perf_counter()
        failover = role == "fallback"
        remaining = _remaining(deadline)
        if remaining <= 0:
            outcome = AttemptOutcome(
                ok=False, mc=mc, role=role, preset=mc.name,
                skip_reason="budget_exhausted", logical_call_id=logical_id,
                attempt_id=attempt_id, error_category="budget_exhausted",
            )
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason="budget_exhausted", error_category="budget_exhausted",
                logical_final=logical_final, failover=failover,
            )
            return outcome
        if _SHUTTING_DOWN:
            raise asyncio.CancelledError()

        if not breaker_permits(mc.name, call_category):
            duration_ms = int((time.perf_counter() - started) * 1000)
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason="breaker_open", error_category="breaker_open",
                logical_final=logical_final, failover=failover,
            )
            return AttemptOutcome(
                ok=False, mc=mc, role=role, preset=mc.name,
                skip_reason="breaker_open", error_category="breaker_open",
                duration_ms=duration_ms, logical_call_id=logical_id, attempt_id=attempt_id,
            )

        try:
            prepared = await _invoke_prepare(prepare, mc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            category, reason = classify_exception(exc)
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                error_category=category, output_hint=type(exc).__name__,
                logical_final=logical_final, failover=failover,
            )
            return AttemptOutcome(
                ok=False, error=exc, mc=mc, role=role, preset=mc.name,
                error_category=category, failover_reason=reason,
                duration_ms=duration_ms, logical_call_id=logical_id, attempt_id=attempt_id,
            )

        if prepared.refuse_reason:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason=prepared.refuse_reason, error_category=prepared.refuse_reason,
                logical_final=logical_final, failover=failover,
            )
            return AttemptOutcome(
                ok=False, mc=mc, role=role, preset=mc.name,
                skip_reason=prepared.refuse_reason, error_category=prepared.refuse_reason,
                duration_ms=duration_ms, logical_call_id=logical_id, attempt_id=attempt_id,
            )

        gen_kwargs = dict(prepared.gen_kwargs)
        req_timeout = min(float(gen_kwargs.get("timeout") or category_timeout(call_category)), remaining)
        gen_kwargs["timeout"] = req_timeout
        try:
            value = await asyncio.wait_for(
                create_protocol_response(
                    mc,
                    prepared.messages,
                    tools=prepared.tools,
                    tool_choice=prepared.tool_choice,
                    gen_kwargs=gen_kwargs,
                ),
                timeout=req_timeout,
            )
        except asyncio.CancelledError:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason="cancelled", error_category="cancelled",
                logical_final=logical_final, failover=failover, switch_reason=switch_reason,
            )
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            category, reason = classify_exception(exc)
            breaker_record(mc.name, call_category, category, ok=False)
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                error_category=category, output_hint=type(exc).__name__,
                switch_reason=switch_reason, logical_final=logical_final, failover=failover,
            )
            return AttemptOutcome(
                ok=False, error=exc, mc=mc, role=role, preset=mc.name,
                error_category=category, failover_reason=reason,
                duration_ms=duration_ms, logical_call_id=logical_id, attempt_id=attempt_id,
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        if validate is not None:
            try:
                validate(value)
            except Exception as exc:
                confirm_format_failure(mc.name, call_category)
                _row_from_mc(
                    mc, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                    logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                    error_category="response_format", output_hint=type(exc).__name__,
                    logical_final=logical_final, failover=failover,
                )
                return AttemptOutcome(
                    ok=False, error=exc, mc=mc, role=role, preset=mc.name,
                    error_category="response_format", duration_ms=duration_ms,
                    logical_call_id=logical_id, attempt_id=attempt_id, value=value,
                )
        if reset_breaker_on_http_success:
            confirm_business_success(mc.name, call_category)
        _row_from_mc(
            mc, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=True,
            logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
            switch_reason=switch_reason, logical_final=True, failover=failover,
        )
        return AttemptOutcome(
            ok=True, value=value, mc=mc, role=role, preset=mc.name,
            duration_ms=duration_ms, logical_call_id=logical_id, attempt_id=attempt_id,
            switched=role == "fallback", logical_ok=True,
        )

    primary_id = f"{logical_id}:p"
    fallback_mc, fallback_skip = resolve_fallback_client(
        call_category, char_id=char_id, primary=primary_mc, explicit_preset=explicit_preset,
    )
    primary_final = fallback_mc is None
    primary = await _one(
        primary_mc, role="primary", attempt_id=primary_id, logical_final=primary_final,
    )
    if primary.ok:
        primary.logical_ok = True
        return primary
    if primary.skip_reason == "breaker_open" and fallback_mc is not None:
        pass
    elif primary.failover_reason not in FAILOVER_ELIGIBLE:
        if fallback_mc is not None and not primary.skip_reason:
            primary.skip_reason = "not_eligible"
            _row_from_mc(
                primary_mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                logical_call_id=logical_id, attempt_id=f"{logical_id}:skip",
                route_role="primary", skip_reason="not_eligible",
                error_category=primary.error_category or "not_eligible",
                logical_final=True,
            )
        return primary
    if fallback_mc is None:
        if not primary.skip_reason:
            primary.skip_reason = fallback_skip or "not_configured"
            # Annotate the already-written primary row cannot be rewritten;
            # emit a companion skip breadcrumb only when the primary row had
            # no skip_reason. Stats read skip_reason from any row of the logical call.
            _row_from_mc(
                primary_mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                logical_call_id=logical_id, attempt_id=f"{logical_id}:skip",
                route_role="primary", skip_reason=primary.skip_reason,
                error_category=primary.error_category or primary.skip_reason,
                logical_final=True,
            )
        return primary
    if _SHUTTING_DOWN:
        raise asyncio.CancelledError()
    if _remaining(deadline) <= 0:
        primary.skip_reason = "budget_exhausted"
        _row_from_mc(
            fallback_mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
            logical_call_id=logical_id, attempt_id=f"{logical_id}:f",
            route_role="fallback", skip_reason="budget_exhausted",
            error_category="budget_exhausted",
            switch_reason=primary.failover_reason or primary.skip_reason,
            logical_final=True, failover=True,
        )
        return primary

    fallback = await _one(
        fallback_mc,
        role="fallback",
        attempt_id=f"{logical_id}:f",
        switch_reason=primary.failover_reason or primary.skip_reason,
        logical_final=True,
    )
    fallback.switched = True
    fallback.logical_ok = fallback.ok
    if not fallback.ok and fallback.error is None:
        fallback.error = primary.error
    return fallback


async def execute_stream(
    *,
    call_category: str,
    prepare: PrepareFn,
    caller: str,
    char_id: str | None = None,
    primary_mc: ModelClient | None = None,
    explicit_preset: bool = False,
    purpose: str | None = None,
) -> AsyncIterator[str]:
    """Yield text deltas; switch only before any user-visible text is yielded."""
    from core.llm_protocol import stream_text

    purpose = purpose or call_category
    logical_id = new_logical_call_id()
    deadline = time.monotonic() + total_wall(call_category)
    if primary_mc is None:
        primary_mc = get_model_client(call_category, char_id=char_id)
    fallback_mc, fallback_skip = resolve_fallback_client(
        call_category, char_id=char_id, primary=primary_mc, explicit_preset=explicit_preset,
    )

    async def _stream_one(mc: ModelClient, *, role: str, switch_reason: str = "") -> AsyncIterator[str]:
        started = time.perf_counter()
        failover = role == "fallback"
        attempt_id = f"{logical_id}:{'f' if failover else 'p'}"
        remaining = _remaining(deadline)
        if remaining <= 0:
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason="budget_exhausted", error_category="budget_exhausted",
                logical_final=role == "fallback" or fallback_mc is None, failover=failover,
            )
            raise FailoverSkip("budget_exhausted")
        if _SHUTTING_DOWN:
            raise asyncio.CancelledError()
        if not breaker_permits(mc.name, call_category):
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason="breaker_open", error_category="breaker_open",
                logical_final=role == "fallback" or fallback_mc is None, failover=failover,
            )
            raise FailoverSkip("breaker_open")
        prepared = await _invoke_prepare(prepare, mc)
        if prepared.refuse_reason:
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason=prepared.refuse_reason, error_category=prepared.refuse_reason,
                logical_final=role == "fallback" or fallback_mc is None, failover=failover,
            )
            raise FailoverSkip(prepared.refuse_reason)
        gen_kwargs = dict(prepared.gen_kwargs)
        gen_kwargs["timeout"] = min(float(gen_kwargs.get("timeout") or category_timeout(call_category)), remaining)
        emitted = False
        try:
            async for piece in stream_text(mc, prepared.messages, gen_kwargs=gen_kwargs):
                if piece:
                    emitted = True
                yield piece
        except asyncio.CancelledError:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason="cancelled", error_category="cancelled",
                logical_final=True, failover=failover, switch_reason=switch_reason,
            )
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            category, reason = classify_exception(exc)
            breaker_record(mc.name, call_category, category, ok=False)
            _row_from_mc(
                mc, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                error_category=category, output_hint=type(exc).__name__,
                switch_reason=switch_reason,
                logical_final=emitted or fallback_mc is None or role == "fallback",
                failover=failover,
            )
            wrapped = StreamAttemptError(exc, category=category, reason=reason, emitted=emitted, mc=mc)
            raise wrapped from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        confirm_business_success(mc.name, call_category)
        _row_from_mc(
            mc, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=True,
            logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
            switch_reason=switch_reason, logical_final=True, failover=failover,
        )

    switch_reason = ""
    try:
        async for piece in _stream_one(primary_mc, role="primary"):
            yield piece
        return
    except StreamAttemptError as err:
        if err.emitted or err.reason not in FAILOVER_ELIGIBLE or fallback_mc is None:
            if fallback_mc is None and not err.emitted:
                _row_from_mc(
                    primary_mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                    logical_call_id=logical_id, attempt_id=f"{logical_id}:skip",
                    route_role="primary", skip_reason=fallback_skip or "not_configured",
                    error_category=err.category, logical_final=True,
                )
            elif err.emitted and fallback_mc is not None:
                _row_from_mc(
                    primary_mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                    logical_call_id=logical_id, attempt_id=f"{logical_id}:skip",
                    route_role="primary", skip_reason="stream_already_emitted",
                    error_category=err.category, logical_final=True,
                )
            elif fallback_mc is not None and err.reason not in FAILOVER_ELIGIBLE:
                _row_from_mc(
                    primary_mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                    logical_call_id=logical_id, attempt_id=f"{logical_id}:skip",
                    route_role="primary", skip_reason="not_eligible",
                    error_category=err.category, logical_final=True,
                )
            raise err.original
        switch_reason = err.reason or "primary_failed"
    except FailoverSkip as skip:
        # Only an open primary breaker may skip straight to a configured fallback.
        # Budget, cancel, and capability refusals are not transport failures.
        if skip.reason == "breaker_open" and fallback_mc is not None:
            switch_reason = skip.reason
        else:
            if fallback_mc is not None and skip.reason not in {"breaker_open", "cancelled"}:
                _row_from_mc(
                    primary_mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                    logical_call_id=logical_id, attempt_id=f"{logical_id}:skip",
                    route_role="primary", skip_reason=skip.reason or "not_eligible",
                    error_category=skip.reason or "not_eligible", logical_final=True,
                )
            raise

    if fallback_mc is None:
        return
    if _remaining(deadline) <= 0:
        _row_from_mc(
            fallback_mc, caller=caller, purpose=purpose, duration_ms=0, ok=False,
            logical_call_id=logical_id, attempt_id=f"{logical_id}:f",
            route_role="fallback", skip_reason="budget_exhausted",
            error_category="budget_exhausted", logical_final=True, failover=True,
        )
        raise FailoverSkip("budget_exhausted")
    async for piece in _stream_one(
        fallback_mc,
        role="fallback",
        switch_reason=switch_reason or "primary_failed",
    ):
        yield piece


class StreamAttemptError(Exception):
    def __init__(
        self,
        original: BaseException,
        *,
        category: str,
        reason: str,
        emitted: bool,
        mc: ModelClient,
    ) -> None:
        super().__init__(str(original))
        self.original = original
        self.category = category
        self.reason = reason
        self.emitted = emitted
        self.mc = mc
