"""Authenticated control and observation for character memory dossiers."""
from __future__ import annotations

from pathlib import Path as FilePath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.agent_runtime.models import TaskPrincipal
from core.memory.scope import MemoryScope

router = APIRouter()


def _scope(uid: str, char_id: str) -> MemoryScope:
    try:
        from core.asset_registry import get_registry
        from core.data_paths import safe_user_id

        safe_user_id(uid)
        safe_user_id(char_id)
        get_registry().resolve(char_id, "character")
        return MemoryScope.reality_scope(uid, char_id)
    except (TypeError, ValueError, KeyError):
        raise HTTPException(status_code=422, detail={"code": "invalid_scope"}) from None


class ConsolidationSettingsUpdate(BaseModel):
    enabled: bool | None = None
    paused: bool | None = None
    pause_reason: str | None = Field(None, max_length=128)
    night_start_hour: int | None = Field(None, ge=0, le=23)
    night_end_hour: int | None = Field(None, ge=0, le=23)
    idle_seconds: int | None = Field(None, ge=60, le=86400)
    batch_size: int | None = Field(None, ge=1, le=100)
    max_input_chars: int | None = Field(None, ge=1000, le=48000)
    max_tokens_per_call: int | None = Field(None, ge=64, le=4000)
    daily_call_budget: int | None = Field(None, ge=1, le=100)
    daily_token_budget: int | None = Field(None, ge=64, le=100000)
    daily_wall_seconds: int | None = Field(None, ge=1, le=86400)
    per_scope_daily_calls: int | None = Field(None, ge=1, le=50)
    per_scope_daily_tokens: int | None = Field(None, ge=64, le=50000)
    call_timeout_seconds: int | None = Field(None, ge=1, le=600)
    retry_backoff_seconds: int | None = Field(None, ge=1, le=86400)
    background_preset: str | None = Field(None, max_length=128)


class ConsolidationControl(BaseModel):
    action: Literal["start", "stop", "pause", "resume", "revoke", "recover_unknown"]
    uid: str | None = Field(None, min_length=1, max_length=128)
    char_id: str | None = Field(None, min_length=1, max_length=128)
    reason: str = Field("", max_length=128)


def _write_settings(changes: dict) -> dict:
    from core import config_loader

    config_path = config_loader.get_config_path()
    document = read_config_file(config_path)
    section = document.setdefault("memory_consolidation", {})
    section.update(changes)
    write_config_file(config_path, document)
    config_loader.reload_config()
    from core.memory.consolidation_worker import config

    return config()


@router.get("/observability/memory-consolidation", summary="Read dossier consolidation metadata")
async def observe_memory_consolidation(
    uid: str | None = Query(None, min_length=1, max_length=128),
    char_id: str | None = Query(None, min_length=1, max_length=128),
    _auth=Depends(require_scopes("state.read")),
):
    from core.memory.consolidation_worker import runtime_snapshot

    if (uid is None) != (char_id is None):
        raise HTTPException(status_code=422, detail={"code": "uid_and_char_id_required"})
    if uid is not None:
        _scope(uid, char_id or "")
    try:
        return runtime_snapshot(uid=uid, char_id=char_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail={"code": str(exc)[:128]}) from exc


@router.get("/observability/memory-history-inventory", summary="Read-only history reconciliation inventory")
async def observe_memory_history_inventory(
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    _auth=Depends(require_scopes("state.read")),
):
    """Expose only redacted counts/revisions; this endpoint never initializes data."""
    _scope(uid, char_id)
    from core.memory.history_reconciliation import build_inventory
    try:
        return build_inventory(uid, char_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail={"code": str(exc)[:128]}) from exc


@router.get("/observability/memory-history-reconciliation", summary="Reconciliation batch ledger status")
async def observe_memory_history_reconciliation(
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    _auth=Depends(require_scopes("state.read")),
):
    from core.memory.history_reconciliation import status
    _scope(uid, char_id)
    return status(MemoryScope.reality_scope(uid, char_id))


@router.post("/memory-history-reconciliation/manifest", summary="Create a dry-run reconciliation manifest")
async def create_memory_history_manifest(
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    _auth=Depends(require_scopes("admin")),
):
    from core.memory.history_reconciliation import create_manifest
    _scope(uid, char_id)
    return create_manifest(MemoryScope.reality_scope(uid, char_id))


@router.post("/memory-history-reconciliation/control", summary="Pause, resume, or dry-run reconciliation")
async def control_memory_history_reconciliation(
    body: dict,
    _auth=Depends(require_scopes("admin")),
):
    action = str(body.get("action") or "")
    uid, char_id = str(body.get("uid") or ""), str(body.get("char_id") or "")
    if action not in {"pause", "resume", "dry_run", "apply"} or not uid or not char_id:
        raise HTTPException(status_code=422, detail={"code": "invalid_reconciliation_control"})
    _scope(uid, char_id)
    from core.memory import history_reconciliation
    scope = MemoryScope.reality_scope(uid, char_id)
    if action == "dry_run":
        return history_reconciliation.apply_dry_run(scope, backup_verified=False)
    if action == "apply":
        backup_path = str(body.get("backup_path") or "").strip()
        if not backup_path:
            raise HTTPException(status_code=422, detail={"code": "backup_path_required"})
        verification = history_reconciliation.verify_backup_snapshot(FilePath(backup_path))
        if not verification["verified"]:
            raise HTTPException(status_code=409, detail={"code": "backup_not_verified", "errors": verification["errors"]})
        batch_size = int(body.get("batch_size") or 10)
        if not 1 <= batch_size <= 100:
            raise HTTPException(status_code=422, detail={"code": "invalid_batch_size"})
        return history_reconciliation.apply_batch(
            scope, backup={"verified": True, "backup_path": backup_path},
            batch_size=batch_size, dry_run=False,
        )
    return history_reconciliation.set_paused(scope, action == "pause", reason=str(body.get("reason") or "admin"))


@router.get("/memory/dossiers", summary="Search character memory dossiers")
async def search_memory_dossiers(
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    q: str = Query("", max_length=256),
    limit: int = Query(20, ge=1, le=20),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.memory import dossiers

    scope = _scope(uid, char_id)
    return {"scope": {"char_id": char_id, "realm": "reality"},
            "items": dossiers.search(scope, q, limit=limit)}


@router.get("/memory/dossiers/{dossier_id}", summary="Read one character memory dossier")
async def read_memory_dossier(
    dossier_id: str = Path(..., min_length=32, max_length=32),
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.memory import dossiers

    scope = _scope(uid, char_id)
    try:
        value = dossiers.read(scope, dossier_id)
    except dossiers.DossierError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code}) from exc
    if value is None:
        raise HTTPException(status_code=404, detail={"code": "dossier_not_found"})
    return {"scope": {"char_id": char_id, "realm": "reality"}, "dossier": value}


@router.get("/memory/dossiers/{dossier_id}/events", summary="Read dossier event references")
async def read_memory_dossier_events(
    dossier_id: str = Path(..., min_length=32, max_length=32),
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.memory import dossiers

    try:
        result = dossiers.dossier_events(_scope(uid, char_id), dossier_id, offset=offset, limit=limit)
    except dossiers.DossierError as exc:
        status = 404 if exc.code == "dossier_not_found" else 422
        raise HTTPException(status_code=status, detail={"code": exc.code}) from exc
    return {"scope": {"char_id": char_id, "realm": "reality"}, **result}


@router.patch("/settings/memory-consolidation", summary="Update dossier consolidation settings")
async def update_memory_consolidation_settings(
    body: ConsolidationSettingsUpdate,
    _auth=Depends(require_scopes("admin")),
):
    changes = body.model_dump(exclude_none=True)
    return {"settings": _write_settings(changes)}


@router.post("/memory-consolidation/control", summary="Control dossier consolidation lifecycle")
async def control_memory_consolidation(
    body: ConsolidationControl,
    _auth=Depends(require_scopes("admin")),
):
    from core.memory import consolidation_worker

    if (body.uid is None) != (body.char_id is None):
        raise HTTPException(status_code=422, detail={"code": "uid_and_char_id_required"})
    if body.action == "recover_unknown" and (not body.uid or not body.char_id):
        raise HTTPException(status_code=422, detail={"code": "uid_and_char_id_required"})
    if body.uid and body.char_id:
        _scope(body.uid, body.char_id)
    changes: dict = {}
    if body.action == "start":
        changes = {"enabled": True, "paused": False, "pause_reason": ""}
    elif body.action == "stop":
        changes = {"enabled": False}
    elif body.action == "pause":
        changes = {"paused": True, "pause_reason": body.reason or "admin_paused"}
    elif body.action == "resume":
        changes = {"paused": False, "pause_reason": ""}
    elif body.action == "revoke":
        current = consolidation_worker.config()
        changes = {"enabled": False, "paused": False, "pause_reason": "",
                   "grant_revision": int(current["grant_revision"]) + 1}
    settings = _write_settings(changes) if changes else consolidation_worker.config()
    result = {"changed": 0, "receipts_reconciled": 0, "unknown_failed": 0}
    if body.uid and body.char_id and body.action not in {"start", "stop"}:
        result = consolidation_worker.control_scope(
            TaskPrincipal.reality(body.uid, body.char_id), body.action,
        )
    return {"action": body.action, "settings": settings, "result": result}
