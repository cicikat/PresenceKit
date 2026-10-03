"""Read-only Memory Event evidence-ledger query endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from admin.auth import require_scopes
from core.memory.event_query import EventQueryError
from core.memory.scope import MemoryScope

router = APIRouter()

_MAX_WINDOW = 50
_MAX_RESULTS = 100


def _scope(uid: str, char_id: str, realm: str) -> MemoryScope:
    if realm != "reality":
        raise HTTPException(status_code=422, detail={"code": "unsupported_realm"})
    try:
        from core.asset_registry import get_registry

        get_registry().resolve(char_id, "character")
        return MemoryScope.reality_scope(uid, char_id)
    except (TypeError, ValueError):
        # Deliberately avoid reflecting arbitrary user/character values.
        raise HTTPException(status_code=422, detail={"code": "invalid_scope"}) from None


def _trace(scope: MemoryScope, query_type: str, result: dict[str, Any] | None, *, outcome: str = "ok") -> None:
    from core.memory.event_query import record_query_trace

    result_count = len(result.get("items", [])) if result is not None else 0
    if result is not None:
        result_count += len(result.get("before", [])) + len(result.get("after", []))
        if "event" in result and result["event"] is not None:
            result_count += 1
    record_query_trace(
        scope,
        query_type=query_type,
        result_count=result_count,
        truncation_reason=(result or {}).get("truncation_reason", ""),
        outcome=outcome,
    )


def _query_error(scope: MemoryScope, query_type: str, exc: EventQueryError) -> HTTPException:
    _trace(scope, query_type, None, outcome=exc.code)
    status = 422 if exc.code in {"invalid_cursor", "invalid_time_range", "invalid_scope"} else 503
    return HTTPException(status_code=status, detail={"code": exc.code})


@router.get("/memory-events/search", summary="检索 scoped Memory Event 证据账本")
async def search_memory_events(
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    realm: str = Query("reality", max_length=16),
    q: str = Query("", max_length=256),
    actor: str = Query("", max_length=64),
    kind: str = Query("", max_length=64),
    source: str = Query("", max_length=128),
    occurred_after: float | None = Query(None, ge=0),
    occurred_before: float | None = Query(None, ge=0),
    cursor: str = Query("", max_length=1024),
    limit: int = Query(25, ge=1, le=_MAX_RESULTS),
    _auth=Depends(require_scopes("memory.read")),
):
    """Seed lookup; only the requested reality scope is ever read."""
    from core.memory import event_query

    scope = _scope(uid, char_id, realm)
    try:
        result = event_query.search(
            scope,
            text=q,
            actor=actor,
            kind=kind,
            source=source,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            cursor=cursor,
            limit=limit,
        )
    except EventQueryError as exc:
        raise _query_error(scope, "search", exc)
    _trace(scope, "search", result)
    return {"scope": {"uid": uid, "char_id": char_id, "realm": realm}, **result}


@router.get("/memory-events/query-trace", summary="读取脱敏 Memory Event 查询审计")
async def get_memory_event_query_trace(
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    realm: str = Query("reality", max_length=16),
    limit: int = Query(25, ge=1, le=_MAX_RESULTS),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.memory import event_query

    scope = _scope(uid, char_id, realm)
    try:
        entries = event_query.query_traces(scope, limit=limit)
    except EventQueryError as exc:
        raise _query_error(scope, "trace", exc)
    return {
        "scope": {"uid": uid, "char_id": char_id, "realm": realm},
        "entries": entries,
        "count": len(entries),
    }


@router.get("/memory-events/lineage/episodes/{episode_id}", summary="追溯情景记忆的事件证据")
async def get_episode_lineage(
    episode_id: str = Path(..., min_length=1, max_length=256),
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    realm: str = Query("reality", max_length=16),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.memory import lineage

    scope = _scope(uid, char_id, realm)
    try:
        result = lineage.resolve_episode(uid, episode_id, char_id=char_id)
    except EventQueryError as exc:
        raise _query_error(scope, "lineage_episode", exc)
    if result is None:
        _trace(scope, "lineage_episode", None, outcome="not_found")
        raise HTTPException(status_code=404, detail={"code": "episode_not_found"})
    _trace(scope, "lineage_episode", {"items": result.get("events", [])})
    return {"scope": {"uid": uid, "char_id": char_id, "realm": realm}, **result}


@router.get("/memory-events/lineage/storyline/{arc_id}/nodes/{node_id}", summary="追溯故事线节点的事件证据")
async def get_storyline_node_lineage(
    arc_id: str = Path(..., min_length=1, max_length=256),
    node_id: str = Path(..., min_length=1, max_length=256),
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    realm: str = Query("reality", max_length=16),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.memory import lineage

    scope = _scope(uid, char_id, realm)
    try:
        result = lineage.resolve_storyline_node(uid, arc_id, node_id, char_id=char_id)
    except EventQueryError as exc:
        raise _query_error(scope, "lineage_storyline", exc)
    if result is None:
        _trace(scope, "lineage_storyline", None, outcome="not_found")
        raise HTTPException(status_code=404, detail={"code": "storyline_node_not_found"})
    _trace(scope, "lineage_storyline", {"items": result.get("events", [])})
    return {"scope": {"uid": uid, "char_id": char_id, "realm": realm}, **result}


@router.get("/memory-events/lineage/dry-run", summary="统计可确定回填的 Memory Event 血缘")
async def get_memory_event_lineage_dry_run(
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    realm: str = Query("reality", max_length=16),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.memory import lineage

    scope = _scope(uid, char_id, realm)
    try:
        result = lineage.dry_run(uid, char_id=char_id)
    except EventQueryError as exc:
        raise _query_error(scope, "lineage_dry_run", exc)
    _trace(scope, "lineage_dry_run", None)
    return {"scope": {"uid": uid, "char_id": char_id, "realm": realm}, **result}


@router.get("/memory-events/{event_id}", summary="读取单条 scoped Memory Event 证据")
async def get_memory_event(
    event_id: str = Path(..., min_length=1, max_length=256),
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    realm: str = Query("reality", max_length=16),
    source: str = Query("", max_length=128, description="Explicit source enables forensic access to isolated evidence"),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.memory import event_query

    scope = _scope(uid, char_id, realm)
    try:
        event = event_query.get_event(scope, event_id, source=source)
    except EventQueryError as exc:
        raise _query_error(scope, "event", exc)
    if event is None:
        _trace(scope, "event", None, outcome="not_found")
        raise HTTPException(status_code=404, detail={"code": "event_not_found"})
    result = {"event": event}
    _trace(scope, "event", result)
    return {"scope": {"uid": uid, "char_id": char_id, "realm": realm}, **result}


@router.delete("/memory-events/{event_id}", summary="墓碑化一条 scoped Memory Event 证据")
async def tombstone_memory_event(
    event_id: str = Path(..., min_length=1, max_length=256),
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    realm: str = Query("reality", max_length=16),
    _auth=Depends(require_scopes("admin")),
):
    """Forget payload fields while retaining the event and its relation edges.

    Physical deletion is deliberately unavailable until an owner-confirmed
    retention policy exists.  Derived memories are not silently rewritten;
    their lineage continues to point at a visibly tombstoned source.
    """
    from core.memory import event_store

    scope = _scope(uid, char_id, realm)
    result = event_store.tombstone_event(scope, event_id)
    if result.error_code == "not_found":
        raise HTTPException(status_code=404, detail={"code": "event_not_found"})
    if not result.ok:
        status = 422 if result.error_code in {"invalid_event", "invalid_scope"} else 503
        raise HTTPException(status_code=status, detail={"code": result.error_code})
    return {
        "scope": {"uid": uid, "char_id": char_id, "realm": realm},
        "event_id": result.event_id,
        "tombstoned": True,
        "changed": result.changed,
        "physical_delete": "disabled_pending_owner_policy",
        "edges": "retained",
        "derived_memories": {"dossiers": "invalidated", "others": "not_cascaded"},
    }


@router.get("/memory-events/{event_id}/window", summary="读取事件的确定性前后窗口")
async def get_memory_event_window(
    event_id: str = Path(..., min_length=1, max_length=256),
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    realm: str = Query("reality", max_length=16),
    before: int = Query(10, ge=0, le=_MAX_WINDOW),
    after: int = Query(10, ge=0, le=_MAX_WINDOW),
    source: str = Query("", max_length=128, description="Explicit source enables forensic access to isolated evidence"),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.memory import event_query

    scope = _scope(uid, char_id, realm)
    try:
        result = event_query.window(scope, event_id, before=before, after=after, source=source)
    except EventQueryError as exc:
        raise _query_error(scope, "window", exc)
    if result is None:
        _trace(scope, "window", None, outcome="not_found")
        raise HTTPException(status_code=404, detail={"code": "event_not_found"})
    _trace(scope, "window", result)
    return {"scope": {"uid": uid, "char_id": char_id, "realm": realm}, **result}


@router.get("/memory-events/{event_id}/related", summary="读取确定性关联事件边")
async def get_related_memory_events(
    event_id: str = Path(..., min_length=1, max_length=256),
    uid: str = Query(..., min_length=1, max_length=128),
    char_id: str = Query(..., min_length=1, max_length=128),
    realm: str = Query("reality", max_length=16),
    cursor: str = Query("", max_length=1024),
    limit: int = Query(25, ge=1, le=_MAX_RESULTS),
    source: str = Query("", max_length=128, description="Explicit source enables forensic access to isolated evidence"),
    _auth=Depends(require_scopes("memory.read")),
):
    from core.memory import event_query

    scope = _scope(uid, char_id, realm)
    try:
        result = event_query.related(scope, event_id, cursor=cursor, limit=limit, source=source)
    except EventQueryError as exc:
        raise _query_error(scope, "related", exc)
    if result is None:
        _trace(scope, "related", None, outcome="not_found")
        raise HTTPException(status_code=404, detail={"code": "event_not_found"})
    _trace(scope, "related", result)
    return {"scope": {"uid": uid, "char_id": char_id, "realm": realm}, **result}
