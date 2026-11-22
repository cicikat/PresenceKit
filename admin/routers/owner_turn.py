"""Versioned single-owner turn API with durable idempotency."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from admin.auth import TokenInfo, require_scopes
from core.owner_turn_service import (
    execute_idempotent_owner_turn,
    owner_input_context,
    read_owner_turn_receipt,
    validate_client_turn_id,
    validate_message,
    validate_upload_ids,
)

router = APIRouter()
_ALLOWED_BODY_KEYS = frozenset({"client_turn_id", "message", "reply_to", "upload_ids"})
_FORBIDDEN_BODY_KEYS = frozenset({
    "uid", "char_id", "source", "trust", "tool_categories", "origin",
    "token", "config", "path", "file_path", "tool_capabilities",
})


class OwnerTurnRequest(BaseModel):
    """OpenAPI-visible request shape; semantic validation stays below."""

    client_turn_id: str
    message: str
    reply_to: dict | None = None
    upload_ids: list[str] | None = None

    model_config = ConfigDict(extra="forbid")


def _require_owner_input(info: TokenInfo) -> None:
    if getattr(info, "profile", None) not in {"owner-input", "integration"} or "chat" not in getattr(info, "scopes", ()):
        raise HTTPException(status_code=403, detail="owner-input token required")


def _validate_reply_to(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="reply_to must be an object")
    text = value.get("text")
    ts = value.get("ts")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="reply_to.text is required")
    if len(text) > 2000:
        raise HTTPException(status_code=422, detail="reply_to.text exceeds the maximum length")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        raise HTTPException(status_code=422, detail="reply_to.ts must be numeric")
    return {"text": text, "ts": float(ts)}


def _request_body(body: object) -> tuple[str, str, dict | None, list[str]]:
    if isinstance(body, OwnerTurnRequest):
        body = body.dict()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    keys = set(body)
    forbidden = sorted(keys & _FORBIDDEN_BODY_KEYS)
    if forbidden:
        raise HTTPException(status_code=422, detail="caller-controlled fields are not accepted")
    if keys - _ALLOWED_BODY_KEYS:
        raise HTTPException(status_code=422, detail="unknown owner turn field")
    try:
        client_turn_id = validate_client_turn_id(body.get("client_turn_id"))
        message = validate_message(body.get("message"))
        upload_ids = validate_upload_ids(body.get("upload_ids"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    reply_to = _validate_reply_to(body.get("reply_to"))
    if upload_ids:
        # Upload ingestion has not issued an owner-input opaque reference yet;
        # never silently ignore a claimed attachment.
        raise HTTPException(status_code=409, detail="upload_id_not_available")
    return client_turn_id, message, reply_to, upload_ids


@router.post("/v1/owner/turns", summary="Execute one idempotent owner turn")
async def owner_turn(
    body: OwnerTurnRequest,
    _auth: TokenInfo = Depends(require_scopes("chat")),
):
    _require_owner_input(_auth)
    client_turn_id, message, reply_to, upload_ids = _request_body(body)
    from admin.reality_admission import require_reality_turn
    from core.owner_chat.service import run_owner_chat_turn
    from core.config_loader import get_config

    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    require_reality_turn(uid)
    context = owner_input_context(_auth.label, getattr(_auth, "profile", None) or "owner-input")
    try:
        status, result = await execute_idempotent_owner_turn(
            client_turn_id=client_turn_id,
            message=message,
            reply_to=reply_to,
            upload_ids=upload_ids,
            context=context,
            executor=run_owner_chat_turn,
        )
    except Exception as exc:
        from core.llm_client import UpstreamResponseFormatError

        if isinstance(exc, UpstreamResponseFormatError):
            raise HTTPException(status_code=502, detail="owner turn upstream failure") from exc
        raise HTTPException(status_code=503, detail="owner turn unavailable") from exc

    if status == "conflict":
        raise HTTPException(status_code=409, detail="client_turn_id payload conflict")
    if status == "in_flight":
        return JSONResponse(status_code=202, content={"status": "in_flight", **(result or {})})
    if status == "completed_result_expired":
        return JSONResponse(status_code=410, content={"status": "completed_result_expired", **(result or {})})
    if status == "interrupted_unknown":
        raise HTTPException(status_code=503, detail="execution_outcome_unknown")
    if status not in {"completed", "completed_replay"} or result is None:
        raise HTTPException(status_code=503, detail=str((result or {}).get("error_code") or "owner turn unavailable"))
    if status == "completed":
        from core.scheduler.sensor_events import notify_chat_happened

        notify_chat_happened()
    return result


@router.get("/v1/owner/turns/{client_turn_id}", summary="Read one caller-owned owner turn status")
async def owner_turn_status(
    client_turn_id: str,
    _auth: TokenInfo = Depends(require_scopes("chat")),
):
    _require_owner_input(_auth)
    try:
        client_turn_id = validate_client_turn_id(client_turn_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = await read_owner_turn_receipt(_auth.label, client_turn_id)
    if row is None:
        raise HTTPException(status_code=404, detail="owner turn not found")
    return owner_turn_receipts.projection(row)
