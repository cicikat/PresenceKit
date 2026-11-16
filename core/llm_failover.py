"""Bounded primary→fallback executor for text-model calls.

Missing-preset routing stays in ``model_registry._resolve_preset_name``.
This module only switches after a completed primary attempt (including that
preset's existing SDK retries) fails with an allowlisted transport class.

Ledger rows keep every attempt. A later fallback success never overwrites the
primary failure. SDK-internal retries are not separate rows.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.llm_protocol import UpstreamResponseFormatError, error_category_for_exception
from core.model_registry import ModelClient, get_model_client
from core.llm_turn_route import current_route

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
    "letter_eval": 10.0,
    "invariants_relation": 30.0,
    "event_edge_proposer": 30.0,
    "rpg_kp": 30.0,
    "sensor_judge": 10.0,
    "ime_judge": 10.0,
    "food_extract": 20.0,
    "minecraft_reaction": 3.0,
}
DEFAULT_CALL_TIMEOUT = 90.0

# Total wall includes primary HTTP + SDK waits + one fallback. Not infinite.
TOTAL_WALL: dict[str, float] = {
    "minecraft_reaction": 3.0,
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
    "letter_eval": 20.0,
    "invariants_relation": 60.0,
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
    decision: Any = None
    has_tool_results: bool = False


def has_tool_results(messages: list[dict[str, Any]]) -> bool:
    """Detect completed actions before prompt/protocol conversion removes metadata."""
    return any(
        message.get("role") == "tool" or message.get("type") == "function_call_output"
        or message.get("_layer") == "autonomy_tool_result"
        or any(isinstance(block, dict) and block.get("type") == "tool_result"
               for block in (message.get("content") if isinstance(message.get("content"), list) else []))
        for message in messages
    )


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
    protocol_recovery: bool = False,
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
    if trip in _BREAKER_TRIP or (protocol_recovery and category == "protocol_incompatible"):
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
    effective_preset: str = "",
    tool_history_present: bool = False,
) -> None:
    from core.api_call_log import append

    route = current_route()
    append(
        turn_id=route.turn_id if route else "",
        turn_step=route.steps if route else 0,
        effective_preset=effective_preset,
        tool_history_present=tool_history_present,
        breaker_open=skip_reason == "breaker_open",
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
    tool_history_present: bool = False,
) -> None:
    _record_row(
        effective_preset=str(getattr(mc, "name", "") or ""),
        tool_history_present=tool_history_present,
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
        sdk_retry_policy="zero" if failover or route_role == "fallback" or getattr(mc, 'api_protocol', '') == 'systemone' else "preset",
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
    """Retry inference only; tool dispatch and receipts remain owned by the loop."""
    from core.llm_protocol import create as create_protocol_response

    purpose = purpose or call_category
    logical_id = new_logical_call_id()
    deadline = time.monotonic() + total_wall(call_category)
    if primary_mc is None:
        primary_mc = get_model_client(call_category, char_id=char_id)
    original_mc = primary_mc
    fallback_mc, fallback_skip = resolve_fallback_client(
        call_category, char_id=char_id, primary=primary_mc, explicit_preset=explicit_preset,
    )
    route = current_route()
    if route is not None:
        primary_mc, fallback_mc, fallback_skip = route.freeze(
            primary_mc, fallback_mc, fallback_skip, call_category, char_id, explicit_preset)
        original_mc = primary_mc
        primary_mc = route.select(primary_mc, fallback_mc, call_category, char_id, explicit_preset)
    sticky = primary_mc is not original_mc
    if sticky:
        fallback_mc, fallback_skip = None, "turn_fallback_exhausted"
    recovery = getattr(primary_mc, "protocol_recovery", "off")
    tool_history = False

    async def _one(
        mc: ModelClient,
        *,
        role: str,
        attempt_id: str,
        switch_reason: str = "",
        logical_final: bool,
    ) -> AttemptOutcome:
        nonlocal tool_history
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
                mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=0, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason="budget_exhausted", error_category="budget_exhausted",
                logical_final=logical_final, failover=failover,
            )
            return outcome
        if _SHUTTING_DOWN:
            raise asyncio.CancelledError()

        # Prepare before the breaker so a skipped primary still establishes
        # whether the request contains completed (possibly mutating) actions.
        try:
            prepared = await _invoke_prepare(prepare, mc)
            tool_history = tool_history or prepared.has_tool_results or has_tool_results(prepared.messages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            category, reason = classify_exception(exc)
            _row_from_mc(
                mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                error_category=category, output_hint=type(exc).__name__,
                logical_final=logical_final, failover=failover,
            )
            return AttemptOutcome(
                ok=False, error=exc, mc=mc, role=role, preset=mc.name,
                error_category=category, failover_reason=reason,
                duration_ms=duration_ms, logical_call_id=logical_id, attempt_id=attempt_id,
            )

        if role != "recovery" and not breaker_permits(mc.name, call_category):
            duration_ms = int((time.perf_counter() - started) * 1000)
            _row_from_mc(
                mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason="breaker_open", error_category="breaker_open",
                logical_final=logical_final, failover=failover,
            )
            return AttemptOutcome(
                ok=False, mc=mc, role=role, preset=mc.name,
                skip_reason="breaker_open", error_category="breaker_open",
                duration_ms=duration_ms, logical_call_id=logical_id, attempt_id=attempt_id,
            )

        if prepared.refuse_reason:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _row_from_mc(
                mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
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
        req_timeout = min(float(gen_kwargs.get("timeout") or category_timeout(call_category)), _remaining(deadline))
        if req_timeout <= 0:
            _row_from_mc(
                mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=int((time.perf_counter() - started) * 1000),
                ok=False, logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason="budget_exhausted", error_category="budget_exhausted",
                logical_final=logical_final, failover=failover,
            )
            return AttemptOutcome(ok=False, mc=mc, role=role, preset=mc.name,
                                  skip_reason="budget_exhausted", error_category="budget_exhausted",
                                  logical_call_id=logical_id, attempt_id=attempt_id)
        gen_kwargs["timeout"] = req_timeout
        try:
            if getattr(mc, "api_protocol", "") == "systemone":
                from core.decision_contract import create as create_decision, SYSTEMONE_CATEGORIES
                if call_category not in SYSTEMONE_CATEGORIES or prepared.decision is None or prepared.tools:
                    raise ValueError("System One requires a typed decision, never chat or tools")
                request = create_decision(mc, prepared.decision, timeout=req_timeout)
            else:
                request = create_protocol_response(
                    mc, prepared.messages, tools=prepared.tools,
                    tool_choice=prepared.tool_choice, gen_kwargs=gen_kwargs,
                )
            value = await asyncio.wait_for(
                request,
                timeout=req_timeout,
            )
        except asyncio.CancelledError:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _row_from_mc(
                mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
                logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
                skip_reason="cancelled", error_category="cancelled",
                logical_final=logical_final, failover=failover, switch_reason=switch_reason,
            )
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            category, reason = classify_exception(exc)
            if category == "protocol_incompatible" and recovery != "off":
                reason = category
            breaker_record(mc.name, call_category, category, ok=False,
                           protocol_recovery=recovery != "off")
            _row_from_mc(
                mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
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
                    mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=False,
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
            mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=duration_ms, ok=True,
            logical_call_id=logical_id, attempt_id=attempt_id, route_role=role,
            switch_reason=switch_reason, logical_final=True, failover=failover,
        )
        return AttemptOutcome(
            ok=True, value=value, mc=mc, role=role, preset=mc.name,
            duration_ms=duration_ms, logical_call_id=logical_id, attempt_id=attempt_id,
            switched=role == "fallback", logical_ok=True,
        )

    primary_id = f"{logical_id}:p"
    primary_final = fallback_mc is None and recovery != "nonstream_then_fallback"
    primary = await _one(
        primary_mc, role="fallback" if sticky else "primary", attempt_id=primary_id,
        switch_reason=route.switch_reason if sticky else "", logical_final=primary_final,
    )
    if primary.ok:
        primary.logical_ok = True
        return primary
    if tool_history and primary.error_category == "protocol_incompatible":
        # A malformed/partially consumed generation cannot be safely recovered.
        # Transport failure of a new inference with completed receipts can.
        primary.skip_reason = "tool_history_protocol_incompatible"
        _row_from_mc(
            primary_mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=0, ok=False,
            logical_call_id=logical_id, attempt_id=f"{logical_id}:skip",
            route_role="primary", skip_reason=primary.skip_reason,
            error_category=primary.error_category, logical_final=True,
        )
        return primary
    if (primary.error_category == "protocol_incompatible" and recovery == "nonstream_then_fallback"
            and getattr(primary_mc, "force_stream", False)):
        nonstream_mc = copy.copy(primary_mc)
        nonstream_mc.force_stream = False
        recovered = await _one(nonstream_mc, role="recovery", attempt_id=f"{logical_id}:r",
                               switch_reason="protocol_incompatible", logical_final=fallback_mc is None)
        if recovered.ok:
            return recovered
        primary = recovered
    if primary.skip_reason == "breaker_open" and fallback_mc is not None:
        pass
    elif primary.failover_reason not in FAILOVER_ELIGIBLE and not (
        recovery != "off" and primary.failover_reason == "protocol_incompatible"
    ):
        if (fallback_mc is not None or not primary_final) and not primary.skip_reason:
            primary.skip_reason = "not_eligible"
            _row_from_mc(
                primary_mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=0, ok=False,
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
                primary_mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=0, ok=False,
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
            fallback_mc, tool_history_present=tool_history, caller=caller, purpose=purpose, duration_ms=0, ok=False,
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
    fallback.failover_reason = primary.failover_reason or primary.skip_reason
    if route is not None:
        route.accept(original_mc, fallback, call_category, char_id)
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

    original_mc = primary_mc
    route = current_route()
    if route is not None:
        primary_mc, fallback_mc, fallback_skip = route.freeze(
            primary_mc, fallback_mc, fallback_skip, call_category, char_id, explicit_preset)
        original_mc = primary_mc
        primary_mc = route.select(primary_mc, fallback_mc, call_category, char_id, explicit_preset)
    sticky = primary_mc is not original_mc
    if sticky:
        fallback_mc, fallback_skip = None, "turn_fallback_exhausted"

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
        async for piece in _stream_one(
            primary_mc, role="fallback" if sticky else "primary",
            switch_reason=route.switch_reason if sticky else "",
        ):
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
    if route is not None:
        route.accept(original_mc, AttemptOutcome(ok=True, mc=fallback_mc, role="fallback",
                     failover_reason=switch_reason), call_category, char_id)


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
