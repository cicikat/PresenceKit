"""Character-owned structured library (work order T2).

One SQLite file per Reality owner+char bucket, stored beside (not inside) the
self text-file root.  There is no raw SQL surface: tools pass structured
arguments, every identifier is validated against a strict pattern and quoted,
and every value is bound as a parameter.  Locking, the grant check and audit
reuse the self-file runner (``character_self._run``), so ``self_revoked``
freezes this library too.  Audit rows record the operation, table name and
affected row count, never row contents.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any

from core.character_self import (
    SelfError,
    _denied,
    _envelope,
    _principal,
    _quota_limits,
    _run,
    dumps,
)
from core.sandbox import get_paths
from core.sensitive_redaction import RedactionError, redact_for_export

IDENT_RE = re.compile(r"^[A-Za-z_一-鿿][\w一-鿿]{0,31}$")
COLUMN_TYPES = {"text": "TEXT", "number": "REAL", "bool": "INTEGER", "date": "TEXT"}
MAX_TABLES = 20
MAX_ROWS_PER_TABLE = 5000
MAX_COLUMNS = 20
MAX_INSERT_ROWS = 50
MAX_QUERY_LIMIT = 50
MAX_IN_VALUES = 50
MAX_TEXT_CHARS = 4000
MAX_DESCRIPTION_CHARS = 200
TRASH_PREFIX = "_trash_"
META_TABLE = "_self_db_tables"
ID_COLUMN = "_id"
_OPS = ("eq", "ne", "gt", "lt", "contains", "in")
_SQL_OPS = {"eq": "=", "ne": "!=", "gt": ">", "lt": "<"}


def _db_path(principal) -> Path:
    path = get_paths().character_self_db(principal.uid, char_id=principal.char_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _q(identifier: str) -> str:
    # IDENT_RE (and the reserved names) contain no quote characters.
    return '"' + identifier + '"'


def _ident(value: Any, *, what: str, allow_id: bool = False) -> str:
    if not isinstance(value, str) or not IDENT_RE.fullmatch(value):
        raise SelfError("invalid_identifier", extra={"field": what})
    if allow_id is False and value == ID_COLUMN:
        raise SelfError("invalid_identifier", extra={"field": what})
    return value


def _table_name(value: Any) -> str:
    name = _ident(value, what="table")
    if name.startswith("_"):
        raise SelfError("invalid_identifier", extra={"field": "table", "reason": "reserved_prefix"})
    return name


def _connect(principal) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path(principal)), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_q(META_TABLE)} ("
        "name TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', "
        "columns TEXT NOT NULL, created_at REAL NOT NULL, trashed_at REAL)"
    )
    return conn


def _live_tables(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in conn.execute(f"SELECT name, description, columns, created_at FROM {_q(META_TABLE)} WHERE trashed_at IS NULL ORDER BY created_at"):
        result[row["name"]] = {
            "description": row["description"],
            "columns": json.loads(row["columns"]),
            "created_at": row["created_at"],
        }
    return result


def _require_table(conn: sqlite3.Connection, table: str) -> list[dict[str, str]]:
    live = _live_tables(conn)
    if table not in live:
        raise SelfError("table_not_found")
    return live[table]["columns"]


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0])


def _check_file_quota(conn: sqlite3.Connection, principal) -> None:
    pages = int(conn.execute("PRAGMA page_count").fetchone()[0])
    size = pages * int(conn.execute("PRAGMA page_size").fetchone()[0])
    limit = _quota_limits(principal)["max_total_bytes"]
    if size > limit:
        raise SelfError("quota_exhausted", extra={"quota": "db_bytes", "max_bytes": limit})


def _coerce(value: Any, ctype: str, column: str) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        raise SelfError("invalid_value", extra={"column": column})
    if ctype == "text":
        text = value if isinstance(value, str) else (str(value).lower() if isinstance(value, bool) else str(value))
        if len(text) > MAX_TEXT_CHARS:
            raise SelfError("value_too_long", extra={"column": column, "max_chars": MAX_TEXT_CHARS})
        return text
    if ctype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise SelfError("invalid_value", extra={"column": column})
        try:
            return float(value) if isinstance(value, str) or isinstance(value, float) else int(value)
        except ValueError as exc:
            raise SelfError("invalid_value", extra={"column": column}) from exc
    if ctype == "bool":
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, int) and value in (0, 1):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return 1 if value.lower() == "true" else 0
        raise SelfError("invalid_value", extra={"column": column})
    if ctype == "date":
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError as exc:
            raise SelfError("invalid_value", extra={"column": column, "expected": "YYYY-MM-DD"}) from exc
    raise SelfError("invalid_value", extra={"column": column})


def _column_types(columns: list[dict[str, str]]) -> dict[str, str]:
    types = {c["name"]: c["type"] for c in columns}
    types[ID_COLUMN] = "number"
    return types


def _build_where(where: Any, types: dict[str, str]) -> tuple[str, list[Any]]:
    if not isinstance(where, dict) or not where:
        raise SelfError("where_required")
    clauses: list[str] = []
    params: list[Any] = []
    for column, spec in where.items():
        _ident(column, what="where", allow_id=True)
        if column not in types:
            raise SelfError("unknown_column", extra={"column": column})
        conditions = spec if isinstance(spec, dict) else {"eq": spec}
        if not conditions:
            raise SelfError("invalid_where", extra={"column": column})
        for op, operand in conditions.items():
            if op not in _OPS:
                raise SelfError("invalid_where", extra={"column": column, "op": str(op)[:16]})
            ctype = types[column]
            if op == "in":
                if not isinstance(operand, list) or not operand or len(operand) > MAX_IN_VALUES:
                    raise SelfError("invalid_where", extra={"column": column, "op": "in"})
                values = [_coerce(item, ctype, column) for item in operand]
                clauses.append(f"{_q(column)} IN ({','.join('?' for _ in values)})")
                params.extend(values)
            elif op == "contains":
                if not isinstance(operand, str) or not operand:
                    raise SelfError("invalid_where", extra={"column": column, "op": "contains"})
                escaped = operand.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                clauses.append(f"CAST({_q(column)} AS TEXT) LIKE ? ESCAPE '\\'")
                params.append(f"%{escaped}%")
            elif operand is None:
                if op not in {"eq", "ne"}:
                    raise SelfError("invalid_where", extra={"column": column, "op": op})
                clauses.append(f"{_q(column)} IS {'NOT ' if op == 'ne' else ''}NULL")
            else:
                clauses.append(f"{_q(column)} {_SQL_OPS[op]} ?")
                params.append(_coerce(operand, ctype, column))
    return " AND ".join(clauses), params


def _prune_trash(conn: sqlite3.Connection, principal) -> None:
    limits = _quota_limits(principal)
    ttl = limits["trash_ttl_days"] * 86400
    now = time.time()
    rows = conn.execute(
        f"SELECT name, trashed_at FROM {_q(META_TABLE)} WHERE trashed_at IS NOT NULL ORDER BY trashed_at"
    ).fetchall()
    doomed = [r["name"] for r in rows if now - float(r["trashed_at"]) > ttl]
    keep = [r["name"] for r in rows if r["name"] not in doomed]
    doomed.extend(keep[:-limits["max_trash"]] if len(keep) > limits["max_trash"] else [])
    for name in doomed:
        conn.execute(f"DROP TABLE IF EXISTS {_q(name)}")
        conn.execute(f"DELETE FROM {_q(META_TABLE)} WHERE name = ?", (name,))


def _redact(value: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return redact_for_export(value)
        except RedactionError as exc:
            raise SelfError("sensitive_redaction_failed") from exc
    return value


def _out_value(value: Any, ctype: str) -> Any:
    if value is None:
        return None
    if ctype == "bool":
        return bool(value)
    if ctype == "number" and isinstance(value, float) and value.is_integer():
        return int(value)
    return _redact(value)


def _execute(user_id, char_id, operation: str, table: str, origin: str, body, *, write: bool) -> str:
    def _op(principal, _root, _meta, grant, causation):
        conn = _connect(principal)
        try:
            with conn:
                if write:
                    _prune_trash(conn, principal)
                extra = body(conn, principal)
                if write:
                    _check_file_quota(conn, principal)
        except sqlite3.Error as exc:
            raise SelfError("db_error", extra={"reason": type(exc).__name__}) from exc
        finally:
            conn.close()
        extra = dict(extra)
        extra["grant_revision"] = grant.get("revision")
        return _envelope(
            principal, operation=operation, path=table, origin=origin, causation=causation,
            result=extra.pop("result", operation), ok=True, extra=extra,
        )

    return dumps(_run(user_id, char_id, operation, table, origin, _op))


def _guard(user_id, char_id, operation: str, table: str, origin: str, fn) -> str | None:
    """Validate arguments before opening anything; return a denial JSON on error."""
    try:
        fn()
    except SelfError as exc:
        try:
            principal = _principal(user_id, char_id)
        except SelfError:
            principal = None
        return dumps(_denied(principal, operation, table, origin, exc))
    return None


def _need(condition: bool, code: str, **extra: Any) -> None:
    if not condition:
        raise SelfError(code, extra=extra)


def db_tables(*, user_id=None, char_id=None, origin: str = "tool") -> str:
    def body(conn, principal):
        live = _live_tables(conn)
        trash = conn.execute(f"SELECT COUNT(*) FROM {_q(META_TABLE)} WHERE trashed_at IS NOT NULL").fetchone()[0]
        tables = [{
            "table": name, "description": meta["description"],
            "columns": meta["columns"], "rows": _row_count(conn, name),
        } for name, meta in live.items()]
        return {"result": "listed", "tables": tables, "trash_count": int(trash),
                "limits": {"max_tables": MAX_TABLES, "max_rows_per_table": MAX_ROWS_PER_TABLE}}
    return _execute(user_id, char_id, "db_tables", "", origin, body, write=False)


def db_create_table(table, columns, description="", *, user_id=None, char_id=None, origin: str = "tool") -> str:
    cleaned: list[dict[str, str]] = []

    def validate():
        _table_name(table)
        if not isinstance(columns, list) or not columns or len(columns) > MAX_COLUMNS:
            raise SelfError("invalid_columns", extra={"max_columns": MAX_COLUMNS})
        seen = set()
        for col in columns:
            if not isinstance(col, dict):
                raise SelfError("invalid_columns")
            name = _ident(col.get("name"), what="column")
            ctype = col.get("type", "text")
            if ctype not in COLUMN_TYPES or name in seen:
                raise SelfError("invalid_columns", extra={"column": name})
            seen.add(name)
            cleaned.append({"name": name, "type": ctype})
        if description is not None and not isinstance(description, str):
            raise SelfError("invalid_value", extra={"field": "description"})

    denied = _guard(user_id, char_id, "db_create_table", str(table or ""), origin, validate)
    if denied:
        return denied

    def body(conn, principal):
        live = _live_tables(conn)
        if table in live:
            raise SelfError("table_exists")
        if len(live) >= MAX_TABLES:
            raise SelfError("quota_exhausted", extra={"quota": "tables", "max_tables": MAX_TABLES})
        defs = ", ".join(f"{_q(c['name'])} {COLUMN_TYPES[c['type']]}" for c in cleaned)
        conn.execute(f"DROP TABLE IF EXISTS {_q(table)}")
        conn.execute(f"CREATE TABLE {_q(table)} ({_q(ID_COLUMN)} INTEGER PRIMARY KEY AUTOINCREMENT, {defs})")
        conn.execute(
            f"INSERT OR REPLACE INTO {_q(META_TABLE)} (name, description, columns, created_at, trashed_at) VALUES (?,?,?,?,NULL)",
            (table, (description or "")[:MAX_DESCRIPTION_CHARS], json.dumps(cleaned, ensure_ascii=False), time.time()),
        )
        return {"result": "created", "table": table, "columns": cleaned, "rows_affected": 0}
    return _execute(user_id, char_id, "db_create_table", table, origin, body, write=True)


def db_insert(table, rows, *, user_id=None, char_id=None, origin: str = "tool") -> str:
    def validate():
        _table_name(table)
        _need(
            isinstance(rows, list) and bool(rows) and len(rows) <= MAX_INSERT_ROWS
            and all(isinstance(r, dict) for r in rows),
            "invalid_rows", max_rows=MAX_INSERT_ROWS,
        )

    denied = _guard(user_id, char_id, "db_insert", str(table or ""), origin, validate)
    if denied:
        return denied

    def body(conn, principal):
        columns = _require_table(conn, table)
        types = {c["name"]: c["type"] for c in columns}
        if _row_count(conn, table) + len(rows) > MAX_ROWS_PER_TABLE:
            raise SelfError("quota_exhausted", extra={"quota": "rows", "max_rows": MAX_ROWS_PER_TABLE})
        prepared = []
        for row in rows:
            unknown = [k for k in row if k not in types]
            if unknown or not row:
                raise SelfError("unknown_column", extra={"column": str(unknown[0])[:32] if unknown else ""})
            prepared.append({k: _coerce(v, types[k], k) for k, v in row.items()})
        ids = []
        for row in prepared:
            names = list(row)
            if names:
                sql = f"INSERT INTO {_q(table)} ({','.join(_q(n) for n in names)}) VALUES ({','.join('?' for _ in names)})"
                cur = conn.execute(sql, [row[n] for n in names])
            else:
                cur = conn.execute(f"INSERT INTO {_q(table)} DEFAULT VALUES")
            ids.append(cur.lastrowid)
        return {"result": "inserted", "table": table, "rows_affected": len(prepared), "ids": ids}
    return _execute(user_id, char_id, "db_insert", table, origin, body, write=True)


def _parse_order(order_by, types) -> str:
    if order_by in (None, ""):
        return f" ORDER BY {_q(ID_COLUMN)}"
    if isinstance(order_by, dict):
        column, desc = order_by.get("column"), bool(order_by.get("desc"))
    elif isinstance(order_by, str):
        text = order_by.strip()
        desc = text.startswith("-") or text.lower().endswith(" desc")
        column = text.lstrip("-")
        for suffix in (" desc", " asc", " DESC", " ASC"):
            if column.endswith(suffix):
                column = column[: -len(suffix)]
        column = column.strip()
    else:
        raise SelfError("invalid_order_by")
    _ident(column, what="order_by", allow_id=True)
    if column not in types:
        raise SelfError("unknown_column", extra={"column": column})
    return f" ORDER BY {_q(column)} {'DESC' if desc else 'ASC'}, {_q(ID_COLUMN)}"


def db_query(table, where=None, order_by=None, limit=20, offset=0, *, user_id=None, char_id=None, origin: str = "tool") -> str:
    state: dict[str, int] = {}

    def validate():
        _table_name(table)
        try:
            state["limit"] = max(1, min(MAX_QUERY_LIMIT, int(limit)))
            state["offset"] = max(0, int(offset or 0))
        except (TypeError, ValueError) as exc:
            raise SelfError("invalid_value", extra={"field": "limit"}) from exc
        if where is not None and not isinstance(where, dict):
            raise SelfError("invalid_where")

    denied = _guard(user_id, char_id, "db_query", str(table or ""), origin, validate)
    if denied:
        return denied

    def body(conn, principal):
        columns = _require_table(conn, table)
        types = _column_types(columns)
        sql = f"SELECT * FROM {_q(table)}"
        params: list[Any] = []
        if where:
            clause, params = _build_where(where, types)
            sql += " WHERE " + clause
        total = conn.execute(sql.replace("SELECT *", "SELECT COUNT(*)", 1), params).fetchone()[0]
        sql += _parse_order(order_by, types) + " LIMIT ? OFFSET ?"
        rows = conn.execute(sql, params + [state["limit"], state["offset"]]).fetchall()
        out = [{k: _out_value(row[k], types.get(k, "number")) for k in row.keys()} for row in rows]
        return {"result": "queried", "table": table, "rows": out, "row_count": len(out),
                "total_matching": int(total), "truncated": int(total) > state["offset"] + len(out)}
    return _execute(user_id, char_id, "db_query", table, origin, body, write=False)


def db_update(table, where, set, *, user_id=None, char_id=None, origin: str = "tool") -> str:  # noqa: A002 - tool arg name
    def validate():
        _table_name(table)
        _need(isinstance(where, dict) and bool(where), "where_required")
        _need(isinstance(set, dict) and bool(set), "invalid_value", field="set")

    denied = _guard(user_id, char_id, "db_update", str(table or ""), origin, validate)
    if denied:
        return denied

    def body(conn, principal):
        columns = _require_table(conn, table)
        types = _column_types(columns)
        assignments, values = [], []
        for column, value in set.items():
            if column not in types or column == ID_COLUMN:
                raise SelfError("unknown_column", extra={"column": str(column)[:32]})
            assignments.append(f"{_q(column)} = ?")
            values.append(_coerce(value, types[column], column))
        clause, params = _build_where(where, types)
        cur = conn.execute(f"UPDATE {_q(table)} SET {', '.join(assignments)} WHERE {clause}", values + params)
        return {"result": "updated", "table": table, "rows_affected": cur.rowcount}
    return _execute(user_id, char_id, "db_update", table, origin, body, write=True)


def db_delete(table, where, *, user_id=None, char_id=None, origin: str = "tool") -> str:
    def validate():
        _table_name(table)
        _need(isinstance(where, dict) and bool(where), "where_required")

    denied = _guard(user_id, char_id, "db_delete", str(table or ""), origin, validate)
    if denied:
        return denied

    def body(conn, principal):
        types = _column_types(_require_table(conn, table))
        clause, params = _build_where(where, types)
        cur = conn.execute(f"DELETE FROM {_q(table)} WHERE {clause}", params)
        return {"result": "deleted", "table": table, "rows_affected": cur.rowcount}
    return _execute(user_id, char_id, "db_delete", table, origin, body, write=True)


def db_drop_table(table, *, user_id=None, char_id=None, origin: str = "tool") -> str:
    denied = _guard(user_id, char_id, "db_drop_table", str(table or ""), origin, lambda: _table_name(table))
    if denied:
        return denied

    def body(conn, principal):
        _require_table(conn, table)
        stamp = int(time.time())
        trashed = f"{TRASH_PREFIX}{stamp}_{table}"
        suffix = 0
        while conn.execute(f"SELECT 1 FROM {_q(META_TABLE)} WHERE name = ?", (trashed,)).fetchone():
            suffix += 1
            trashed = f"{TRASH_PREFIX}{stamp}_{suffix}_{table}"
        rows = _row_count(conn, table)
        conn.execute(f"ALTER TABLE {_q(table)} RENAME TO {_q(trashed)}")
        conn.execute(f"UPDATE {_q(META_TABLE)} SET name = ?, trashed_at = ? WHERE name = ?", (trashed, time.time(), table))
        return {"result": "dropped", "table": table, "rows_affected": rows, "recoverable": True}
    return _execute(user_id, char_id, "db_drop_table", table, origin, body, write=True)


def observability(uid: str, char_id: str) -> dict[str, Any]:
    """Metadata only: table names, column counts, row counts, file size."""
    principal = _principal(uid, char_id)
    path = get_paths().character_self_db(principal.uid, char_id=principal.char_id)
    if not path.exists():
        return {"present": False, "table_count": 0, "tables": [], "file_bytes": 0, "trash_count": 0}
    conn = _connect(principal)
    try:
        live = _live_tables(conn)
        tables = [{"table": n, "columns": len(m["columns"]), "rows": _row_count(conn, n)} for n, m in live.items()]
        trash = conn.execute(f"SELECT COUNT(*) FROM {_q(META_TABLE)} WHERE trashed_at IS NOT NULL").fetchone()[0]
    finally:
        conn.close()
    return {
        "present": True, "table_count": len(tables), "tables": tables,
        "file_bytes": path.stat().st_size, "trash_count": int(trash),
        "limits": {"max_tables": MAX_TABLES, "max_rows_per_table": MAX_ROWS_PER_TABLE,
                   "max_total_bytes": _quota_limits(principal)["max_total_bytes"]},
    }
