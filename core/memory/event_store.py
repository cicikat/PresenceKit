"""Scoped SQLite evidence ledger for Memory Event.

This module is deliberately independent from ``event_log`` and the prompt
pipeline.  It stores immutable evidence rows only; callers cannot issue SQL
through the public API.
"""

from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id

logger = logging.getLogger(__name__)

SCHEMA_NAME = "memory_event_ledger"
SCHEMA_VERSION = 4
EDGE_RELATION_TYPES = frozenset({
    "previous", "next", "same_turn", "reply_to", "triggered_by",
    "derived_from", "correction_of", "media_of",
})
PROPOSAL_RELATION_TYPES = frozenset({
    "same_topic", "follows_up", "possible_cause", "contradicts", "supports",
})

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_PATH_GUARD = threading.RLock()
_VERIFIED_SCHEMA_PATHS: set[str] = set()
_OBSERVABILITY_LOCK = threading.Lock()
_OBSERVABILITY: dict[str, Any] = {
    "attempted": 0,
    "written": 0,
    "duplicates": 0,
    "failed": 0,
    "by_character": {},
    "by_realm": {},
}
_EDGE_OBSERVABILITY: dict[str, Any] = {
    "attempted": 0, "written": 0, "duplicates": 0, "failed": 0,
}
_PROPOSER_SOURCE_OBSERVABILITY: dict[str, int] = {
    "input_count": 0, "filtered_count": 0,
}
_HOT_PATH_OBSERVABILITY: dict[str, int] = {
    "append_count": 0,
    "append_ms_total": 0,
    "edge_ms_total": 0,
    "stream_queries": 0,
    "turn_queries": 0,
    "edges_removed": 0,
    "busy": 0,
    "locked": 0,
    "schema_mismatch": 0,
}
_STARTUP_INITIALIZATION: dict[str, Any] = {
    "status": "not_run",
    "discovered": 0,
    "healthy": 0,
    "upgraded": 0,
    "failed": 0,
    "error_codes": {},
}

# Ledger writes run before visible fanout.  The in-process per-ledger lock
# handles ordinary contention; this short SQLite budget keeps an external
# lock holder from delaying a chat turn for the former five seconds.
REALTIME_BUSY_TIMEOUT_MS = 250


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    turn_id: str = ""
    seq: int = 0
    occurred_at: float = 0.0
    ingested_at: float = 0.0
    uid: str = ""
    char_id: str = ""
    realm: str = "reality"
    kind: str = ""
    actor: str = ""
    channel: str = ""
    stream: str = ""
    source: str = ""
    # Nullable provenance links for Brief 217. Evidence IDs stay in event_id;
    # these identify the ingress that caused the actual turn.
    ingress_event_id: str = ""
    causation_id: str = ""
    raw_payload_json: str = ""
    raw_text: str = ""
    visible_text: str = ""
    memory_text: str = ""
    media_refs_json: str = ""
    redaction_state: str = "unredacted"
    relation_hints_json: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EventRecord":
        if not isinstance(value, Mapping):
            raise TypeError("event must be a mapping or EventRecord")
        data = dict(value)
        for field in ("raw_payload_json", "media_refs_json"):
            if field in data and not isinstance(data[field], str):
                data[field] = json.dumps(data[field], ensure_ascii=False, separators=(",", ":"))
        return cls(**{field: data[field] for field in cls.__dataclass_fields__ if field in data})

    def normalized(self, scope: MemoryScope) -> "EventRecord":
        require_character_id(scope.character_id)
        event_id = str(self.event_id).strip()
        if not event_id:
            raise ValueError("event_id must be non-empty")
        if self.uid and self.uid != scope.uid:
            raise ValueError("event uid does not match scope uid")
        if self.char_id and self.char_id != scope.character_id:
            raise ValueError("event char_id does not match scope character_id")
        now = time.time()
        occurred_at = float(self.occurred_at if self.occurred_at is not None else now)
        if occurred_at == 0.0 and self.kind != "legacy_unknown":
            occurred_at = now
        ingested_at = float(self.ingested_at or now)
        realm = str(self.realm or "reality")
        if realm != scope.domain:
            raise ValueError("event realm does not match scope domain")
        return EventRecord(
            event_id=event_id,
            turn_id=str(self.turn_id or ""),
            seq=int(self.seq),
            occurred_at=occurred_at,
            ingested_at=ingested_at,
            uid=scope.uid,
            char_id=scope.character_id or "",
            realm=realm,
            kind=str(self.kind or ""),
            actor=str(self.actor or ""),
            channel=str(self.channel or ""),
            stream=str(self.stream or self.channel or ""),
            source=str(self.source or ""),
            ingress_event_id=str(self.ingress_event_id or ""),
            causation_id=str(self.causation_id or ""),
            raw_payload_json=str(self.raw_payload_json or ""),
            raw_text=str(self.raw_text or ""),
            visible_text=str(self.visible_text or ""),
            memory_text=str(self.memory_text or ""),
            media_refs_json=str(self.media_refs_json or ""),
            redaction_state=str(self.redaction_state or "unredacted"),
            relation_hints_json=str(self.relation_hints_json or ""),
        )


@dataclass(frozen=True)
class AppendResult:
    ok: bool
    inserted: bool
    event_id: str
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TombstoneResult:
    """Outcome of a reversible evidence forget request.

    A tombstone intentionally keeps the event row and all relation edges.  It
    removes recallable payload fields while preserving stable identifiers and
    provenance metadata needed to explain derived-memory references.
    """

    ok: bool
    changed: bool
    event_id: str
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SchemaStatus:
    schema_name: str
    expected_version: int
    schema_version: int | None
    exists: bool
    healthy: bool
    tables: tuple[str, ...] = ()
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_name": self.schema_name,
            "expected_version": self.expected_version,
            "schema_version": self.schema_version,
            "exists": self.exists,
            "healthy": self.healthy,
            "tables": list(self.tables),
            "error_code": self.error_code,
        }


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL DEFAULT 0,
    occurred_at REAL NOT NULL,
    ingested_at REAL NOT NULL,
    uid TEXT NOT NULL,
    char_id TEXT NOT NULL,
    realm TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    stream TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    ingress_event_id TEXT NOT NULL DEFAULT '',
    causation_id TEXT NOT NULL DEFAULT '',
    raw_payload_json TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    visible_text TEXT NOT NULL DEFAULT '',
    memory_text TEXT NOT NULL DEFAULT '',
    media_refs_json TEXT NOT NULL DEFAULT '',
    redaction_state TEXT NOT NULL DEFAULT 'unredacted',
    relation_hints_json TEXT NOT NULL DEFAULT '',
    UNIQUE(uid, char_id, event_id)
);
CREATE TABLE IF NOT EXISTS event_edges (
    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,
    char_id TEXT NOT NULL,
    from_event_id TEXT NOT NULL,
    to_event_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    relation_type TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'system',
    confidence REAL NOT NULL DEFAULT 1.0,
    schema_version INTEGER NOT NULL DEFAULT 2,
    created_at REAL NOT NULL,
    UNIQUE(uid, char_id, from_event_id, to_event_id, edge_type)
);
CREATE TABLE IF NOT EXISTS event_topics (
    uid TEXT NOT NULL,
    char_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    score REAL,
    created_at REAL NOT NULL,
    PRIMARY KEY(uid, char_id, event_id, topic)
);
CREATE TABLE IF NOT EXISTS event_edge_proposals (
    proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,
    char_id TEXT NOT NULL,
    realm TEXT NOT NULL,
    from_event_id TEXT NOT NULL,
    to_event_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL,
    model TEXT NOT NULL,
    preset TEXT NOT NULL,
    model_version TEXT NOT NULL DEFAULT '',
    prompt_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    UNIQUE(uid, char_id, realm, from_event_id, to_event_id, relation_type)
);
CREATE TABLE IF NOT EXISTS event_edge_proposer_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,
    char_id TEXT NOT NULL,
    realm TEXT NOT NULL,
    created_at REAL NOT NULL,
    day_key TEXT NOT NULL,
    input_count INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    token_budget INTEGER NOT NULL,
    model TEXT NOT NULL,
    preset TEXT NOT NULL,
    model_version TEXT NOT NULL DEFAULT '',
    prompt_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_turn_id ON events(turn_id);
CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_realm ON events(realm);
CREATE INDEX IF NOT EXISTS idx_events_stream_adjacency
    ON events(uid, char_id, realm, stream, source, occurred_at, seq, event_id);
CREATE INDEX IF NOT EXISTS idx_events_turn_partition
    ON events(uid, char_id, realm, turn_id, source, actor);
CREATE INDEX IF NOT EXISTS idx_event_edge_proposals_scope ON event_edge_proposals(uid, char_id, realm, created_at);
CREATE INDEX IF NOT EXISTS idx_event_edge_proposer_runs_scope ON event_edge_proposer_runs(uid, char_id, realm, created_at);
CREATE INDEX IF NOT EXISTS idx_event_edge_proposer_runs_day ON event_edge_proposer_runs(uid, char_id, realm, day_key);
"""


def _path(scope: MemoryScope) -> Path:
    if scope.domain != "reality":
        raise ValueError("event store requires a reality scope")
    return resolve_path(scope, "event_store")


def _prepare_write_path(scope: MemoryScope) -> Path:
    """Materialize the sandbox root before concurrent resolver calls."""
    from core.sandbox import get_paths

    with _PATH_GUARD:
        get_paths().root_dir().mkdir(parents=True, exist_ok=True)
        path = _path(scope)
        # DataPaths validates with Path.resolve(). On Windows, resolving a
        # nested non-existent path while another writer creates it can produce
        # a transient false sandbox-escape result, so prepare it under the
        # same guard as resolution.
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def _lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path, timeout=REALTIME_BUSY_TIMEOUT_MS / 1000, check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={REALTIME_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def _observe_append(result: AppendResult, scope: object) -> AppendResult:
    """Update redacted, process-local write health counters."""
    char_id = str(getattr(scope, "character_id", "") or "unknown")
    realm = str(getattr(scope, "domain", "") or "unknown")

    def update(bucket: dict[str, Any]) -> None:
        bucket["attempted"] = int(bucket.get("attempted", 0)) + 1
        if result.inserted:
            bucket["written"] = int(bucket.get("written", 0)) + 1
        elif result.ok:
            bucket["duplicates"] = int(bucket.get("duplicates", 0)) + 1
        else:
            bucket["failed"] = int(bucket.get("failed", 0)) + 1

    with _OBSERVABILITY_LOCK:
        update(_OBSERVABILITY)
        for group, key in (("by_character", char_id), ("by_realm", realm)):
            buckets = _OBSERVABILITY[group]
            update(buckets.setdefault(key, {}))
    return result


def observability_snapshot() -> dict[str, Any]:
    """Return a read-only, content-free projection of ledger write health."""
    from core.memory.source_policy import observability_snapshot as source_policy_snapshot

    with _OBSERVABILITY_LOCK:
        attempted = int(_OBSERVABILITY["attempted"])
        successful = int(_OBSERVABILITY["written"]) + int(_OBSERVABILITY["duplicates"])
        return {
            "scope": "process",
            "attempted": attempted,
            "written": int(_OBSERVABILITY["written"]),
            "duplicates": int(_OBSERVABILITY["duplicates"]),
            "failed": int(_OBSERVABILITY["failed"]),
            "success_rate": successful / attempted if attempted else None,
            "by_character": {key: dict(value) for key, value in _OBSERVABILITY["by_character"].items()},
            "by_realm": {key: dict(value) for key, value in _OBSERVABILITY["by_realm"].items()},
            "hot_path": {
                **dict(_HOT_PATH_OBSERVABILITY),
                "busy_timeout_ms": REALTIME_BUSY_TIMEOUT_MS,
                "append_ms_average": round(
                    _HOT_PATH_OBSERVABILITY["append_ms_total"] / attempted, 3
                ) if attempted else None,
                "edge_ms_average": round(
                    _HOT_PATH_OBSERVABILITY["edge_ms_total"] / attempted, 3
                ) if attempted else None,
            },
            "source_policy": source_policy_snapshot(),
            "startup_initialization": dict(_STARTUP_INITIALIZATION),
        }


def _reset_observability_for_tests() -> None:
    with _OBSERVABILITY_LOCK:
        for key in ("attempted", "written", "duplicates", "failed"):
            _OBSERVABILITY[key] = 0
        _OBSERVABILITY["by_character"] = {}
        _OBSERVABILITY["by_realm"] = {}
    with _OBSERVABILITY_LOCK:
        for key in _EDGE_OBSERVABILITY:
            _EDGE_OBSERVABILITY[key] = 0
        for key in _PROPOSER_SOURCE_OBSERVABILITY:
            _PROPOSER_SOURCE_OBSERVABILITY[key] = 0
        for key in _HOT_PATH_OBSERVABILITY:
            _HOT_PATH_OBSERVABILITY[key] = 0
        _STARTUP_INITIALIZATION.update({
            "status": "not_run", "discovered": 0, "healthy": 0,
            "upgraded": 0, "failed": 0, "error_codes": {},
        })


def _initialize(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise RuntimeError("unsupported_schema_version")
    existing_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    # Add columns before the complete schema script creates indexes that refer
    # to them. This is required for in-place v3 -> v4 upgrades.
    event_columns = (
        {row[1] for row in connection.execute("PRAGMA table_info(events)")}
        if "events" in existing_tables else set()
    )
    if "stream" not in event_columns:
        if event_columns:
            connection.execute("ALTER TABLE events ADD COLUMN stream TEXT NOT NULL DEFAULT ''")
    if "relation_hints_json" not in event_columns:
        if event_columns:
            connection.execute("ALTER TABLE events ADD COLUMN relation_hints_json TEXT NOT NULL DEFAULT ''")
    for column in ("ingress_event_id", "causation_id"):
        if event_columns and column not in event_columns:
            connection.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    edge_columns = (
        {row[1] for row in connection.execute("PRAGMA table_info(event_edges)")}
        if "event_edges" in existing_tables else set()
    )
    for column, definition in (
        ("relation_type", "TEXT NOT NULL DEFAULT ''"),
        ("origin", "TEXT NOT NULL DEFAULT 'system'"),
        ("confidence", "REAL NOT NULL DEFAULT 1.0"),
        ("schema_version", "INTEGER NOT NULL DEFAULT 2"),
    ):
        if edge_columns and column not in edge_columns:
            connection.execute(f"ALTER TABLE event_edges ADD COLUMN {column} {definition}")
    connection.executescript(_SCHEMA_SQL)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_events_ingress_event ON events(ingress_event_id)")
    connection.execute("UPDATE event_edges SET relation_type = edge_type WHERE relation_type = ''")
    connection.execute("UPDATE event_edges SET schema_version = ? WHERE schema_version IS NULL OR schema_version = 0", (SCHEMA_VERSION,))
    if version < SCHEMA_VERSION:
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    connection.commit()


def initialize(scope: MemoryScope) -> SchemaStatus:
    """Create or upgrade one scoped ledger; return status instead of raising."""
    try:
        path = _prepare_write_path(scope)
    except (TypeError, ValueError):
        return SchemaStatus(SCHEMA_NAME, SCHEMA_VERSION, None, False, False, error_code="invalid_scope")
    except Exception as exc:
        logger.warning("[event_store] initialize path preparation failed: %s", exc)
        return SchemaStatus(SCHEMA_NAME, SCHEMA_VERSION, None, False, False, error_code="database_error")
    with _lock_for(path):
        try:
            with _connect(path) as connection:
                _initialize(connection)
            status = schema_status(scope)
            if status.healthy:
                _VERIFIED_SCHEMA_PATHS.add(str(path))
            return status
        except Exception as exc:
            logger.warning("[event_store] initialize failed: %s", exc)
            return SchemaStatus(SCHEMA_NAME, SCHEMA_VERSION, None, path.exists(), False, error_code="database_error")


def initialize_existing_ledgers(*, max_ledgers: int = 1000) -> dict[str, Any]:
    """Upgrade existing canonical ledgers before realtime services start.

    Discovery is metadata-only and bounded. Missing scopes are never created;
    only ``runtime/memory/{char_id}/{uid}/event_store.sqlite3`` files are
    accepted. Callers decide whether failures should block startup.
    """
    from core.data_paths import safe_user_id
    from core.sandbox import get_paths

    root = get_paths().memory_char_root().parent
    ledgers: list[tuple[MemoryScope, Path]] = []
    try:
        character_dirs = sorted(root.iterdir()) if root.exists() else []
    except OSError:
        character_dirs = []
    limit = max(1, int(max_ledgers))
    for char_dir in character_dirs:
        if not char_dir.is_dir() or char_dir.is_symlink():
            continue
        try:
            char_id = safe_user_id(char_dir.name)
            user_dirs = sorted(char_dir.iterdir())
        except (OSError, ValueError):
            continue
        for user_dir in user_dirs:
            if not user_dir.is_dir() or user_dir.is_symlink():
                continue
            try:
                uid = safe_user_id(user_dir.name)
            except ValueError:
                continue
            path = user_dir / "event_store.sqlite3"
            if path.is_file() and not path.is_symlink():
                ledgers.append((MemoryScope.reality_scope(uid, char_id), path))
                if len(ledgers) > limit:
                    break
        if len(ledgers) > limit:
            break

    truncated = len(ledgers) > limit
    ledgers = ledgers[:limit]

    result: dict[str, Any] = {
        "status": "ok", "discovered": len(ledgers), "healthy": 0,
        "upgraded": 0, "failed": 0, "error_codes": {},
        "truncated": truncated,
    }
    for scope, _path_value in ledgers:
        before = schema_status(scope)
        status = initialize(scope)
        if status.healthy:
            result["healthy"] += 1
            if before.schema_version != SCHEMA_VERSION:
                result["upgraded"] += 1
            continue
        result["failed"] += 1
        code = status.error_code or "schema_mismatch"
        result["error_codes"][code] = int(result["error_codes"].get(code, 0)) + 1
    if result["failed"] or result["truncated"]:
        result["status"] = "attention"
    with _OBSERVABILITY_LOCK:
        _STARTUP_INITIALIZATION.clear()
        _STARTUP_INITIALIZATION.update(result)
    return dict(result)


def append_event(scope: MemoryScope, event: EventRecord | Mapping[str, Any]) -> AppendResult:
    """Append one immutable event, idempotently, with structured failure output."""
    if isinstance(event, EventRecord):
        event_id = event.event_id
    elif isinstance(event, Mapping):
        event_id = str(event.get("event_id", ""))
    else:
        event_id = ""
    try:
        path = _prepare_write_path(scope)
    except (AttributeError, TypeError, ValueError) as exc:
        logger.warning("[event_store] invalid scope: %s", exc)
        return _observe_append(AppendResult(False, False, event_id, "invalid_scope"), scope)
    except Exception as exc:
        logger.warning("[event_store] path preparation failed: %s", exc)
        return _observe_append(AppendResult(False, False, event_id, "database_error"), scope)
    relation_hints: dict[str, str] = {}
    try:
        if isinstance(event, Mapping):
            for relation in EDGE_RELATION_TYPES - {"previous", "next", "same_turn"}:
                value = event.get(f"{relation}_event_id")
                if value in (None, "") and isinstance(event.get(relation), str):
                    value = event.get(relation)
                if value not in (None, ""):
                    relation_hints[relation] = str(value).strip()
            # The mapping is deliberately kept out of the public projection.
            event = dict(event)
            event.setdefault("relation_hints_json", json.dumps(relation_hints, separators=(",", ":")))
        record = event if isinstance(event, EventRecord) else EventRecord.from_mapping(event)
        record = record.normalized(scope)
    except (TypeError, ValueError, KeyError):
        return _observe_append(AppendResult(False, False, event_id, "invalid_event"), scope)

    columns = tuple(EventRecord.__dataclass_fields__)
    values = tuple(getattr(record, field) for field in columns)
    placeholders = ", ".join("?" for _ in columns)
    append_started = time.perf_counter()
    with _lock_for(path):
        try:
            if not path.exists():
                status = initialize(scope)
                if not status.healthy:
                    code = status.error_code or "schema_mismatch"
                    return _observe_append(AppendResult(False, False, record.event_id, code), scope)
            elif str(path) not in _VERIFIED_SCHEMA_PATHS:
                status = schema_status(scope)
                if not status.healthy:
                    with _OBSERVABILITY_LOCK:
                        _HOT_PATH_OBSERVABILITY["schema_mismatch"] += int(status.error_code == "schema_mismatch")
                    return _observe_append(AppendResult(False, False, record.event_id, status.error_code or "schema_mismatch"), scope)
                _VERIFIED_SCHEMA_PATHS.add(str(path))
            with _connect(path) as connection:
                connection.execute(
                    f"INSERT INTO events ({', '.join(columns)}) VALUES ({placeholders})",
                    values,
                )
                _ensure_deterministic_edges(connection, scope, record)
                connection.commit()
            if record.media_refs_json and record.media_refs_json not in {"", "[]"}:
                try:
                    from core.chat_media import invalidate_live_media_cache
                    invalidate_live_media_cache()
                except Exception:
                    pass
            return _observe_append(AppendResult(True, True, record.event_id), scope)
        except sqlite3.IntegrityError:
            # Idempotent retries are still allowed to repair deterministic
            # edges when the original transaction pre-dated edge generation.
            try:
                with _connect(path) as repair:
                    existing = repair.execute(
                        "SELECT * FROM events WHERE uid = ? AND char_id = ? AND realm = ? AND event_id = ?",
                        (scope.uid, scope.character_id, scope.domain, record.event_id),
                    ).fetchone()
                    if existing is not None:
                        _ensure_deterministic_edges(repair, scope, existing)
                        repair.commit()
            except Exception:
                pass
            return _observe_append(AppendResult(True, False, record.event_id, "duplicate"), scope)
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            code = "locked" if "locked" in message else "busy" if "busy" in message else "database_error"
            if code in {"locked", "busy"}:
                with _OBSERVABILITY_LOCK:
                    _HOT_PATH_OBSERVABILITY[code] += 1
            _edge_observe("failed")
            logger.warning("[event_store] append failed: %s", code)
            return _observe_append(AppendResult(False, False, record.event_id, code), scope)
        except Exception as exc:
            _edge_observe("failed")
            logger.warning("[event_store] append failed: %s", exc)
            return _observe_append(AppendResult(False, False, record.event_id, "database_error"), scope)
        finally:
            with _OBSERVABILITY_LOCK:
                _HOT_PATH_OBSERVABILITY["append_count"] += 1
                _HOT_PATH_OBSERVABILITY["append_ms_total"] += round(
                    (time.perf_counter() - append_started) * 1000
                )


def append_topics(scope: MemoryScope, event_id: str, topics: Iterable[str]) -> bool:
    """Attach controlled rule tags to an existing event, independently of append."""
    normalized = sorted({str(topic).strip()[:128] for topic in topics if str(topic).strip()})[:20]
    if not normalized:
        return True
    try:
        path = _path(scope)
        if not path.exists():
            return False
        with _lock_for(path):
            with _connect(path) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM events WHERE uid=? AND char_id=? AND realm=? AND event_id=?",
                    (scope.uid, scope.character_id, scope.domain, event_id),
                ).fetchone()
                if exists is None:
                    return False
                connection.executemany(
                    """INSERT OR IGNORE INTO event_topics
                       (uid, char_id, event_id, topic, score, created_at) VALUES (?, ?, ?, ?, 1.0, ?)""",
                    [(scope.uid, scope.character_id, event_id, topic, time.time()) for topic in normalized],
                )
                connection.commit()
        return True
    except Exception:
        logger.warning("[event_store] topic append failed", exc_info=True)
        return False


def tombstone_event(scope: MemoryScope, event_id: str) -> TombstoneResult:
    """Forget event payload without physically deleting evidence or edges.

    The operation is scoped, local and idempotent.  It never initializes a
    missing ledger, so an admin typo cannot materialize a new database.
    """
    clean_id = str(event_id or "").strip()
    if not clean_id:
        return TombstoneResult(False, False, clean_id, "invalid_event")
    try:
        path = _path(scope)
    except (AttributeError, TypeError, ValueError):
        return TombstoneResult(False, False, clean_id, "invalid_scope")
    if not path.exists():
        return TombstoneResult(False, False, clean_id, "not_found")
    with _lock_for(path):
        try:
            with _connect(path) as connection:
                _initialize(connection)
                row = connection.execute(
                    "SELECT redaction_state FROM events WHERE uid = ? AND char_id = ? AND realm = ? AND event_id = ?",
                    (scope.uid, scope.character_id, scope.domain, clean_id),
                ).fetchone()
                if row is None:
                    return TombstoneResult(False, False, clean_id, "not_found")
                if row["redaction_state"] == "tombstoned":
                    return TombstoneResult(True, False, clean_id, "already_tombstoned")
                connection.execute(
                    """UPDATE events
                    SET raw_payload_json = '', raw_text = '', visible_text = '', memory_text = '',
                        media_refs_json = '[]', redaction_state = 'tombstoned'
                    WHERE uid = ? AND char_id = ? AND realm = ? AND event_id = ?""",
                    (scope.uid, scope.character_id, scope.domain, clean_id),
                )
                connection.commit()
            try:
                from core.chat_media import invalidate_live_media_cache
                invalidate_live_media_cache()
            except Exception:
                pass
            return TombstoneResult(True, True, clean_id)
        except Exception as exc:
            logger.warning("[event_store] tombstone failed: %s", exc)
            return TombstoneResult(False, False, clean_id, "database_error")


def migration_marker(scope: MemoryScope, event_id: str) -> str | None:
    """Read the legacy block fingerprint for duplicate/conflict accounting.

    This intentionally exposes only an internal checksum to the offline
    importer, never the raw payload JSON to an admin projection or prompt.
    """
    try:
        path = _path(scope)
    except (AttributeError, TypeError, ValueError):
        return None
    if not path.exists():
        return None
    with _lock_for(path):
        try:
            with _connect(path) as connection:
                row = connection.execute(
                    "SELECT raw_payload_json FROM events WHERE uid = ? AND char_id = ? AND realm = ? AND event_id = ?",
                    (scope.uid, scope.character_id, scope.domain, str(event_id)),
                ).fetchone()
            if row is None:
                return None
            payload = json.loads(row["raw_payload_json"] or "{}")
            marker = payload.get("legacy_block_sha256") if isinstance(payload, dict) else None
            return str(marker) if isinstance(marker, str) else None
        except (sqlite3.Error, json.JSONDecodeError, TypeError, ValueError):
            return None


def safe_text_fingerprint(event: Mapping[str, Any]) -> str:
    """Content-safe identity digest shared by live/legacy duplicate checks."""
    def value(key: str) -> Any:
        try:
            return event[key]
        except (KeyError, TypeError):
            return None
    text = str(value("memory_text") or value("visible_text") or value("raw_text") or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def migration_evidence(scope: MemoryScope, event_id: str) -> dict[str, str] | None:
    """Return only identity fields and a text digest for the offline importer."""
    try:
        path = _path(scope)
    except (AttributeError, TypeError, ValueError):
        return None
    if not path.exists():
        return None
    with _lock_for(path):
        try:
            with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    """SELECT turn_id, actor, source, raw_text, visible_text, memory_text
                       FROM events WHERE uid=? AND char_id=? AND realm=? AND event_id=?""",
                    (scope.uid, scope.character_id, scope.domain, str(event_id)),
                ).fetchone()
            if row is None:
                return None
            return {
                "turn_id": str(row["turn_id"] or ""),
                "actor": str(row["actor"] or ""),
                "source": str(row["source"] or ""),
                "text_fingerprint": safe_text_fingerprint(row),
            }
        except sqlite3.Error:
            return None


def migration_evidence_status(scope: MemoryScope, event_id: str) -> tuple[str, dict[str, str] | None]:
    """Read-only, short-budget evidence classification for migration dry-run."""
    try:
        path = _path(scope)
    except (AttributeError, TypeError, ValueError):
        return "invalid_scope", None
    if not path.is_file():
        return "missing_ledger", None
    try:
        with sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.05,
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA busy_timeout=50")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version != SCHEMA_VERSION:
                return "schema_mismatch", None
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(events)")}
            required = {"event_id", "turn_id", "actor", "source", "raw_text", "visible_text", "memory_text"}
            if not required.issubset(columns):
                return "schema_mismatch", None
            row = connection.execute(
                """SELECT turn_id, actor, source, raw_text, visible_text, memory_text
                   FROM events WHERE uid=? AND char_id=? AND realm=? AND event_id=?""",
                (scope.uid, scope.character_id, scope.domain, str(event_id)),
            ).fetchone()
        if row is None:
            return "not_found", None
        return "ok", {
            "turn_id": str(row["turn_id"] or ""),
            "actor": str(row["actor"] or ""),
            "source": str(row["source"] or ""),
            "text_fingerprint": safe_text_fingerprint(row),
        }
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message:
            return "locked", None
        if "busy" in message:
            return "busy", None
        return "database_error", None
    except (sqlite3.Error, OSError, ValueError):
        return "database_error", None


def event_ids_for_turn(scope: MemoryScope, turn_id: str) -> list[str]:
    """Map a legacy turn identity into its actual visible ledger events."""
    if not turn_id:
        return []
    try:
        path = _path(scope)
    except (AttributeError, TypeError, ValueError):
        return []
    if not path.exists():
        return []
    from core.memory.source_policy import sql_predicate
    predicate, params = sql_predicate()
    with _lock_for(path):
        try:
            with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25) as connection:
                rows = connection.execute(
                    "SELECT event_id FROM events WHERE uid=? AND char_id=? AND realm=? AND turn_id=?" + predicate,
                    (scope.uid, scope.character_id, scope.domain, str(turn_id), *params),
                ).fetchall()
            return [str(row[0]) for row in rows]
        except sqlite3.Error:
            return []


def schema_status(scope: MemoryScope) -> SchemaStatus:
    """Read-only schema/version status; missing files are not created."""
    path = _path(scope)
    if not path.exists():
        return SchemaStatus(SCHEMA_NAME, SCHEMA_VERSION, None, False, True)
    with _lock_for(path):
        try:
            with _connect(path) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                tables = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchall()
                )
            healthy = version == SCHEMA_VERSION and {
                "events", "event_edges", "event_topics", "event_edge_proposals", "event_edge_proposer_runs",
            } <= set(tables)
            return SchemaStatus(SCHEMA_NAME, SCHEMA_VERSION, version, True, healthy, tables, "" if healthy else "schema_mismatch")
        except Exception as exc:
            logger.warning("[event_store] schema status failed: %s", exc)
            return SchemaStatus(SCHEMA_NAME, SCHEMA_VERSION, None, True, False, error_code="database_error")


def _edge_observe(key: str) -> None:
    with _OBSERVABILITY_LOCK:
        _EDGE_OBSERVABILITY[key] = int(_EDGE_OBSERVABILITY.get(key, 0)) + 1


def _insert_edge(
    connection: sqlite3.Connection,
    scope: MemoryScope,
    from_event_id: str,
    to_event_id: str,
    relation_type: str,
    *,
    created_at: float,
) -> None:
    if relation_type not in EDGE_RELATION_TYPES or not from_event_id or not to_event_id or from_event_id == to_event_id:
        return
    # Endpoint checks are scoped and realm-bound.  This is what prevents a
    # failed/partial event write from leaving a new dangling edge.
    endpoint_count = connection.execute(
        "SELECT COUNT(*) FROM events WHERE uid = ? AND char_id = ? AND realm = ? AND event_id IN (?, ?)",
        (scope.uid, scope.character_id, scope.domain, from_event_id, to_event_id),
    ).fetchone()[0]
    if endpoint_count != 2:
        return
    _edge_observe("attempted")
    try:
        connection.execute(
            """INSERT INTO event_edges
               (uid, char_id, from_event_id, to_event_id, edge_type,
                relation_type, origin, confidence, created_at, schema_version)
               VALUES (?, ?, ?, ?, ?, ?, 'system', 1.0, ?, ?)""",
            (scope.uid, scope.character_id, from_event_id, to_event_id,
             relation_type, relation_type, created_at, SCHEMA_VERSION),
        )
        _edge_observe("written")
    except sqlite3.IntegrityError:
        _edge_observe("duplicates")
    except Exception:
        _edge_observe("failed")
        raise


def _relation_hints(row: Any) -> dict[str, str]:
    try:
        raw = row["relation_hints_json"] if isinstance(row, sqlite3.Row) else row.relation_hints_json
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError, IndexError):
        return {}
    return value if isinstance(value, dict) else {}


def _stream_partition(record: EventRecord | sqlite3.Row) -> tuple[str, str]:
    """Return the explicit adjacency partition for an event.

    ``source`` is part of the stream identity.  This keeps source-isolated
    evidence from becoming a temporal shortcut into an ordinary Reality turn.
    """
    stream = str(record["stream"] if isinstance(record, sqlite3.Row) else record.stream)
    channel = str(record["channel"] if isinstance(record, sqlite3.Row) else record.channel)
    source = record["source"] if isinstance(record, sqlite3.Row) else record.source
    from core.memory.source_policy import partition_key
    return stream or channel, partition_key(source)


def _delete_edge_pair(
    connection: sqlite3.Connection,
    scope: MemoryScope,
    left_event_id: str,
    right_event_id: str,
) -> int:
    """Remove exactly the bidirectional adjacency pair split by a late event."""
    cursor = connection.execute(
        """DELETE FROM event_edges
           WHERE uid = ? AND char_id = ?
             AND ((from_event_id = ? AND to_event_id = ? AND relation_type = 'next')
               OR (from_event_id = ? AND to_event_id = ? AND relation_type = 'previous'))""",
        (
            scope.uid, scope.character_id,
            left_event_id, right_event_id,
            right_event_id, left_event_id,
        ),
    )
    return max(0, int(cursor.rowcount))


def _ensure_deterministic_edges(
    connection: sqlite3.Connection,
    scope: MemoryScope,
    record: EventRecord | sqlite3.Row,
) -> None:
    """Update only the new event's finite deterministic neighbourhood.

    Earlier revisions rebuilt every edge in a scope for every append.  This
    routine intentionally reads at most two stream neighbours and the current
    turn; late insertion deletes the one now-invalid adjacency pair before
    inserting its replacement.  It never scans the ledger or historical turns.
    """
    edge_started = time.perf_counter()
    now = time.time()
    event_id = str(record["event_id"] if isinstance(record, sqlite3.Row) else record.event_id)
    occurred_at = float(record["occurred_at"] if isinstance(record, sqlite3.Row) else record.occurred_at)
    seq = int(record["seq"] if isinstance(record, sqlite3.Row) else record.seq)
    stream, source = _stream_partition(record)
    ordering = (occurred_at, occurred_at, seq, seq, event_id)
    scope_params = (scope.uid, scope.character_id, scope.domain, stream, source)

    with _OBSERVABILITY_LOCK:
        _HOT_PATH_OBSERVABILITY["stream_queries"] += 2
    predecessor = connection.execute(
        """SELECT * FROM events
           WHERE uid=? AND char_id=? AND realm=? AND stream=? AND source=?
             AND (occurred_at < ? OR (occurred_at = ? AND (seq < ? OR (seq = ? AND event_id < ?))))
           ORDER BY occurred_at DESC, seq DESC, event_id DESC LIMIT 1""",
        (*scope_params, *ordering),
    ).fetchone()
    successor = connection.execute(
        """SELECT * FROM events
           WHERE uid=? AND char_id=? AND realm=? AND stream=? AND source=?
             AND (occurred_at > ? OR (occurred_at = ? AND (seq > ? OR (seq = ? AND event_id > ?))))
           ORDER BY occurred_at ASC, seq ASC, event_id ASC LIMIT 1""",
        (*scope_params, *ordering),
    ).fetchone()
    if predecessor is not None and successor is not None:
        removed = _delete_edge_pair(connection, scope, predecessor["event_id"], successor["event_id"])
        with _OBSERVABILITY_LOCK:
            _HOT_PATH_OBSERVABILITY["edges_removed"] += removed
    if predecessor is not None:
        _insert_edge(connection, scope, predecessor["event_id"], event_id, "next", created_at=now)
        _insert_edge(connection, scope, event_id, predecessor["event_id"], "previous", created_at=now)
    if successor is not None:
        _insert_edge(connection, scope, event_id, successor["event_id"], "next", created_at=now)
        _insert_edge(connection, scope, successor["event_id"], event_id, "previous", created_at=now)

    turn_id = str(record["turn_id"] if isinstance(record, sqlite3.Row) else record.turn_id)
    if turn_id:
        with _OBSERVABILITY_LOCK:
            _HOT_PATH_OBSERVABILITY["turn_queries"] += 1
        turn_rows = connection.execute(
            """SELECT * FROM events WHERE uid=? AND char_id=? AND realm=?
               AND turn_id=? AND source=? AND actor IN ('user', 'assistant')
               ORDER BY seq ASC, event_id ASC LIMIT 2""",
            (scope.uid, scope.character_id, scope.domain, turn_id, source),
        ).fetchall()
        user = next((row for row in turn_rows if str(row["actor"] or "") == "user"), None)
        assistant = next((row for row in turn_rows if str(row["actor"] or "") == "assistant"), None)
        if user is not None and assistant is not None:
            _insert_edge(connection, scope, user["event_id"], assistant["event_id"], "same_turn", created_at=now)
            _insert_edge(connection, scope, assistant["event_id"], user["event_id"], "reply_to", created_at=now)

    hints = _relation_hints(record)
    if hints:
        for relation in ("triggered_by", "derived_from", "correction_of", "media_of", "reply_to"):
            target = str(hints.get(relation) or "")
            if target:
                _insert_edge(connection, scope, event_id, target, relation, created_at=now)
    with _OBSERVABILITY_LOCK:
        _HOT_PATH_OBSERVABILITY["edge_ms_total"] += round(
            (time.perf_counter() - edge_started) * 1000
        )


def edge_observability_snapshot(scope: MemoryScope) -> dict[str, Any]:
    """Return content-free edge counts, including dangling endpoints."""
    result: dict[str, Any] = {
        "scope": {"uid": scope.uid, "char_id": scope.character_id, "realm": scope.domain},
        "edge_count": 0, "by_relation": {}, "dangling_count": 0,
        "duplicate_writes": 0, "failed_writes": 0,
    }
    path = _path(scope)
    if not path.exists():
        return result
    with _lock_for(path):
        try:
            with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                columns = {row[1] for row in connection.execute("PRAGMA table_info(event_edges)")}
                if version != SCHEMA_VERSION or not {"relation_type", "origin", "confidence", "schema_version"} <= columns:
                    result["error_code"] = "schema_mismatch"
                    return result
                rows = connection.execute(
                    """SELECT e.relation_type, e.edge_type,
                       CASE WHEN f.event_id IS NULL OR t.event_id IS NULL THEN 1 ELSE 0 END
                       FROM event_edges e
                       LEFT JOIN events f ON f.uid=e.uid AND f.char_id=e.char_id AND f.realm=? AND f.event_id=e.from_event_id
                       LEFT JOIN events t ON t.uid=e.uid AND t.char_id=e.char_id AND t.realm=? AND t.event_id=e.to_event_id
                       WHERE e.uid=? AND e.char_id=?""",
                    (scope.domain, scope.domain, scope.uid, scope.character_id),
                ).fetchall()
            for relation, legacy_relation, dangling in rows:
                relation = str(relation or legacy_relation or "unknown")
                result["edge_count"] += 1
                result["by_relation"][relation] = result["by_relation"].get(relation, 0) + 1
                result["dangling_count"] += int(dangling)
        except Exception:
            result["error_code"] = "database_error"
    with _OBSERVABILITY_LOCK:
        result["duplicate_writes"] = int(_EDGE_OBSERVABILITY["duplicates"])
        result["failed_writes"] = int(_EDGE_OBSERVABILITY["failed"])
    return result


def recent_events_for_proposal(scope: MemoryScope, *, limit: int = 8) -> list[dict[str, Any]]:
    """Read a bounded, scope-frozen event window for the proposer only."""
    if not isinstance(limit, int) or limit < 1 or limit > 50:
        raise ValueError("invalid_event_window")
    path = _path(scope)
    if not path.exists():
        return []
    with _lock_for(path):
        try:
            with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0) as connection:
                connection.row_factory = sqlite3.Row
                candidates = connection.execute(
                    """SELECT event_id, source
                       FROM events WHERE uid=? AND char_id=? AND realm=?
                       ORDER BY occurred_at DESC, seq DESC, event_id DESC LIMIT ?""",
                    (scope.uid, scope.character_id, scope.domain, min(200, limit * 4)),
                ).fetchall()
                from core.memory.source_policy import is_isolated
                filtered_count = len(candidates) - sum(
                    not is_isolated(row["source"]) for row in candidates
                )
                with _OBSERVABILITY_LOCK:
                    _PROPOSER_SOURCE_OBSERVABILITY["input_count"] += len(candidates)
                    _PROPOSER_SOURCE_OBSERVABILITY["filtered_count"] += filtered_count
                from core.memory.source_policy import sql_predicate
                source_clause, source_params = sql_predicate()
                allowed_rows = connection.execute(
                    """SELECT event_id FROM events
                       WHERE uid=? AND char_id=? AND realm=?""" + source_clause + """
                       ORDER BY occurred_at DESC, seq DESC, event_id DESC LIMIT ?""",
                    (scope.uid, scope.character_id, scope.domain, *source_params, limit),
                ).fetchall()
                allowed_ids = [str(row["event_id"]) for row in allowed_rows]
                if not allowed_ids:
                    return []
                placeholders = ", ".join("?" for _ in allowed_ids)
                rows = connection.execute(
                    f"""SELECT event_id, occurred_at, actor, kind, channel,
                                COALESCE(NULLIF(memory_text, ''), visible_text) AS text
                         FROM events WHERE uid=? AND char_id=? AND realm=?
                           AND event_id IN ({placeholders})
                         ORDER BY occurred_at DESC, seq DESC, event_id DESC""",
                    (scope.uid, scope.character_id, scope.domain, *allowed_ids),
                ).fetchall()
            return [
                {
                    "event_id": str(row["event_id"]),
                    "occurred_at": float(row["occurred_at"] or 0),
                    "actor": str(row["actor"] or ""),
                    "kind": str(row["kind"] or ""),
                    "channel": str(row["channel"] or ""),
                    "text": str(row["text"] or "")[:512],
                }
                for row in reversed(rows)
            ]
        except Exception as exc:
            logger.warning("[event_store] proposal event window failed: %s", exc)
            return []


def existing_ledger_is_healthy(scope: MemoryScope) -> bool:
    """Check a pre-existing ledger without creating or migrating one."""
    return existing_ledger_health_code(scope) == "ok"


def existing_ledger_health_code(scope: MemoryScope) -> str:
    """Strict read-only proposer health classification."""
    path = _path(scope)
    if not path.is_file():
        return "missing"
    with _lock_for(path):
        try:
            with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25) as connection:
                connection.execute("PRAGMA query_only=ON")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                names = {
                    str(row[0]) for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                required = {
                    "events": {"event_id", "uid", "char_id", "realm", "occurred_at", "seq", "actor", "kind", "channel", "memory_text", "visible_text"},
                    "event_edges": {"uid", "char_id", "from_event_id", "to_event_id", "relation_type"},
                    "event_topics": {"uid", "char_id", "event_id", "topic"},
                    "event_edge_proposals": {"uid", "char_id", "realm", "from_event_id", "to_event_id", "relation_type"},
                    "event_edge_proposer_runs": {"uid", "char_id", "realm", "created_at", "day_key", "token_budget", "status"},
                }
                if version != SCHEMA_VERSION:
                    return "version_mismatch"
                if not set(required).issubset(names):
                    return "table_missing"
                for table, columns in required.items():
                    actual = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
                    if not columns.issubset(actual):
                        return "column_missing"
                scoped = connection.execute(
                    "SELECT 1 FROM events WHERE (uid<>? OR char_id<>? OR realm<>?) LIMIT 1",
                    (scope.uid, scope.character_id, scope.domain),
                ).fetchone()
            return "ok" if scoped is None else "scope_mismatch"
        except sqlite3.OperationalError as exc:
            return "timeout" if "locked" in str(exc).lower() or "busy" in str(exc).lower() else "database_error"
        except (sqlite3.Error, OSError, ValueError):
            return "database_error"


def proposal_budget_snapshot(scope: MemoryScope, day_key: str) -> dict[str, int]:
    """Return persisted per-day call/token usage without creating a ledger."""
    path = _path(scope)
    if not path.exists():
        return {"calls": 0, "tokens": 0}
    with _lock_for(path):
        try:
            with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0) as connection:
                row = connection.execute(
                    """SELECT COUNT(*), COALESCE(SUM(token_budget), 0)
                       FROM event_edge_proposer_runs
                       WHERE uid=? AND char_id=? AND realm=? AND day_key=?""",
                    (scope.uid, scope.character_id, scope.domain, day_key),
                ).fetchone()
            return {"calls": int(row[0] or 0), "tokens": int(row[1] or 0)}
        except Exception:
            return {"calls": 0, "tokens": 0}


def latest_proposer_run_at(scope: MemoryScope) -> float:
    path = _path(scope)
    if not path.exists():
        return 0.0
    with _lock_for(path):
        try:
            with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0) as connection:
                row = connection.execute(
                    """SELECT COALESCE(MAX(created_at), 0) FROM event_edge_proposer_runs
                       WHERE uid=? AND char_id=? AND realm=?""",
                    (scope.uid, scope.character_id, scope.domain),
                ).fetchone()
            return float(row[0] or 0.0)
        except Exception:
            return 0.0


def append_edge_proposal(scope: MemoryScope, proposal: Mapping[str, Any]) -> bool:
    """Insert one validated model proposal; never touches deterministic edges."""
    if not isinstance(proposal, Mapping):
        raise ValueError("invalid_proposal")
    relation = str(proposal.get("relation_type") or "")
    from_id = str(proposal.get("from_event_id") or "").strip()
    to_id = str(proposal.get("to_event_id") or "").strip()
    reason = str(proposal.get("reason") or "").strip()[:240]
    try:
        confidence = float(proposal.get("confidence"))
    except (TypeError, ValueError):
        raise ValueError("invalid_proposal") from None
    if relation not in PROPOSAL_RELATION_TYPES or not from_id or not to_id or from_id == to_id:
        raise ValueError("invalid_proposal")
    if not reason or not 0.0 <= confidence <= 1.0:
        raise ValueError("invalid_proposal")
    path = _path(scope)
    if not existing_ledger_is_healthy(scope):
        raise RuntimeError("schema_mismatch")
    model = str(proposal.get("model") or "")[:160]
    preset = str(proposal.get("preset") or "")[:160]
    model_version = str(proposal.get("model_version") or "")[:80]
    prompt_hash = str(proposal.get("prompt_hash") or "")[:128]
    created_at = float(proposal.get("created_at") or time.time())
    with _lock_for(path):
        with _connect(path) as connection:
            endpoints = connection.execute(
                """SELECT event_id, source FROM events
                   WHERE uid=? AND char_id=? AND realm=? AND event_id IN (?, ?)""",
                (scope.uid, scope.character_id, scope.domain, from_id, to_id),
            ).fetchall()
            if len(endpoints) != 2:
                raise ValueError("invalid_proposal_scope")
            from core.memory.source_policy import is_isolated
            if any(is_isolated(row[1]) for row in endpoints):
                with _OBSERVABILITY_LOCK:
                    _PROPOSER_SOURCE_OBSERVABILITY["filtered_count"] += 1
                raise ValueError("invalid_proposal_source")
            cursor = connection.execute(
                """INSERT OR IGNORE INTO event_edge_proposals
                   (uid, char_id, realm, from_event_id, to_event_id, relation_type,
                    reason, confidence, model, preset, model_version, prompt_hash,
                    created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed')""",
                (scope.uid, scope.character_id, scope.domain, from_id, to_id, relation,
                 reason, confidence, model, preset, model_version, prompt_hash, created_at),
            )
            connection.commit()
            return cursor.rowcount == 1


def record_proposer_run(scope: MemoryScope, *, day_key: str, input_count: int,
                        candidate_count: int, token_budget: int, model: str,
                        preset: str, model_version: str, prompt_hash: str,
                        status: str, error_code: str = "") -> None:
    path = _path(scope)
    if not existing_ledger_is_healthy(scope):
        raise RuntimeError("schema_mismatch")
    with _lock_for(path):
        with _connect(path) as connection:
            connection.execute(
                """INSERT INTO event_edge_proposer_runs
                   (uid, char_id, realm, created_at, day_key, input_count,
                    candidate_count, token_budget, model, preset, model_version,
                    prompt_hash, status, error_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (scope.uid, scope.character_id, scope.domain, time.time(), day_key,
                 int(input_count), int(candidate_count), int(token_budget),
                 str(model)[:160], str(preset)[:160], str(model_version)[:80],
                 str(prompt_hash)[:128], str(status)[:32], str(error_code)[:64]),
            )
            connection.commit()


def edge_proposal_observability_snapshot(
    scope: MemoryScope, *, day_key: str = "", daily_call_limit: int = 0,
    daily_token_limit: int = 0,
) -> dict[str, Any]:
    """Content-free proposal/run counters for the admin observability surface."""
    result: dict[str, Any] = {
        "scope": {"uid": scope.uid, "char_id": scope.character_id, "realm": scope.domain},
        "runs": 0, "candidate_count": 0, "failed_count": 0, "duplicate_count": 0,
        "inserted_count": 0, "latest_run_at": 0.0, "by_status": {}, "by_error": {},
        "by_relation": {}, "daily": {"day_key": day_key, "calls": 0, "tokens": 0,
                                       "call_limit": daily_call_limit, "token_limit": daily_token_limit},
    }
    with _OBSERVABILITY_LOCK:
        result["source_policy"] = dict(_PROPOSER_SOURCE_OBSERVABILITY)
    path = _path(scope)
    if not path.exists():
        return result
    with _lock_for(path):
        try:
            with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0) as connection:
                run_row = connection.execute(
                    """SELECT COUNT(*), COALESCE(SUM(candidate_count), 0),
                              COALESCE(SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END), 0),
                              COALESCE(MAX(created_at), 0)
                       FROM event_edge_proposer_runs WHERE uid=? AND char_id=? AND realm=?""",
                    (scope.uid, scope.character_id, scope.domain),
                ).fetchone()
                result["runs"] = int(run_row[0])
                result["candidate_count"] = int(run_row[1])
                result["failed_count"] = int(run_row[2])
                result["latest_run_at"] = float(run_row[3])
                for status, count in connection.execute(
                    """SELECT status, COUNT(*) FROM event_edge_proposer_runs
                       WHERE uid=? AND char_id=? AND realm=? GROUP BY status""",
                    (scope.uid, scope.character_id, scope.domain),
                ):
                    result["by_status"][str(status or "unknown")] = int(count)
                for error_code, count in connection.execute(
                    """SELECT error_code, COUNT(*) FROM event_edge_proposer_runs
                       WHERE uid=? AND char_id=? AND realm=? AND error_code != '' GROUP BY error_code""",
                    (scope.uid, scope.character_id, scope.domain),
                ):
                    result["by_error"][str(error_code)] = int(count)
                for row in connection.execute(
                    """SELECT relation_type, COUNT(*) FROM event_edge_proposals
                       WHERE uid=? AND char_id=? AND realm=? GROUP BY relation_type""",
                    (scope.uid, scope.character_id, scope.domain),
                ):
                    result["by_relation"][str(row[0])] = int(row[1])
                result["inserted_count"] = sum(result["by_relation"].values())
                result["duplicate_count"] = max(
                    0, result["candidate_count"] - result["inserted_count"],
                )
                if day_key:
                    day = connection.execute(
                        """SELECT COUNT(*), COALESCE(SUM(token_budget), 0)
                           FROM event_edge_proposer_runs
                           WHERE uid=? AND char_id=? AND realm=? AND day_key=?""",
                        (scope.uid, scope.character_id, scope.domain, day_key),
                    ).fetchone()
                    result["daily"]["calls"], result["daily"]["tokens"] = map(int, day)
        except Exception:
            result["error_code"] = "database_error"
    return result


# Short aliases keep the adapter small while retaining one public write path.
append = append_event
get_schema_status = schema_status
