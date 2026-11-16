"""Read-only contracts for the backend-only RPG Dream foundation."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from admin.auth import require_scopes
from core.config_loader import get_config
from core.data_paths import DEFAULT_CHAR_ID
from core.dream.rpg_models import RpgCorrectionRequest, RpgCorrectionResponse, RpgEntry, RpgTranscriptResponse, RpgTurnRequest, RpgTurnResponse

router = APIRouter()


class RpgCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool = True
    contract_version: str = "rpg/v1"
    max_primary_characters: int = 1


class DreamCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supported_modes: list[str]
    rpg: RpgCapability


class RpgSessionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dream_id: str
    char_id: str
    script_id: str | None
    status: str
    round_status: str
    active_round_id: str | None
    active_branch_id: str | None
    scene_revision: int
    since: float | None
    last_error_code: str | None
    session_health: str


class RpgStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: RpgSessionProjection | None
    scene: dict = Field(default_factory=dict)


class RpgObservabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_count: int
    active_session_count: int
    round_count: int
    status: str
    round_status: str
    last_error_code: str | None
    recovery_source: str
    last_updated_at: float | None
    path_health: str
    dream_id_hash: str | None
    char_id_hash: str | None
    event_count: int
    dice_count: int
    branch_count: int
    pending_receipt_count: int
    recovery_conflict_count: int
    invalid_proposal_count: int
    latency_bucket: str


def _uid() -> str:
    return str(get_config().get("scheduler", {}).get("owner_id", "owner"))


def _char_id() -> str:
    from core.pipeline_registry import get as get_pipeline
    pipeline = get_pipeline()
    return str((getattr(pipeline, "_active_character_id", None) if pipeline else None) or DEFAULT_CHAR_ID)


def _current():
    from core.dream.dream_state import read_state
    from core.dream.rpg_store import load, projection
    state = read_state(_uid())
    dream_id = str(state.get("dream_id") or "")
    if state.get("dream_mode") != "rpg" or not dream_id:
        return None
    core, health = load(_uid(), dream_id, char_id=str(state.get("char_id") or _char_id()))
    if core is None:
        # Do not manufacture a resumable core from a partial write. This is a
        # content-free error projection only; normal Dream Guard remains closed.
        return {
            "dream_id": dream_id,
            "char_id": str(state.get("char_id") or _char_id()),
            "script_id": (state.get("rpg_session") or {}).get("script_id"),
            "status": "uncertain",
            "round_status": "unknown",
            "active_round_id": None,
            "active_branch_id": None,
            "scene_revision": 0,
            "since": state.get("dream_started_at"),
            "last_error_code": "RPG_SESSION_" + health.upper(),
            "session_health": health,
        }
    return projection(core, health=health, since=state.get("dream_started_at"))


def _kernel_http(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", "RPG_INTERNAL_ERROR")
    retryable = code in {"RPG_ROUND_BUSY", "RPG_REVISION_CONFLICT", "RPG_COMMIT_UNCERTAIN", "RPG_SESSION_UNCERTAIN", "RPG_KP_UNAVAILABLE"}
    status = 409 if code in {"RPG_ROUND_BUSY", "RPG_REVISION_CONFLICT", "RPG_IDEMPOTENCY_CONFLICT", "RPG_REQUEST_CONFLICT", "RPG_NOT_ACTIVE", "RPG_ENDPOINT_REQUIRED"} else 422
    return HTTPException(status_code=status, detail={"code": code, "message": code, "retryable": retryable})


@router.get("/dream/capabilities", response_model=DreamCapabilitiesResponse, summary="Dream mode capabilities")
async def dream_capabilities(_auth=Depends(require_scopes("activity"))):
    from core.dream.dream_state import DreamMode
    return DreamCapabilitiesResponse(supported_modes=[item.value for item in DreamMode], rpg=RpgCapability())


@router.get("/dream/rpg/state", response_model=RpgStateResponse, summary="RPG Dream safe session state")
async def rpg_state(_auth=Depends(require_scopes("activity"))):
    session = _current()
    scene = {}
    if session:
        try:
            from core.dream.rpg_store import read_events
            from core.dream.rpg_projection import derive_snapshot
            events = read_events(_uid(), session["dream_id"], char_id=session["char_id"])
            scene = derive_snapshot(events, active_branch_id=session.get("active_branch_id"), revision=session.get("scene_revision", 0)).get("scene", {})
        except Exception:
            scene = {}
    return RpgStateResponse(session=session, scene=scene)


@router.post("/dream/rpg/turn", response_model=RpgTurnResponse, summary="Run one RPG Dream round")
async def rpg_turn(body: RpgTurnRequest, _auth=Depends(require_scopes("activity"))):
    state = _current()
    if state is None:
        raise _kernel_http(type("E", (), {"code": "RPG_NOT_ACTIVE"})())
    if body.dream_id != state["dream_id"]:
        raise _kernel_http(type("E", (), {"code": "RPG_DREAM_ID_MISMATCH"})())
    try:
        from core.dream.rpg_runtime import run_turn
        return await run_turn(_uid(), dream_id=body.dream_id, request_id=body.request_id, lane=body.lane, message=body.message, expected_revision=body.expected_scene_revision, char_id=state["char_id"])
    except Exception as exc:
        if hasattr(exc, "code"):
            raise _kernel_http(exc) from exc
        raise


@router.get("/dream/rpg/transcript", response_model=RpgTranscriptResponse, summary="Read RPG Dream transcript")
async def rpg_transcript(dream_id: str, before: str | None = Query(default=None, max_length=160), limit: int = Query(default=50, ge=1, le=200), _auth=Depends(require_scopes("activity"))):
    state = _current()
    if state is None:
        raise _kernel_http(type("E", (), {"code": "RPG_NOT_ACTIVE"})())
    if dream_id != state["dream_id"]:
        raise _kernel_http(type("E", (), {"code": "RPG_DREAM_ID_MISMATCH"})())
    try:
        from core.dream.rpg_runtime import transcript_projection
        return RpgTranscriptResponse(**transcript_projection(_uid(), dream_id, char_id=state["char_id"], before=before, limit=limit))
    except Exception as exc:
        if hasattr(exc, "code"):
            raise _kernel_http(exc) from exc
        raise


@router.post("/dream/rpg/corrections", response_model=RpgCorrectionResponse, summary="Apply an RPG correction")
async def rpg_correction(body: RpgCorrectionRequest, _auth=Depends(require_scopes("activity"))):
    state = _current()
    if state is None:
        raise _kernel_http(type("E", (), {"code": "RPG_NOT_ACTIVE"})())
    if body.dream_id != state["dream_id"]:
        raise _kernel_http(type("E", (), {"code": "RPG_DREAM_ID_MISMATCH"})())
    text = body.text.strip() or body.reason.strip()
    try:
        from core.dream import rpg_corrections, rpg_store
        fn = getattr(rpg_corrections, body.operation)
        result = fn(_uid(), body.dream_id, body.target_round_id, text, request_id=body.request_id, expected_revision=body.expected_scene_revision, char_id=state["char_id"])
        rows, _partial = rpg_store.read_transcript(_uid(), body.dream_id, char_id=state["char_id"])
        entry = next((row for row in reversed(rows) if row.get("correlation_id") == body.request_id), None)
        if entry is None:
            entry = {"entry_id": "entry_" + body.request_id, "lane": "shared", "kind": "correction", "content": body.operation, "ts": time.time(), "correlation_id": body.request_id, "revision": result.get("revision", 0), "branch_id": result.get("branch_id")}
            rpg_store.append_transcript(_uid(), body.dream_id, entry, char_id=state["char_id"])
        return RpgCorrectionResponse(dream_id=body.dream_id, request_id=body.request_id, operation=body.operation, scene_revision=int(result.get("revision", 0)), active_branch_id=str(result.get("branch_id") or state.get("active_branch_id") or "root"), idempotent=bool(result.get("idempotent")), entry=RpgEntry.model_validate(entry))
    except Exception as exc:
        if hasattr(exc, "code"):
            raise _kernel_http(exc) from exc
        raise


@router.get(
    "/observability/dream-rpg",
    response_model=RpgObservabilityResponse,
    summary="RPG Dream content-free observability",
)
async def rpg_observability(_auth=Depends(require_scopes("state.read"))):
    from core.dream.dream_state import read_state
    from core.dream.rpg_store import load, observability
    state = read_state(_uid())
    dream_id = str(state.get("dream_id") or "")
    if state.get("dream_mode") != "rpg" or not dream_id:
        return RpgObservabilityResponse(**observability(None, health="missing", recovery_source="no_active_rpg_state"))
    core, health = load(_uid(), dream_id, char_id=str(state.get("char_id") or _char_id()))
    return RpgObservabilityResponse(**observability(core, health=health, recovery_source="disk"))
