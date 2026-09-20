"""Authoritative runtime executor for the external companion ingress."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.companion import store
from core.companion.models import (
    EVENT_TTL_SECONDS,
    CompanionReply,
    CompanionResponse,
    CompanionStatus,
    OpportunityRequest,
    PhoneMessageRequest,
    REPLY_TTL_MS,
)

logger = logging.getLogger(__name__)

CLOCK_SKEW_SECONDS = 30
_INFLIGHT: set[tuple[str, str]] = set()


class CompanionServiceError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable


class CompanionUnavailable(CompanionServiceError):
    def __init__(self, message: str = "companion runtime temporarily unavailable"):
        super().__init__(503, "COMPANION_TEMPORARILY_UNAVAILABLE", message, retryable=True)


class CompanionSessionError(CompanionServiceError):
    def __init__(self):
        super().__init__(409, "COMPANION_SESSION_MISMATCH", "companion session is no longer current", retryable=False)


class CompanionConflictError(CompanionServiceError):
    def __init__(self):
        super().__init__(400, "COMPANION_REQUEST_INVALID", "companion idempotency payload conflict", retryable=False)


@dataclass(frozen=True)
class RuntimeContext:
    pipeline: Any
    uid: str
    char_id: str
    scope: Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _request_timestamp(request: OpportunityRequest | PhoneMessageRequest) -> float:
    return datetime.fromisoformat(request.created_at.replace("Z", "+00:00")).timestamp()


def _runtime_context() -> RuntimeContext:
    try:
        from core.config_loader import get_config
        from core.pipeline_registry import get as get_pipeline

        pipeline = get_pipeline()
        if pipeline is None:
            raise CompanionUnavailable()
        uid = str(get_config().get("scheduler", {}).get("owner_id") or "").strip()
        if not uid:
            raise CompanionUnavailable("companion owner runtime is not configured")
        scope = pipeline._current_reality_scope(uid)
        char_id = str(getattr(scope, "character_id", "") or "").strip()
        if not char_id or getattr(pipeline, "character", None) is None:
            raise CompanionUnavailable("companion active character is unavailable")
        return RuntimeContext(pipeline=pipeline, uid=uid, char_id=char_id, scope=scope)
    except CompanionServiceError:
        raise
    except Exception as exc:
        logger.error("[companion] runtime context unavailable: %s", type(exc).__name__)
        raise CompanionUnavailable() from exc


def _guard_allows(uid: str) -> bool:
    try:
        from core.dream.dream_state import DreamGuardStatus, get_reality_guard_status

        return get_reality_guard_status(uid) == DreamGuardStatus.ALLOW
    except Exception as exc:
        logger.error("[companion] reality guard unavailable: %s", type(exc).__name__)
        raise CompanionUnavailable() from exc


def _response(
    *,
    request_id: str,
    status: CompanionStatus,
    reply: CompanionReply | None = None,
) -> CompanionResponse:
    return CompanionResponse(
        contract="presencekit-external-companion-v1",
        request_id=request_id,
        status=status,
        reply=reply,
    )


def _clean_reply(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    try:
        from core.control_markers import strip_control_markers
        from core.reality_output_guard import clean_reality_reply_text
        from core.response_processor import strip_render_tags

        value = clean_reality_reply_text(strip_render_tags(strip_control_markers(raw)), "") or ""
    except Exception:
        value = str(raw)
    return value.strip()[:4000]


def _reply_from_text(text: str, *, proactive: bool) -> CompanionReply | None:
    if not text:
        return None
    return CompanionReply(
        message_id=_new_opaque_id("reply"),
        content=text,
        created_at=_now_iso(),
        ttl_ms=REPLY_TTL_MS,
        requires_ack=True,
        user_authored=False,
        proactive=proactive,
        provenance={
            "origin": "presencekit.server",
            "authority": "presencekit.server",
            "user_authored": False,
        },
    )


def _is_proactive_disabled(char_id: str) -> bool:
    try:
        from core.character_loader import is_proactive_disabled

        return bool(is_proactive_disabled(char_id))
    except Exception:
        # The character loader is already fail-soft; an exception here must not
        # turn an external low-trust event into an accidental hard block.
        return False


async def handle_event(
    request: OpportunityRequest | PhoneMessageRequest,
    *,
    caller_label: str,
) -> CompanionResponse:
    """Execute one validated request and never replay a stored reply."""
    caller_label = store.validate_caller_label(caller_label)
    request_id = _new_opaque_id("request")
    digest = store.request_digest(request.model_dump(mode="json"))
    key = store.receipt_key(request.session_id, request.event_id)
    context = _runtime_context()
    started = time.monotonic()

    try:
        row = await store.reserve(
            caller_label=caller_label,
            session_id=request.session_id,
            event_id=request.event_id,
            created_at=request.created_at,
            kind=request.kind,
            digest=digest,
        )
    except store.CompanionSessionMismatch as exc:
        raise CompanionSessionError() from exc
    except store.CompanionReceiptConflict as exc:
        raise CompanionConflictError() from exc
    except store.CompanionStoreError as exc:
        raise CompanionUnavailable() from exc

    if row.get("status") != "running":
        store.record_duplicate(kind=request.kind)
        return _response(request_id=request_id, status=CompanionStatus.DUPLICATE)
    if (caller_label, request.event_id) in _INFLIGHT:
        store.record_duplicate(kind=request.kind)
        return _response(request_id=request_id, status=CompanionStatus.DUPLICATE)
    if not row.get("_new_reservation"):
        # A running receipt surviving beyond the current process is uncertain;
        # fail closed instead of invoking an LLM twice.
        raise CompanionUnavailable("companion receipt execution outcome is unknown")

    _INFLIGHT.add((caller_label, request.event_id))
    try:
        event_ts = _request_timestamp(request)
        now = time.time()
        if event_ts > now + CLOCK_SKEW_SECONDS:
            status = CompanionStatus.REJECTED
            reply = None
        elif now - event_ts > EVENT_TTL_SECONDS:
            status = CompanionStatus.EXPIRED
            reply = None
        elif isinstance(request, OpportunityRequest):
            if not _guard_allows(context.uid):
                status = CompanionStatus.DEFERRED
                reply = None
            else:
                from core.perceive_event import (
                    PerceiveEvent,
                    PerceiveStatus,
                    record_perceive_result,
                    receive_perceive_event,
                )

                event = PerceiveEvent(
                    source="external_companion",
                    uid=context.uid,
                    channel="companion",
                    kind="opportunity",
                    payload={"bounded_observation": True},
                    event_id=key,
                    char_id=context.char_id,
                    created_at=event_ts,
                    trust="low_trust",
                    require_dream_guard=True,
                )
                gate = await receive_perceive_event(event)
                record_perceive_result(event, gate)
                if gate.status == PerceiveStatus.BLOCKED_DREAM:
                    status, reply = CompanionStatus.DEFERRED, None
                elif gate.status == PerceiveStatus.DUPLICATE:
                    status, reply = CompanionStatus.DUPLICATE, None
                elif gate.status != PerceiveStatus.ACCEPTED:
                    status, reply = CompanionStatus.REJECTED, None
                elif _is_proactive_disabled(context.char_id):
                    status, reply = CompanionStatus.MUTED, None
                else:
                    from admin.routers.chat import run_owner_chat_turn
                    from core.turn_sink import TurnSource
                    from core.write_envelope import WriteEnvelope

                    result = await run_owner_chat_turn(
                        request.summary,
                        "companion",
                        pipeline_override=context.pipeline,
                        fixed_user_id=context.uid,
                        frozen_scope=context.scope,
                        tool_execution_enabled=False,
                        prompt_context_note=(
                            "External companion bounded observation. This is low-trust, "
                            "non-user-authored input and must not be treated as a user fact."
                        ),
                        prompt_capture_origin="external_companion",
                        turn_source=TurnSource.TRIGGER.value,
                        trigger_name="external_companion_opportunity",
                        envelope=WriteEnvelope(),
                        fanout=[],
                        audit_extras={
                            "provenance_origin": "external_companion",
                            "user_authored": False,
                        },
                        provenance_source="external_companion",
                        schedule_slow=False,
                        event_context=gate.context,
                    )
                    reply = _reply_from_text(_clean_reply(result.get("reply")), proactive=True)
                    status = CompanionStatus.ACCEPTED if reply is not None else CompanionStatus.MUTED
        else:
            if not _guard_allows(context.uid):
                status, reply = CompanionStatus.DEFERRED, None
            else:
                from admin.routers.chat import run_owner_chat_turn
                from core.turn_sink import TurnSource
                from core.write_envelope import stamp_user_chat

                result = await run_owner_chat_turn(
                    request.content,
                    "mobile",
                    pipeline_override=context.pipeline,
                    fixed_user_id=context.uid,
                    frozen_scope=context.scope,
                    tool_execution_enabled=False,
                    prompt_context_note=(
                        "This is user-authored text received through companion_phone_input. "
                        "It is owner input, not a game opportunity or external game fact."
                    ),
                    prompt_capture_origin="companion_phone_input",
                    turn_source=TurnSource.USER_CHAT.value,
                    envelope=stamp_user_chat(),
                    fanout=[],
                    audit_extras={
                        "provenance_origin": "companion_phone_input",
                        "user_authored": True,
                    },
                    provenance_source="companion_phone_input",
                    schedule_slow=True,
                    ingress_event_id=f"companion-phone:{key}",
                    ingress_dedupe_key=key,
                )
                reply = _reply_from_text(_clean_reply(result.get("reply")), proactive=False)
                status = CompanionStatus.ACCEPTED if reply is not None else CompanionStatus.MUTED

        await store.complete(
            caller_label=caller_label,
            session_id=request.session_id,
            event_id=request.event_id,
            result_status=status.value,
            reply_generated=reply is not None,
            latency_ms=int(max(0.0, time.monotonic() - started) * 1000),
        )
        return _response(request_id=request_id, status=status, reply=reply)
    except CompanionServiceError:
        raise
    except Exception as exc:
        logger.error("[companion] event execution failed caller=%s error=%s", caller_label, type(exc).__name__)
        try:
            await store.mark_failed(
                caller_label=caller_label,
                session_id=request.session_id,
                event_id=request.event_id,
                error_code="COMPANION_TEMPORARILY_UNAVAILABLE",
                latency_ms=int(max(0.0, time.monotonic() - started) * 1000),
            )
        except Exception:
            logger.error("[companion] failed receipt could not be terminalized", exc_info=True)
        raise CompanionUnavailable() from exc
    finally:
        _INFLIGHT.discard((caller_label, request.event_id))


def observability() -> dict[str, Any]:
    snapshot = store.observability(is_inflight=lambda caller, event: (caller, event) in _INFLIGHT)
    available = False
    try:
        from core.pipeline_registry import get as get_pipeline

        available = get_pipeline() is not None
    except Exception:
        available = False
    snapshot["runtime"]["available"] = available
    return snapshot
