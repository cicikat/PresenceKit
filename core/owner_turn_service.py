"""Application boundary for trusted owner turns.

The legacy desktop/mobile implementation remains the compatibility executor
for now, but route modules no longer construct its caller semantics. This
module owns the immutable caller context and idempotency boundary used by the
versioned owner API.
"""

from __future__ import annotations

import re
import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from core import owner_turn_receipts

_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_MESSAGE_LENGTH = 12000
_INFLIGHT: dict[tuple[str, str], asyncio.Task[dict]] = {}


def is_currently_inflight(caller_label: str, client_turn_id: str) -> bool:
    """Return whether this process still owns a live execution task."""
    task = _INFLIGHT.get((caller_label, client_turn_id))
    return task is not None and not task.done()


@dataclass(frozen=True)
class TurnCallerContext:
    caller_kind: str
    token_label: str
    token_profile: str
    provenance_channel: str
    live_origin_channel: str
    durable_mobile_mirror: bool
    allowed_tool_categories: frozenset[str] | None = None
    allowed_tool_names: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.caller_kind not in {"desktop", "mobile", "owner_input"}:
            raise ValueError("invalid owner turn caller kind")
        if not self.token_label or not self.token_profile:
            raise ValueError("owner turn caller identity is required")
        if self.provenance_channel not in {"desktop", "mobile"}:
            raise ValueError("invalid provenance channel")
        if self.live_origin_channel not in {"desktop", "mobile"}:
            raise ValueError("invalid live origin channel")


def legacy_desktop_context(token_label: str = "legacy-admin") -> TurnCallerContext:
    return TurnCallerContext(
        caller_kind="desktop", token_label=token_label, token_profile="desktop",
        provenance_channel="desktop", live_origin_channel="desktop",
        durable_mobile_mirror=True,
    )


def legacy_mobile_context(token_label: str = "legacy-admin") -> TurnCallerContext:
    return TurnCallerContext(
        caller_kind="mobile", token_label=token_label, token_profile="mobile",
        provenance_channel="mobile", live_origin_channel="mobile",
        durable_mobile_mirror=True,
    )


def owner_input_context(token_label: str, token_profile: str = "owner-input") -> TurnCallerContext:
    return TurnCallerContext(
        caller_kind="owner_input", token_label=token_label, token_profile=token_profile,
        provenance_channel="mobile", live_origin_channel="mobile",
        durable_mobile_mirror=True,
        allowed_tool_categories=frozenset({"info", "memory"}),
    )


def validate_client_turn_id(value: object) -> str:
    if not isinstance(value, str) or not _OPAQUE_ID_RE.fullmatch(value):
        raise ValueError("client_turn_id must be a stable opaque id")
    return value


def validate_message(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("message must be a string")
    message = value.strip()
    if not message:
        raise ValueError("message cannot be empty")
    if len(message) > _MAX_MESSAGE_LENGTH:
        raise ValueError("message exceeds the maximum length")
    return message


def validate_upload_ids(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError("upload_ids must be a bounded list")
    result = []
    for item in value:
        if not isinstance(item, str) or not _OPAQUE_ID_RE.fullmatch(item):
            raise ValueError("upload_ids must contain opaque ids only")
        result.append(item)
    return result


async def run_legacy_owner_turn(
    message: str,
    context: TurnCallerContext,
    *,
    reply_to: dict | None = None,
    trusted_user_text: str | None = None,
    ingress_event_id: str = "",
    audit_extras: dict | None = None,
    executor: Callable[..., Awaitable[dict]],
) -> dict:
    """Run the one existing reality chain with a fixed caller context."""
    kwargs = {"reply_to": reply_to}
    if audit_extras:
        kwargs["audit_extras"] = audit_extras
    if context.caller_kind != "desktop":
        kwargs["live_origin_channel"] = context.live_origin_channel
        kwargs["durable_mobile_mirror"] = context.durable_mobile_mirror
    if trusted_user_text is not None:
        kwargs["trusted_user_text"] = trusted_user_text
    if context.allowed_tool_categories is not None:
        kwargs["allowed_tool_categories"] = context.allowed_tool_categories
    if context.allowed_tool_names is not None:
        kwargs["allowed_tool_names"] = context.allowed_tool_names
    if ingress_event_id:
        kwargs["ingress_event_id"] = ingress_event_id
        kwargs["ingress_dedupe_key"] = ingress_event_id
    return await executor(message, context.provenance_channel, **kwargs)


async def execute_idempotent_owner_turn(
    *,
    client_turn_id: str,
    message: str,
    reply_to: dict | None,
    upload_ids: list[str],
    context: TurnCallerContext,
    executor: Callable[..., Awaitable[dict]],
) -> tuple[str, dict | None]:
    """Execute once, or project the canonical result from retained history."""
    client_turn_id = validate_client_turn_id(client_turn_id)
    message = validate_message(message)
    request_digest = owner_turn_receipts.request_hash(
        message=message, reply_to=reply_to, upload_ids=upload_ids,
    )
    lock = await owner_turn_receipts.lock_for(context.token_label, client_turn_id)
    async with lock:
        owner_turn_receipts.prune(is_inflight=is_currently_inflight)
        row = owner_turn_receipts.load(context.token_label, client_turn_id)
        if row is not None:
            if row.get("request_hash") != request_digest:
                return "conflict", owner_turn_receipts.projection(row)
            status = row.get("status")
            if status == "completed":
                canonical_char_id = row.get("canonical_char_id")
                projected = _project_retained_result(
                    row.get("canonical_turn_id"), canonical_char_id,
                )
                return ("completed_replay", projected) if projected is not None else (
                    "completed_result_expired", owner_turn_receipts.projection(row)
                )
            if status == "running":
                if is_currently_inflight(context.token_label, client_turn_id):
                    return "in_flight", owner_turn_receipts.projection(row)
                row = owner_turn_receipts.recover_if_interrupted(
                    context.token_label,
                    client_turn_id,
                    is_inflight=False,
                )
                return "interrupted_unknown", owner_turn_receipts.projection(row or {})
            return str(status or "failed"), owner_turn_receipts.projection(row)

        row = owner_turn_receipts.write(
            caller_label=context.token_label,
            client_turn_id=client_turn_id,
            request_digest=request_digest,
            status="running",
        )
        task = asyncio.create_task(_execute_and_record(
            client_turn_id=client_turn_id,
            message=message,
            reply_to=reply_to,
            upload_ids=upload_ids,
            context=context,
            executor=executor,
            created_at=row.get("created_at"),
            request_digest=request_digest,
        ))
        _INFLIGHT[(context.token_label, client_turn_id)] = task
        task.add_done_callback(
            lambda completed: _INFLIGHT.pop((context.token_label, client_turn_id), None)
            if _INFLIGHT.get((context.token_label, client_turn_id)) is completed else None
        )

    result = await asyncio.shield(task)
    return "completed", result


async def _execute_and_record(
    *,
    client_turn_id: str,
    message: str,
    reply_to: dict | None,
    upload_ids: list[str],
    context: TurnCallerContext,
    executor: Callable[..., Awaitable[dict]],
    created_at: float | None,
    request_digest: str,
) -> dict:
    try:
        result = await run_legacy_owner_turn(
            message,
            context,
            reply_to=reply_to,
            ingress_event_id=f"owner:{context.token_label}:{client_turn_id}",
            executor=executor,
        )
        canonical_turn_id = str(result.get("turn_id") or "")
        if not canonical_turn_id:
            raise RuntimeError("owner turn did not produce a canonical turn id")
        owner_turn_receipts.write(
            caller_label=context.token_label,
            client_turn_id=client_turn_id,
            request_digest=request_digest,
            status="completed",
            canonical_turn_id=canonical_turn_id,
            created_at=created_at,
            canonical_char_id=_canonical_character_id(),
        )
        return result
    except Exception as exc:
        owner_turn_receipts.write(
            caller_label=context.token_label,
            client_turn_id=client_turn_id,
            request_digest=request_digest,
            status="failed",
            error_code=type(exc).__name__,
            created_at=created_at,
            canonical_char_id=_canonical_character_id(),
        )
        raise


def _canonical_character_id() -> str | None:
    try:
        from core.scheduler.loop import _active_char_id_or_none

        return _active_char_id_or_none()
    except Exception:
        return None


def _project_canonical_result(turn_id: object, canonical_char_id: object = None) -> dict | None:
    if not isinstance(turn_id, str) or not turn_id:
        return None
    try:
        from core.config_loader import get_config
        from core.memory.short_term import load
        from core.sandbox import get_paths

        cfg = get_config()
        uid = str(cfg.get("scheduler", {}).get("owner_id", "owner"))
    except Exception:
        return None
    candidates: list[str] = []
    if isinstance(canonical_char_id, str) and canonical_char_id:
        candidates.append(canonical_char_id)
    active = _canonical_character_id()
    if active and active not in candidates:
        candidates.append(active)
    try:
        for char_id in get_paths()._memory_character_ids():
            if char_id not in candidates:
                candidates.append(char_id)
    except Exception:
        pass
    for char_id in candidates:
        try:
            entries = load(uid, char_id=char_id)
        except Exception:
            continue
        matches = [
            entry for entry in entries
            if entry.get("role") == "assistant" and entry.get("_turn_id") == turn_id
        ]
        if not matches:
            continue
        reply = matches[-1].get("content")
        if not isinstance(reply, str):
            return None
        return {
            "reply": reply,
            "emotion": "neutral",
            "turn_id": turn_id,
            "msg_id": turn_id,
            "critical_written": True,
        }
    return None


def _project_retained_result(turn_id: object, canonical_char_id: object) -> dict | None:
    """Call the projector while retaining compatibility with one-argument test seams."""
    import inspect

    try:
        parameter_count = len(inspect.signature(_project_canonical_result).parameters)
    except (TypeError, ValueError):
        parameter_count = 2
    if parameter_count < 2:
        return _project_canonical_result(turn_id)
    return _project_canonical_result(turn_id, canonical_char_id)


async def read_owner_turn_receipt(caller_label: str, client_turn_id: str) -> dict | None:
    """Read one caller-owned receipt under its same per-key lock."""
    lock = await owner_turn_receipts.lock_for(caller_label, client_turn_id)
    async with lock:
        row = owner_turn_receipts.load(caller_label, client_turn_id)
        if row is None:
            return None
        if row.get("status") == "running":
            row = owner_turn_receipts.recover_if_interrupted(
                caller_label,
                client_turn_id,
                is_inflight=is_currently_inflight(caller_label, client_turn_id),
            )
        return row
