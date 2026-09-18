"""Bounded, read-only Memory Event tools for the owner Path C loop."""
from __future__ import annotations

import json
from typing import Any

from core.memory import event_query
from core.memory import source_policy
from core.memory.scope import MemoryScope
from core.tools.tool_result import ToolResult

MAX_EVENTS = 20
MAX_TEXT_CHARS = 12_000
MAX_EVIDENCE_CHARS = 1_200
MAX_RELATION_DEPTH = 1


def _scope(user_id: str, char_id: str) -> MemoryScope:
    return MemoryScope.reality_scope(str(user_id), char_id)


def _event_item(event: dict[str, Any] | None, *, relation: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not event:
        return None
    evidence = str(event.get("memory_text") or event.get("visible_text") or "")[:MAX_EVIDENCE_CHARS]
    item = {
        "event_id": str(event.get("event_id") or ""),
        "occurred_at": event.get("occurred_at"),
        "actor": str(event.get("actor") or ""),
        "topic": list(event.get("topics") or [])[:20],
        "kind": str(event.get("kind") or ""),
        "source": str(event.get("source") or ""),
        "turn_id": str(event.get("turn_id") or ""),
        "evidence_text": evidence,
    }
    if relation is not None:
        item["relation"] = relation
    return item


def _encode(payload: dict[str, Any]) -> str:
    """Keep tool output bounded even when the ledger contains long text."""
    payload = dict(payload)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= MAX_TEXT_CHARS:
        return serialized
    for key in ("events", "before", "after", "items"):
        values = payload.get(key)
        if isinstance(values, list):
            while values and len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) > MAX_TEXT_CHARS:
                values.pop()
            payload["truncated"] = True
            break
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return serialized[:MAX_TEXT_CHARS]


def _unknown(reason: str, *, event_id: str = "", detail: str = "") -> ToolResult:
    payload = {"status": "outcome_unknown", "reason": reason}
    if event_id:
        payload["event_id"] = event_id
    if detail:
        payload["detail"] = detail[:160]
    text = _encode(payload)
    return ToolResult(
        raw_data=text,
        safe_summary=text,
        meta={"validity": "outcome_unknown", "execution_status": "outcome_unknown", "failure_reason": reason},
    )


def _success(payload: dict[str, Any]) -> ToolResult:
    text = _encode(payload)
    return ToolResult(
        raw_data=text,
        safe_summary=text,
        meta={"validity": "current_turn", "truncated": bool(payload.get("truncated"))},
    )


def _query_error(exc: Exception, *, event_id: str = "") -> ToolResult:
    reason = getattr(exc, "code", "query_failed")
    return _unknown(str(reason), event_id=event_id)


async def expand_event_window_wrapper(
    user_id: str,
    event_id: str,
    before: int = 10,
    after: int = 10,
    *,
    char_id: str,
) -> str | ToolResult:
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 256:
        return _unknown("invalid_event_id")
    if before < 0 or after < 0 or before > MAX_EVENTS or after > MAX_EVENTS:
        return _unknown("limit_exceeded", event_id=event_id)
    try:
        result = event_query.window(_scope(user_id, char_id), event_id, before=before, after=after)
    except event_query.EventQueryError as exc:
        return _query_error(exc, event_id=event_id)
    if result is None:
        return _unknown("event_not_found", event_id=event_id)
    return _success({
        "status": "ok",
        "event": _event_item(result.get("event"), relation={"type": "target"}),
        "before": [_event_item(item, relation={"type": "temporal_window", "direction": "before"}) for item in result.get("before", [])],
        "after": [_event_item(item, relation={"type": "temporal_window", "direction": "after"}) for item in result.get("after", [])],
        "truncation_reason": result.get("truncation_reason", ""),
    })


async def get_related_events_wrapper(
    user_id: str,
    event_id: str,
    relation_types: list[str] | None = None,
    cursor: str = "",
    limit: int = 20,
    depth: int = 1,
    *,
    char_id: str,
) -> str | ToolResult:
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 256:
        return _unknown("invalid_event_id")
    if len(cursor) > 1024 or limit < 1 or limit > MAX_EVENTS or depth != MAX_RELATION_DEPTH:
        return _unknown("limit_or_depth_exceeded", event_id=event_id)
    allowed = {str(value) for value in (relation_types or []) if str(value)}
    if len(allowed) > 20 or any(len(value) > 64 for value in allowed):
        return _unknown("relation_filter_exceeded", event_id=event_id)
    try:
        result = event_query.related(
            _scope(user_id, char_id), event_id, cursor=cursor, limit=limit,
            relation_types=allowed or None,
        )
    except event_query.EventQueryError as exc:
        return _query_error(exc, event_id=event_id)
    if result is None:
        return _unknown("event_not_found", event_id=event_id)
    items = []
    for item in result.get("items", []):
        if allowed and str(item.get("edge_type") or "") not in allowed:
            continue
        related = _event_item(item.get("event"), relation={
            "type": item.get("edge_type", ""),
            "direction": item.get("direction", ""),
            "edge_id": item.get("edge_id"),
            "relations": [
                {"type": relation.get("relation_type") or relation.get("edge_type", ""),
                 "direction": relation.get("direction", ""), "edge_id": relation.get("edge_id")}
                for relation in item.get("relations", [])
            ],
        })
        if related:
            items.append(related)
    return _success({
        "status": "ok",
        "event_id": event_id,
        "depth": depth,
        "items": items,
        "next_cursor": result.get("next_cursor", ""),
        "truncation_reason": result.get("truncation_reason", ""),
    })


async def search_events_wrapper(
    user_id: str,
    query: str = "",
    actor: str = "",
    kind: str = "",
    source: str = "",
    occurred_after: float | None = None,
    occurred_before: float | None = None,
    cursor: str = "",
    limit: int = 20,
    *,
    char_id: str,
) -> str | ToolResult:
    if len(query) > 256 or len(actor) > 64 or len(kind) > 64 or len(source) > 128 or len(cursor) > 1024:
        return _unknown("argument_length_exceeded")
    if limit < 1 or limit > MAX_EVENTS:
        return _unknown("limit_exceeded")
    if source and not source_policy.role_source_allowed(source):
        source_policy.record_filtered_query()
        return _unknown("source_not_available")
    try:
        result = event_query.search(
            _scope(user_id, char_id), text=query, actor=actor, kind=kind, source=source,
            occurred_after=occurred_after, occurred_before=occurred_before,
            cursor=cursor, limit=limit,
        )
    except event_query.EventQueryError as exc:
        return _query_error(exc)
    return _success({
        "status": "ok",
        "items": [_event_item(item) for item in result.get("items", [])],
        "next_cursor": result.get("next_cursor", ""),
        "truncation_reason": result.get("truncation_reason", ""),
    })
