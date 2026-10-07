"""Read-only scoped queries for the Memory Event evidence ledger.

The module deliberately does not share the event-store write initialization
path: an absent ledger remains absent, and a broken ledger fails closed for
the caller without affecting chat or memory writes.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from core.memory import event_store
from core.memory import source_policy
from core.memory.scope import MemoryScope
from core.safe_write import safe_append_jsonl

MAX_EVENT_TEXT_CHARS = 20_000
TRACE_LIMIT = 100


class EventQueryError(Exception):
    """A stable, content-free query failure code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _path(scope: MemoryScope) -> Path:
    if scope.domain != "reality":
        raise EventQueryError("invalid_scope")
    try:
        return event_store.resolve_path(scope, "event_store")
    except (TypeError, ValueError) as exc:
        raise EventQueryError("invalid_scope") from exc


def _connect(scope: MemoryScope, *, busy_timeout_ms: int = 5000) -> tuple[Path, sqlite3.Connection] | None:
    path = _path(scope)
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=max(0.001, busy_timeout_ms / 1000.0),
            check_same_thread=False,
        )
        connection.execute(f"PRAGMA busy_timeout={max(1, int(busy_timeout_ms))}")
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if version != event_store.SCHEMA_VERSION or not {"events", "event_edges", "event_topics"} <= tables:
            connection.close()
            raise EventQueryError("schema_mismatch")
        return path, connection
    except sqlite3.Error as exc:
        raise EventQueryError("database_error") from exc


def _safe_text(value: object) -> tuple[str, bool]:
    text = str(value or "")
    return text[:MAX_EVENT_TEXT_CHARS], len(text) > MAX_EVENT_TEXT_CHARS


def _safe_media_refs(raw: object) -> list[dict[str, str]]:
    try:
        value = json.loads(str(raw or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    refs: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        projected = {
            key: str(item[key])[:512]
            for key in ("kind", "filename", "sha256", "availability")
            if item.get(key) not in (None, "")
        }
        if projected:
            refs.append(projected)
    return refs


def _event_projection(row: sqlite3.Row) -> dict[str, Any]:
    tombstoned = row["redaction_state"] == "tombstoned"
    raw_text, raw_truncated = _safe_text("" if tombstoned else row["raw_text"])
    visible_text, visible_truncated = _safe_text("" if tombstoned else row["visible_text"])
    memory_text, memory_truncated = _safe_text("" if tombstoned else row["memory_text"])
    return {
        "event_id": row["event_id"],
        "turn_id": row["turn_id"],
        "seq": row["seq"],
        "occurred_at": row["occurred_at"],
        "ingested_at": row["ingested_at"],
        "uid": row["uid"],
        "char_id": row["char_id"],
        "realm": row["realm"],
        "kind": row["kind"],
        "actor": row["actor"],
        "channel": row["channel"],
        "stream": row["stream"] if "stream" in row.keys() else row["channel"],
        "source": row["source"],
        "redaction_state": row["redaction_state"],
        "tombstoned": tombstoned,
        "raw_text": raw_text,
        "visible_text": visible_text,
        "memory_text": memory_text,
        "media_refs": [] if tombstoned else _safe_media_refs(row["media_refs_json"]),
        "truncated_fields": [
            name
            for name, truncated in (
                ("raw_text", raw_truncated),
                ("visible_text", visible_truncated),
                ("memory_text", memory_truncated),
            )
            if truncated
        ],
    }


def _event_projection_with_topics(
    connection: sqlite3.Connection,
    scope: MemoryScope,
    row: sqlite3.Row,
) -> dict[str, Any]:
    projected = _event_projection(row)
    try:
        topic_rows = connection.execute(
            "SELECT topic FROM event_topics WHERE uid = ? AND char_id = ? AND event_id = ? ORDER BY topic ASC LIMIT 20",
            (scope.uid, scope.character_id, row["event_id"]),
        ).fetchall()
        projected["topics"] = [str(item[0])[:128] for item in topic_rows]
    except sqlite3.Error:
        projected["topics"] = []
    return projected


def _observe_default_source_filter() -> None:
    """Count filtered queries, not the current inventory of isolated rows."""
    source_policy.record_filtered_query()


def _find(
    connection: sqlite3.Connection,
    scope: MemoryScope,
    event_id: str,
    *,
    source: str = "",
    include_isolated: bool = False,
) -> sqlite3.Row | None:
    where = ["uid = ?", "char_id = ?", "realm = ?", "event_id = ?"]
    params: list[Any] = [scope.uid, scope.character_id, scope.domain, event_id]
    if source:
        where.append("source = ?")
        params.append(source)
    else:
        predicate, policy_params = source_policy.sql_predicate(include_isolated=include_isolated)
        if predicate:
            where.append(predicate.removeprefix(" AND "))
            params.extend(policy_params)
    return connection.execute(
        f"SELECT * FROM events WHERE {' AND '.join(where)}", params,
    ).fetchone()


def get_event(
    scope: MemoryScope,
    event_id: str,
    *,
    source: str = "",
    include_isolated: bool = False,
) -> dict[str, Any] | None:
    opened = _connect(scope)
    if opened is None:
        return None
    path, connection = opened
    with event_store._lock_for(path):
        try:
            if not source and not include_isolated:
                _observe_default_source_filter()
            row = _find(connection, scope, event_id, source=source, include_isolated=include_isolated)
            return _event_projection_with_topics(connection, scope, row) if row is not None else None
        except sqlite3.Error as exc:
            raise EventQueryError("database_error") from exc
        finally:
            connection.close()


def window(
    scope: MemoryScope,
    event_id: str,
    *,
    before: int,
    after: int,
    source: str = "",
    include_isolated: bool = False,
    busy_timeout_ms: int = 5000,
    messages_only: bool = False,
) -> dict[str, Any] | None:
    opened = _connect(scope, busy_timeout_ms=busy_timeout_ms)
    if opened is None:
        return None
    path, connection = opened
    with event_store._lock_for(path):
        try:
            if not source and not include_isolated:
                _observe_default_source_filter()
            target = _find(connection, scope, event_id, source=source, include_isolated=include_isolated)
            if target is None:
                return None
            message_kinds = ('user_message', 'assistant_message', 'trigger_assistant')
            if messages_only and (target['kind'] not in message_kinds or target['redaction_state'] == 'tombstoned'):
                return None
            source_where, source_params = source_policy.sql_predicate(include_isolated=include_isolated)
            exact_source = source or None
            source_clause = " AND source = ?" if exact_source else source_where
            source_values: tuple[Any, ...] = (exact_source,) if exact_source else source_params
            if messages_only:
                source_clause += " AND kind IN ('user_message','assistant_message','trigger_assistant') AND redaction_state != 'tombstoned'"
            params = (scope.uid, scope.character_id, scope.domain, *source_values, target["occurred_at"], target["occurred_at"], target["seq"], target["seq"], target["event_id"])
            before_rows = connection.execute(
                """SELECT * FROM events WHERE uid = ? AND char_id = ? AND realm = ?
                """ + source_clause + """
                AND (occurred_at < ? OR (occurred_at = ? AND (seq < ? OR (seq = ? AND event_id < ?))))
                ORDER BY occurred_at DESC, seq DESC, event_id DESC LIMIT ?""",
                (*params, before + 1),
            ).fetchall()
            after_rows = connection.execute(
                """SELECT * FROM events WHERE uid = ? AND char_id = ? AND realm = ?
                """ + source_clause + """
                AND (occurred_at > ? OR (occurred_at = ? AND (seq > ? OR (seq = ? AND event_id > ?))))
                ORDER BY occurred_at ASC, seq ASC, event_id ASC LIMIT ?""",
                (*params, after + 1),
            ).fetchall()
            has_more_before = len(before_rows) > before
            has_more_after = len(after_rows) > after
            before_rows = before_rows[:before]
            after_rows = after_rows[:after]
            return {
                "event": _event_projection_with_topics(connection, scope, target),
                "before": [_event_projection_with_topics(connection, scope, row) for row in reversed(before_rows)],
                "after": [_event_projection_with_topics(connection, scope, row) for row in after_rows],
                "truncation_reason": "window_limit" if has_more_before or has_more_after else "",
            }
        except sqlite3.Error as exc:
            raise EventQueryError("database_error") from exc
        finally:
            connection.close()


def _encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, expected_kind: str, event_id: str = "") -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise EventQueryError("invalid_cursor") from None
    if not isinstance(decoded, dict) or decoded.get("v") != 1 or decoded.get("kind") != expected_kind:
        raise EventQueryError("invalid_cursor")
    if event_id and decoded.get("event_id") != event_id:
        raise EventQueryError("invalid_cursor")
    return decoded


def related(
    scope: MemoryScope,
    event_id: str,
    *,
    cursor: str,
    limit: int,
    relation_types: set[str] | None = None,
    source: str = "",
    include_isolated: bool = False,
    busy_timeout_ms: int = 5000,
) -> dict[str, Any] | None:
    decoded = _decode_cursor(cursor, "related", event_id)
    edge_after = int(decoded.get("edge_id", 0)) if decoded else 0
    if edge_after < 0:
        raise EventQueryError("invalid_cursor")
    opened = _connect(scope, busy_timeout_ms=busy_timeout_ms)
    if opened is None:
        return None
    path, connection = opened
    with event_store._lock_for(path):
        try:
            if not source and not include_isolated:
                _observe_default_source_filter()
            if _find(connection, scope, event_id, source=source, include_isolated=include_isolated) is None:
                return None
            relation_types = relation_types or set()
            relation_sql = ""
            relation_params: tuple[Any, ...] = ()
            if relation_types:
                placeholders = ", ".join("?" for _ in relation_types)
                relation_sql = f" AND COALESCE(NULLIF(e.relation_type, ''), e.edge_type) IN ({placeholders})"
                relation_params = tuple(sorted(relation_types))
            exact_source = source or None
            if exact_source:
                source_clause = " AND related.source = ?"
                source_values: tuple[Any, ...] = (exact_source,)
            else:
                source_clause, source_values = source_policy.sql_predicate(
                    "related.source", include_isolated=include_isolated,
                )
            rows = connection.execute(
                """SELECT e.edge_id, e.edge_type,
                   COALESCE(NULLIF(e.relation_type, ''), e.edge_type) AS relation_type,
                   e.origin, e.confidence, e.schema_version, e.created_at,
                   e.from_event_id, e.to_event_id,
                   related.*
                FROM event_edges AS e
                LEFT JOIN events AS related
                  ON related.uid = e.uid AND related.char_id = e.char_id
                 AND related.realm = ?
                 AND related.event_id = CASE WHEN e.from_event_id = ? THEN e.to_event_id ELSE e.from_event_id END
                WHERE e.uid = ? AND e.char_id = ? AND (e.from_event_id = ? OR e.to_event_id = ?) AND e.edge_id > ?"""
                + source_clause + relation_sql + " ORDER BY e.edge_id ASC LIMIT ?",
                (scope.domain, event_id, scope.uid, scope.character_id, event_id, event_id, edge_after, *source_values, *relation_params, 1001),
            ).fetchall()
            # One event can have several deterministic relations to the same
            # neighbour (for example same_turn + reply_to).  Paginate unique
            # neighbours so a short page does not repeat that neighbour before
            # exposing the next event.  Edges for one append are contiguous,
            # so advancing through the repeated edge ids is deterministic.
            selected: list[list[sqlite3.Row]] = []
            selected_by_id: dict[str, list[sqlite3.Row]] = {}
            cursor_edge_id = edge_after
            has_more = False
            for row in rows:
                other_id = row["to_event_id"] if row["from_event_id"] == event_id else row["from_event_id"]
                if other_id in selected_by_id:
                    selected_by_id[other_id].append(row)
                    cursor_edge_id = row["edge_id"]
                    continue
                if len(selected) >= limit:
                    has_more = True
                    break
                group = [row]
                selected.append(group)
                selected_by_id[other_id] = group
                cursor_edge_id = row["edge_id"]
            if len(rows) == 1001 and not has_more:
                has_more = True
            items = []
            for group in selected:
                row = group[0]
                other_id = row["to_event_id"] if row["from_event_id"] == event_id else row["from_event_id"]
                relations = [{
                    "edge_id": edge["edge_id"],
                    "edge_type": edge["relation_type"] or edge["edge_type"],
                    "relation_type": edge["relation_type"] or edge["edge_type"],
                    "origin": edge["origin"],
                    "confidence": edge["confidence"],
                    "schema_version": edge["schema_version"],
                    "edge_created_at": edge["created_at"],
                    "direction": "outgoing" if edge["from_event_id"] == event_id else "incoming",
                } for edge in group]
                items.append({
                    "edge_id": row["edge_id"],
                    "edge_type": row["relation_type"] or row["edge_type"],
                    "relation_type": row["relation_type"] or row["edge_type"],
                    "origin": row["origin"],
                    "confidence": row["confidence"],
                    "schema_version": row["schema_version"],
                    "edge_created_at": row["created_at"],
                    "direction": "outgoing" if row["from_event_id"] == event_id else "incoming",
                    "related_event_id": other_id,
                    "dangling": row["event_id"] is None,
                    "relations": relations,
                    "event": _event_projection_with_topics(connection, scope, row) if row["event_id"] is not None else None,
                })
            return {
                "items": items,
                "next_cursor": _encode_cursor({"v": 1, "kind": "related", "event_id": event_id, "edge_id": cursor_edge_id}) if has_more and selected else "",
                "truncation_reason": "limit" if has_more else "",
            }
        except sqlite3.Error as exc:
            raise EventQueryError("database_error") from exc
        finally:
            connection.close()


def search(
    scope: MemoryScope,
    *,
    text: str,
    actor: str,
    kind: str,
    source: str,
    occurred_after: float | None,
    occurred_before: float | None,
    cursor: str,
    limit: int,
    order: str = "asc",
    busy_timeout_ms: int = 5000,
) -> dict[str, Any]:
    if order not in {"asc", "desc"} or (order == "desc" and cursor):
        raise EventQueryError("invalid_cursor")
    decoded = _decode_cursor(cursor, "search")
    if occurred_after is not None and occurred_before is not None and occurred_after > occurred_before:
        raise EventQueryError("invalid_time_range")
    where = ["uid = ?", "char_id = ?", "realm = ?"]
    params: list[Any] = [scope.uid, scope.character_id, scope.domain]
    if text:
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(raw_text LIKE ? ESCAPE '\\' OR visible_text LIKE ? ESCAPE '\\' OR memory_text LIKE ? ESCAPE '\\')")
        params.extend([f"%{escaped}%"] * 3)
    for column, value in (("actor", actor), ("kind", kind)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    if source:
        where.append("source = ?")
        params.append(source)
    else:
        source_where, source_params = source_policy.sql_predicate(include_isolated=False)
        if source_where:
            where.append(source_where.removeprefix(" AND "))
            params.extend(source_params)
    if occurred_after is not None:
        where.append("occurred_at >= ?")
        params.append(occurred_after)
    if occurred_before is not None:
        where.append("occurred_at <= ?")
        params.append(occurred_before)
    if decoded:
        try:
            occurred_at = float(decoded["occurred_at"])
            seq = int(decoded["seq"])
            event_id = str(decoded["event_id"])
        except (KeyError, TypeError, ValueError):
            raise EventQueryError("invalid_cursor") from None
        where.append("(occurred_at > ? OR (occurred_at = ? AND (seq > ? OR (seq = ? AND event_id > ?))))")
        params.extend([occurred_at, occurred_at, seq, seq, event_id])
    opened = _connect(scope, busy_timeout_ms=busy_timeout_ms)
    if opened is None:
        return {"items": [], "next_cursor": "", "truncation_reason": ""}
    path, connection = opened
    with event_store._lock_for(path):
        try:
            if not source:
                _observe_default_source_filter()
            direction = "DESC" if order == "desc" else "ASC"
            rows = connection.execute(
                f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY occurred_at {direction}, seq {direction}, event_id {direction} LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            return {
                "items": [_event_projection_with_topics(connection, scope, row) for row in rows],
                "next_cursor": _encode_cursor({"v": 1, "kind": "search", "occurred_at": rows[-1]["occurred_at"], "seq": rows[-1]["seq"], "event_id": rows[-1]["event_id"]}) if has_more and rows else "",
                "truncation_reason": "limit" if has_more else "",
            }
        except sqlite3.Error as exc:
            raise EventQueryError("database_error") from exc
        finally:
            connection.close()


def record_query_trace(
    scope: MemoryScope,
    *,
    query_type: str,
    result_count: int,
    truncation_reason: str = "",
    outcome: str = "ok",
) -> None:
    """Persist metadata only. Query text, event IDs, and evidence never enter trace."""
    try:
        from core.memory.path_resolver import resolve_path

        path = resolve_path(scope, "event_query_trace")
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_append_jsonl(path, {
            "ts": time.time(),
            "query_type": query_type,
            "result_count": max(0, int(result_count)),
            "truncation_reason": str(truncation_reason or "")[:64],
            "outcome": str(outcome or "ok")[:64],
            "scope": {"uid": scope.uid, "char_id": scope.character_id, "realm": scope.domain},
        })
    except Exception:
        # Observability must never make a read-only lookup unavailable.
        return


def query_traces(scope: MemoryScope, *, limit: int) -> list[dict[str, Any]]:
    from core.memory.path_resolver import resolve_path

    try:
        path = resolve_path(scope, "event_query_trace")
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            rows.append({
                "ts": item.get("ts"),
                "query_type": item.get("query_type", ""),
                "result_count": item.get("result_count", 0),
                "truncation_reason": item.get("truncation_reason", ""),
                "outcome": item.get("outcome", ""),
                "scope": item.get("scope", {}),
            })
        return list(reversed(rows[-min(limit, TRACE_LIMIT):]))
    except Exception as exc:
        raise EventQueryError("trace_unavailable") from exc
