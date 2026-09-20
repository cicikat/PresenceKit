"""Scoped, revisioned character topic memory (Brief 258 B).

This store is derived state. It references the Reality evidence ledger but does
not copy evidence prose and never writes to an evidence or legacy-memory store.
All mutations enter through :func:`apply_operations`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope

SCHEMA_VERSION = 4
SOURCE_POLICY_REVISION = "memory-dossier-source-policy.v1"
RULES_REVISION = "memory-dossier-rules.v1"
MAX_BATCH_OPERATIONS = 100
MAX_CLAIM_BATCH = 100
DEFAULT_LEASE_SECONDS = 900
MAX_TEXT_CHARS = 4000
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_VALID_CHAINS = frozenset({"owner_chat", "maintenance", "admin_recovery"})
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


class DossierError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SchemaStatus:
    exists: bool
    healthy: bool
    schema_version: int
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists, "healthy": self.healthy,
            "schema_version": self.schema_version, "error_code": self.error_code,
        }


def _scope(scope: MemoryScope) -> MemoryScope:
    if not isinstance(scope, MemoryScope) or scope.domain != "reality" or not scope.character_id:
        raise DossierError("invalid_scope")
    return scope


def _path(scope: MemoryScope) -> Path:
    return resolve_path(_scope(scope), "memory_dossiers")


def _lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: object, *, required: bool = False, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        raise DossierError("invalid_text")
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise DossierError("invalid_text")
    return value


def _id(value: object, field: str = "id") -> str:
    value = str(value or "")
    if not _ID_RE.fullmatch(value):
        raise DossierError(f"invalid_{field}")
    return value


def _new_id() -> str:
    return uuid.uuid4().hex


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=0.25)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=250")
    return connection


def _initialize(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version not in {0, 1, 2, 3, SCHEMA_VERSION}:
        raise DossierError("schema_mismatch")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS dossiers (
          dossier_id TEXT PRIMARY KEY, title TEXT NOT NULL, aliases_json TEXT NOT NULL,
          description TEXT NOT NULL, status TEXT NOT NULL,
          redirect_dossier_id TEXT, revision INTEGER NOT NULL,
          active_understanding_id TEXT, needs_recompute INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS occurrences (
          occurrence_id TEXT PRIMARY KEY, occurrence_key TEXT,
          participants_json TEXT NOT NULL, occurred_from REAL, occurred_to REAL,
          time_certainty TEXT NOT NULL, assertion_kind TEXT NOT NULL,
          experience_state TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL,
          created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_occurrence_key
          ON occurrences(occurrence_key) WHERE occurrence_key IS NOT NULL;
        CREATE TABLE IF NOT EXISTS occurrence_evidence (
          occurrence_id TEXT NOT NULL REFERENCES occurrences(occurrence_id),
          reference_kind TEXT NOT NULL, source_id TEXT NOT NULL,
          source_revision TEXT NOT NULL, valid INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(occurrence_id, reference_kind, source_id, source_revision)
        );
        CREATE INDEX IF NOT EXISTS idx_evidence_reverse
          ON occurrence_evidence(reference_kind, source_id, valid);
        CREATE TABLE IF NOT EXISTS memberships (
          dossier_id TEXT NOT NULL REFERENCES dossiers(dossier_id),
          occurrence_id TEXT NOT NULL REFERENCES occurrences(occurrence_id),
          status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
          PRIMARY KEY(dossier_id, occurrence_id)
        );
        CREATE TABLE IF NOT EXISTS relations (
          relation_id TEXT PRIMARY KEY, from_dossier_id TEXT NOT NULL REFERENCES dossiers(dossier_id),
          to_dossier_id TEXT NOT NULL REFERENCES dossiers(dossier_id), relation_type TEXT NOT NULL,
          tentative INTEGER NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL,
          created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS understandings (
          understanding_id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL REFERENCES dossiers(dossier_id),
          revision INTEGER NOT NULL, summary TEXT NOT NULL, conditions_json TEXT NOT NULL,
          occurred_from REAL, occurred_to REAL, supporting_json TEXT NOT NULL,
          counterexamples_json TEXT NOT NULL, confidence_reason TEXT NOT NULL,
          character_feeling INTEGER NOT NULL, coverage_ingest_seq INTEGER NOT NULL,
          source_policy_revision TEXT NOT NULL, supersedes_id TEXT,
          status TEXT NOT NULL, created_at REAL NOT NULL,
          UNIQUE(dossier_id, revision)
        );
        CREATE TABLE IF NOT EXISTS operations (
          operation_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
          actor TEXT NOT NULL, chain TEXT NOT NULL, status TEXT NOT NULL,
          expected_revisions_json TEXT NOT NULL, committed_revisions_json TEXT NOT NULL,
          source_policy_revision TEXT NOT NULL, rules_revision TEXT NOT NULL,
          reversal_of TEXT, result_json TEXT NOT NULL, created_at REAL NOT NULL,
          committed_at REAL
        );
        CREATE TABLE IF NOT EXISTS invalidations (
          invalidation_id TEXT PRIMARY KEY, reference_kind TEXT NOT NULL,
          source_id TEXT NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_items (
          store_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_revision TEXT NOT NULL,
          ingest_sequence INTEGER NOT NULL, status TEXT NOT NULL, semantic_outcomes_json TEXT NOT NULL,
          attempt INTEGER NOT NULL DEFAULT 0, operation_id TEXT, input_digest TEXT NOT NULL,
          last_error TEXT NOT NULL, revisit_condition TEXT NOT NULL, updated_at REAL NOT NULL,
          rule_version TEXT NOT NULL DEFAULT '', target_revision TEXT NOT NULL DEFAULT '',
          lease_until REAL NOT NULL DEFAULT 0, task_id TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(store_kind, source_id, source_revision)
        );
        CREATE TABLE IF NOT EXISTS processing_commits (
          commit_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
          store_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_revision TEXT NOT NULL,
          ingest_sequence INTEGER NOT NULL, created_at REAL NOT NULL,
          UNIQUE(store_kind, source_id, source_revision, operation_id)
        );
        CREATE TABLE IF NOT EXISTS maintenance_runs (
          run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, work_session_id TEXT NOT NULL,
          status TEXT NOT NULL, input_count INTEGER NOT NULL, input_chars INTEGER NOT NULL,
          token_budget INTEGER NOT NULL, model TEXT NOT NULL, preset TEXT NOT NULL,
          identity_revision TEXT NOT NULL, prompt_revision TEXT NOT NULL,
          rules_revision TEXT NOT NULL, error_code TEXT NOT NULL,
          started_at REAL NOT NULL, finished_at REAL, wall_seconds REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS maintenance_state (
          state_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dossier_status_title ON dossiers(status, title);
        CREATE INDEX IF NOT EXISTS idx_membership_occurrence ON memberships(occurrence_id, status);
        CREATE INDEX IF NOT EXISTS idx_source_items_status ON source_items(status, store_kind, ingest_sequence);
        """
    )
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(source_items)")}
    if "rule_version" not in columns:
        connection.execute("ALTER TABLE source_items ADD COLUMN rule_version TEXT NOT NULL DEFAULT ''")
    if "target_revision" not in columns:
        connection.execute("ALTER TABLE source_items ADD COLUMN target_revision TEXT NOT NULL DEFAULT ''")
    if "lease_until" not in columns:
        connection.execute("ALTER TABLE source_items ADD COLUMN lease_until REAL NOT NULL DEFAULT 0")
    if "task_id" not in columns:
        connection.execute("ALTER TABLE source_items ADD COLUMN task_id TEXT NOT NULL DEFAULT ''")
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def initialize(scope: MemoryScope) -> SchemaStatus:
    path = _path(scope)
    with _lock(path):
        try:
            with _connect(path) as connection:
                _initialize(connection)
                connection.commit()
            return SchemaStatus(True, True, SCHEMA_VERSION)
        except DossierError as exc:
            return SchemaStatus(True, False, 0, exc.code)
        except (OSError, sqlite3.Error):
            return SchemaStatus(True, False, 0, "database_error")


def schema_status(scope: MemoryScope) -> SchemaStatus:
    path = _path(scope)
    if not path.exists():
        return SchemaStatus(False, False, 0, "not_initialized")
    with _lock(path):
        try:
            with _connect(path, readonly=True) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"dossiers", "occurrences", "occurrence_evidence", "memberships", "relations", "understandings", "operations", "invalidations", "source_items", "processing_commits", "maintenance_runs", "maintenance_state"}
            healthy = version == SCHEMA_VERSION and required <= tables
            return SchemaStatus(True, healthy, version, "" if healthy else "schema_mismatch")
        except (OSError, sqlite3.Error):
            return SchemaStatus(True, False, 0, "database_error")


def _row(connection: sqlite3.Connection, table: str, key: str, value: str) -> sqlite3.Row:
    row = connection.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
    if row is None:
        raise DossierError(f"{table[:-1]}_not_found")
    return row


def _expect_revision(row: sqlite3.Row, operation: Mapping[str, Any]) -> None:
    expected = operation.get("expected_revision")
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise DossierError("expected_revision_required")
    if int(row["revision"]) != expected:
        raise DossierError("revision_conflict")


def _event_refs(operations: Iterable[Mapping[str, Any]]) -> list[str]:
    result: list[str] = []
    for operation in operations:
        if operation.get("action") == "create_occurrence":
            for ref in operation.get("evidence", []):
                if isinstance(ref, Mapping) and ref.get("reference_kind") == "event":
                    result.append(str(ref.get("source_id") or ""))
    return result


def _validate_events(scope: MemoryScope, ids: Iterable[str]) -> None:
    from core.memory.event_query import get_event
    for event_id in set(ids):
        if not event_id or get_event(scope, event_id) is None:
            raise DossierError("evidence_not_found")


def _create_dossier(connection: sqlite3.Connection, op: Mapping[str, Any], now: float) -> dict[str, Any]:
    dossier_id = _id(op.get("dossier_id") or _new_id(), "dossier_id")
    title = _text(op.get("title"), required=True, limit=200)
    aliases = op.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 20:
        raise DossierError("invalid_aliases")
    aliases = [_text(item, required=True, limit=200) for item in aliases]
    connection.execute("INSERT INTO dossiers VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (dossier_id, title, _json(aliases), _text(op.get("description", "")), "active", None, 1, None, 0, now, now))
    return {"dossier_id": dossier_id, "revision": 1}


def _rename(connection: sqlite3.Connection, op: Mapping[str, Any], now: float) -> dict[str, Any]:
    dossier_id = _id(op.get("dossier_id"), "dossier_id")
    row = _row(connection, "dossiers", "dossier_id", dossier_id); _expect_revision(row, op)
    revision = int(row["revision"]) + 1
    connection.execute("UPDATE dossiers SET title=?, revision=?, updated_at=? WHERE dossier_id=?",
                       (_text(op.get("title"), required=True, limit=200), revision, now, dossier_id))
    return {"dossier_id": dossier_id, "revision": revision}


def _create_occurrence(connection: sqlite3.Connection, op: Mapping[str, Any], now: float) -> dict[str, Any]:
    occurrence_id = _id(op.get("occurrence_id") or _new_id(), "occurrence_id")
    key = _text(op.get("occurrence_key", ""), limit=256) or None
    if key and connection.execute("SELECT occurrence_id FROM occurrences WHERE occurrence_key=?", (key,)).fetchone():
        raise DossierError("duplicate_occurrence_candidate")
    participants = op.get("participants", [])
    if not isinstance(participants, list) or len(participants) > 20:
        raise DossierError("invalid_participants")
    certainty = str(op.get("time_certainty") or "unknown")
    assertion = str(op.get("assertion_kind") or "legacy_unknown")
    experience_state = str(op.get("experience_state") or "confirmed")
    if (certainty not in {"exact", "bounded", "unknown"}
            or assertion not in {"user_stated", "observed", "inferred", "legacy_unknown"}
            or experience_state not in {"confirmed", "planned", "cancelled", "reported", "hypothetical", "assistant_suggestion"}):
        raise DossierError("invalid_occurrence")
    start, end = op.get("occurred_from"), op.get("occurred_to")
    if start is not None: start = float(start)
    if end is not None: end = float(end)
    if start is not None and end is not None and start > end:
        raise DossierError("invalid_time_range")
    evidence = op.get("evidence", [])
    if not isinstance(evidence, list) or not evidence:
        raise DossierError("evidence_required")
    connection.execute("INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (occurrence_id, key, _json(participants), start, end, certainty, assertion,
                        experience_state, "active", 1, now, now))
    for ref in evidence:
        if not isinstance(ref, Mapping): raise DossierError("invalid_evidence")
        kind = str(ref.get("reference_kind") or "")
        if kind not in {"event", "legacy_unknown", "ephemeral"}: raise DossierError("invalid_evidence")
        source_id = _text(ref.get("source_id"), required=True, limit=512)
        if kind == "ephemeral": raise DossierError("ephemeral_evidence_not_durable")
        connection.execute("INSERT INTO occurrence_evidence VALUES(?,?,?,?,1)",
                           (occurrence_id, kind, source_id, _text(str(ref.get("source_revision") or "unknown"), required=True, limit=256)))
    return {"occurrence_id": occurrence_id, "revision": 1}


def _attach(connection: sqlite3.Connection, op: Mapping[str, Any], now: float) -> dict[str, Any]:
    dossier_id = _id(op.get("dossier_id"), "dossier_id"); occurrence_id = _id(op.get("occurrence_id"), "occurrence_id")
    dossier = _row(connection, "dossiers", "dossier_id", dossier_id); _expect_revision(dossier, op)
    _row(connection, "occurrences", "occurrence_id", occurrence_id)
    connection.execute("INSERT INTO memberships VALUES(?,?, 'active',?,?) ON CONFLICT(dossier_id,occurrence_id) DO UPDATE SET status='active',updated_at=excluded.updated_at",
                       (dossier_id, occurrence_id, now, now))
    revision = int(dossier["revision"]) + 1
    connection.execute("UPDATE dossiers SET revision=?,updated_at=? WHERE dossier_id=?", (revision, now, dossier_id))
    return {"dossier_id": dossier_id, "occurrence_id": occurrence_id, "revision": revision}


def _revise(connection: sqlite3.Connection, op: Mapping[str, Any], now: float) -> dict[str, Any]:
    dossier_id = _id(op.get("dossier_id"), "dossier_id")
    dossier = _row(connection, "dossiers", "dossier_id", dossier_id); _expect_revision(dossier, op)
    support = [_id(item, "occurrence_id") for item in op.get("supporting_occurrence_ids", [])]
    counters = [_id(item, "occurrence_id") for item in op.get("counterexample_occurrence_ids", [])]
    for occurrence_id in set(support + counters): _row(connection, "occurrences", "occurrence_id", occurrence_id)
    revision = int(dossier["revision"]) + 1; understanding_id = _new_id()
    prior = dossier["active_understanding_id"]
    if prior:
        connection.execute("UPDATE understandings SET status='superseded' WHERE understanding_id=?", (prior,))
    connection.execute("INSERT INTO understandings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        understanding_id, dossier_id, revision, _text(op.get("summary"), required=True),
        _json(op.get("conditions", [])), op.get("occurred_from"), op.get("occurred_to"),
        _json(support), _json(counters), _text(op.get("confidence_reason", "")),
        int(bool(op.get("character_feeling"))), max(0, int(op.get("coverage_ingest_seq") or 0)),
        SOURCE_POLICY_REVISION, prior, "active", now))
    connection.execute("UPDATE dossiers SET active_understanding_id=?,revision=?,needs_recompute=0,updated_at=? WHERE dossier_id=?",
                       (understanding_id, revision, now, dossier_id))
    return {"dossier_id": dossier_id, "understanding_id": understanding_id, "revision": revision}


def _relate(connection: sqlite3.Connection, op: Mapping[str, Any], now: float) -> dict[str, Any]:
    source = _id(op.get("from_dossier_id"), "dossier_id"); target = _id(op.get("to_dossier_id"), "dossier_id")
    if source == target: raise DossierError("invalid_relation")
    source_row = _row(connection, "dossiers", "dossier_id", source); _expect_revision(source_row, op)
    _row(connection, "dossiers", "dossier_id", target)
    kind = str(op.get("relation_type") or "related")
    if kind not in {"related", "follows", "tentative_cause"}: raise DossierError("invalid_relation")
    tentative = 1 if kind == "tentative_cause" or bool(op.get("tentative")) else 0
    relation_id = _id(op.get("relation_id") or _new_id(), "relation_id")
    connection.execute("INSERT INTO relations VALUES(?,?,?,?,?,'active',1,?,?)",
                       (relation_id, source, target, kind, tentative, now, now))
    revision = int(source_row["revision"]) + 1
    connection.execute("UPDATE dossiers SET revision=?,updated_at=? WHERE dossier_id=?", (revision, now, source))
    return {"relation_id": relation_id, "dossier_id": source, "revision": revision}


def _status(connection: sqlite3.Connection, op: Mapping[str, Any], now: float) -> dict[str, Any]:
    dossier_id = _id(op.get("dossier_id"), "dossier_id")
    row = _row(connection, "dossiers", "dossier_id", dossier_id); _expect_revision(row, op)
    status = str(op.get("status") or "")
    if status not in {"active", "dormant", "retired"}: raise DossierError("invalid_status")
    revision = int(row["revision"]) + 1
    connection.execute("UPDATE dossiers SET status=?,redirect_dossier_id=NULL,revision=?,updated_at=? WHERE dossier_id=?",
                       (status, revision, now, dossier_id))
    return {"dossier_id": dossier_id, "status": status, "revision": revision}


def _merge(connection: sqlite3.Connection, op: Mapping[str, Any], now: float) -> dict[str, Any]:
    target = _id(op.get("target_dossier_id"), "dossier_id")
    target_row = _row(connection, "dossiers", "dossier_id", target)
    expected_target = op.get("expected_target_revision")
    if not isinstance(expected_target, int) or int(target_row["revision"]) != expected_target: raise DossierError("revision_conflict")
    sources = [_id(item, "dossier_id") for item in op.get("source_dossier_ids", [])]
    if not sources or target in sources: raise DossierError("invalid_merge")
    expected = op.get("expected_source_revisions", {})
    for source in sources:
        row = _row(connection, "dossiers", "dossier_id", source)
        if not isinstance(expected, Mapping) or expected.get(source) != int(row["revision"]): raise DossierError("revision_conflict")
        connection.execute("UPDATE dossiers SET status='merged',redirect_dossier_id=?,revision=revision+1,updated_at=? WHERE dossier_id=?", (target, now, source))
        connection.execute("INSERT OR IGNORE INTO memberships SELECT ?,occurrence_id,status,?,? FROM memberships WHERE dossier_id=? AND status='active'", (target, now, now, source))
    target_revision = int(target_row["revision"]) + 1
    connection.execute("UPDATE dossiers SET revision=?,updated_at=? WHERE dossier_id=?", (target_revision, now, target))
    return {"dossier_id": target, "revision": target_revision, "merged": sources}


def _split(connection: sqlite3.Connection, op: Mapping[str, Any], now: float) -> dict[str, Any]:
    source = _id(op.get("source_dossier_id"), "dossier_id")
    row = _row(connection, "dossiers", "dossier_id", source); _expect_revision(row, op)
    created = _create_dossier(connection, op.get("new_dossier", {}), now)
    occurrence_ids = [_id(item, "occurrence_id") for item in op.get("occurrence_ids", [])]
    if not occurrence_ids: raise DossierError("invalid_split")
    for occurrence_id in occurrence_ids:
        membership = connection.execute("SELECT status FROM memberships WHERE dossier_id=? AND occurrence_id=?", (source, occurrence_id)).fetchone()
        if membership is None or membership["status"] != "active": raise DossierError("membership_not_found")
        connection.execute("UPDATE memberships SET status='retired',updated_at=? WHERE dossier_id=? AND occurrence_id=?", (now, source, occurrence_id))
        connection.execute("INSERT INTO memberships VALUES(?,?,'active',?,?)", (created["dossier_id"], occurrence_id, now, now))
    revision = int(row["revision"]) + 1
    connection.execute("UPDATE dossiers SET revision=?,updated_at=? WHERE dossier_id=?", (revision, now, source))
    return {"dossier_id": source, "revision": revision, "split_dossier_id": created["dossier_id"]}


_HANDLERS = {"create_dossier": _create_dossier, "rename_dossier": _rename,
             "create_occurrence": _create_occurrence, "attach_occurrence": _attach,
             "revise_understanding": _revise, "relate_dossiers": _relate,
             "set_dossier_status": _status, "merge_dossiers": _merge,
             "split_dossier": _split}


def apply_operations(scope: MemoryScope, operations: list[Mapping[str, Any]], *, operation_id: str,
                     actor: str, chain: str, source_policy_revision: str = SOURCE_POLICY_REVISION,
                     reversal_of: str = "", processing_items: list[Mapping[str, Any]] | None = None,
                     maintenance_run_id: str = "", maintenance_wall_seconds: float = 0,
                     now: float | None = None) -> dict[str, Any]:
    """Validate and atomically commit one bounded, idempotent operation batch."""
    scope = _scope(scope); operation_id = _id(operation_id, "operation_id")
    if chain not in _VALID_CHAINS: raise DossierError("invalid_chain")
    actor = _text(actor, required=True, limit=200)
    if maintenance_run_id:
        maintenance_run_id = _id(maintenance_run_id, "run_id")
    processing_items = processing_items or []
    if (not isinstance(operations, list) or len(operations) > MAX_BATCH_OPERATIONS
            or not isinstance(processing_items, list) or len(processing_items) > MAX_BATCH_OPERATIONS
            or (not operations and not processing_items)):
        raise DossierError("invalid_operation_batch")
    if source_policy_revision != SOURCE_POLICY_REVISION: raise DossierError("source_policy_changed")
    for operation in operations:
        if not isinstance(operation, Mapping) or operation.get("action") not in _HANDLERS:
            raise DossierError("invalid_operation")
    normalized_items: list[dict[str, Any]] = []
    for item in processing_items:
        if not isinstance(item, Mapping): raise DossierError("invalid_processing_item")
        store_kind = _text(item.get("store_kind"), required=True, limit=64)
        source_id = _text(item.get("source_id"), required=True, limit=512)
        source_revision = _text(item.get("source_revision"), required=True, limit=256)
        ingest_sequence = int(item.get("ingest_sequence") or 0)
        input_digest = _text(item.get("input_digest"), required=True, limit=128)
        outcomes = item.get("semantic_outcomes", ["evidence_only"])
        if not isinstance(outcomes, list) or not outcomes: raise DossierError("invalid_processing_item")
        normalized_items.append({"store_kind": store_kind, "source_id": source_id,
            "source_revision": source_revision, "ingest_sequence": ingest_sequence,
            "input_digest": input_digest, "semantic_outcomes": [str(value)[:64] for value in outcomes]})
    request = {"operations": operations, "processing_items": normalized_items, "actor": actor, "chain": chain,
               "source_policy_revision": source_policy_revision, "reversal_of": reversal_of,
               "maintenance_run_id": maintenance_run_id}
    request_digest = _digest(request); timestamp = time.time() if now is None else float(now)
    path = _path(scope)
    # Receipts are authoritative for outcome-unknown recovery. Check one before
    # consulting mutable evidence so a committed operation remains replayable
    # after its source is later withdrawn.
    if path.exists():
        with _lock(path), _connect(path, readonly=True) as connection:
            existing = connection.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest: raise DossierError("idempotency_conflict")
                return json.loads(existing["result_json"])
    _validate_events(scope, _event_refs(operations))
    with _lock(path):
        with _connect(path) as connection:
            _initialize(connection)
            existing = connection.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest: raise DossierError("idempotency_conflict")
                return json.loads(existing["result_json"])
            try:
                connection.execute("BEGIN IMMEDIATE")
                results = [_HANDLERS[str(op["action"])](connection, op, timestamp) for op in operations]
                committed = {item["dossier_id"]: item["revision"] for item in results if "dossier_id" in item and "revision" in item}
                for item in normalized_items:
                    connection.execute("""INSERT INTO source_items
                      (store_kind,source_id,source_revision,ingest_sequence,status,semantic_outcomes_json,
                       attempt,operation_id,input_digest,last_error,revisit_condition,updated_at,
                       rule_version,target_revision)
                      VALUES(?,?,?,?, 'committed',?,1,?,?, '', '',?,?,?)
                      ON CONFLICT(store_kind,source_id,source_revision) DO UPDATE SET
                        ingest_sequence=excluded.ingest_sequence,status='committed',
                        semantic_outcomes_json=excluded.semantic_outcomes_json,
                        operation_id=excluded.operation_id,input_digest=excluded.input_digest,
                        last_error='',updated_at=excluded.updated_at,
                        rule_version=excluded.rule_version,lease_until=0""" ,
                      (item["store_kind"], item["source_id"], item["source_revision"], item["ingest_sequence"],
                       _json(item["semantic_outcomes"]), operation_id, item["input_digest"], timestamp,
                       RULES_REVISION, ""))
                    connection.execute("INSERT OR IGNORE INTO processing_commits VALUES(?,?,?,?,?,?,?)",
                      (_new_id(), operation_id, item["store_kind"], item["source_id"],
                       item["source_revision"], item["ingest_sequence"], timestamp))
                if normalized_items:
                    checkpoint = max(item["ingest_sequence"] for item in normalized_items)
                    connection.execute("""INSERT INTO maintenance_state VALUES('event_checkpoint',?,?)
                      ON CONFLICT(state_key) DO UPDATE SET value_json=excluded.value_json,
                      updated_at=excluded.updated_at""", (_json(checkpoint), timestamp))
                result = {"ok": True, "operation_id": operation_id, "results": results,
                          "committed_revisions": committed, "processed": len(normalized_items)}
                connection.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    operation_id, request_digest, actor, chain, "committed", "{}", _json(committed),
                    source_policy_revision, RULES_REVISION, reversal_of or None, _json(result), timestamp, timestamp))
                if maintenance_run_id:
                    cursor = connection.execute(
                        """UPDATE maintenance_runs SET status='committed',error_code='',finished_at=?,wall_seconds=?
                           WHERE run_id=? AND status='running'""",
                        (timestamp, max(0.0, float(maintenance_wall_seconds)), maintenance_run_id),
                    )
                    if cursor.rowcount != 1:
                        raise DossierError("maintenance_run_not_running")
                connection.commit()
            except Exception:
                connection.rollback(); raise
    try:
        from core.memory import provenance_log
        source_ids = _event_refs(operations)
        provenance_log.append(scope.uid, scope.character_id or "", artifact="memory_dossier",
                              field="batch", after_gist=f"{len(operations)} operations",
                              trigger_signal=chain, source_event_ids=source_ids,
                              origin={"operation_id": operation_id, "actor": actor, "chain": chain})
    except Exception:
        pass
    return result


def invalidate_source(scope: MemoryScope, source_id: str, *, reason: str,
                      reference_kind: str = "event", now: float | None = None) -> dict[str, int]:
    """Immediately suppress conclusions depending on a withdrawn source."""
    scope = _scope(scope); source_id = _text(source_id, required=True, limit=512)
    reason = _text(reason, required=True, limit=200); path = _path(scope)
    if not path.exists(): return {"occurrences": 0, "dossiers": 0}
    timestamp = time.time() if now is None else float(now)
    with _lock(path), _connect(path) as connection:
        _initialize(connection); connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT occurrence_id FROM occurrence_evidence WHERE reference_kind=? AND source_id=? AND valid=1", (reference_kind, source_id)).fetchall()
        occurrence_ids = [str(row[0]) for row in rows]
        if occurrence_ids:
            placeholders = ",".join("?" for _ in occurrence_ids)
            connection.execute("UPDATE occurrence_evidence SET valid=0 WHERE reference_kind=? AND source_id=?", (reference_kind, source_id))
            connection.execute(f"UPDATE occurrences SET status='needs_recompute',revision=revision+1,updated_at=? WHERE occurrence_id IN ({placeholders})", (timestamp, *occurrence_ids))
            dossiers = connection.execute(f"SELECT DISTINCT dossier_id FROM memberships WHERE status='active' AND occurrence_id IN ({placeholders})", occurrence_ids).fetchall()
        else: dossiers = []
        dossier_ids = [str(row[0]) for row in dossiers]
        for dossier_id in dossier_ids:
            connection.execute("UPDATE dossiers SET needs_recompute=1,active_understanding_id=NULL,revision=revision+1,updated_at=? WHERE dossier_id=?", (timestamp, dossier_id))
            connection.execute("UPDATE understandings SET status='invalidated' WHERE dossier_id=? AND status='active'", (dossier_id,))
        connection.execute("INSERT INTO invalidations VALUES(?,?,?,?,?)", (_new_id(), reference_kind, source_id, reason, timestamp))
        connection.commit()
    return {"occurrences": len(occurrence_ids), "dossiers": len(dossier_ids)}


def search(scope: MemoryScope, query: str = "", *, limit: int = 3) -> list[dict[str, Any]]:
    scope = _scope(scope)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20: raise DossierError("invalid_limit")
    path = _path(scope)
    if not path.exists(): return []
    needle = f"%{str(query or '').strip()}%"
    with _lock(path), _connect(path, readonly=True) as connection:
        rows = connection.execute("""SELECT d.*,u.summary,u.occurred_from,u.occurred_to,u.coverage_ingest_seq
          FROM dossiers d LEFT JOIN understandings u ON u.understanding_id=d.active_understanding_id
          WHERE d.status IN ('active','dormant') AND d.needs_recompute=0
            AND (?='%%' OR d.title LIKE ? OR d.aliases_json LIKE ? OR COALESCE(u.summary,'') LIKE ?)
          ORDER BY d.updated_at DESC,d.dossier_id LIMIT ?""", (needle, needle, needle, needle, limit)).fetchall()
    return [{"dossier_id": row["dossier_id"], "title": row["title"], "status": row["status"],
             "revision": row["revision"], "summary": row["summary"] or "",
             "occurred_from": row["occurred_from"], "occurred_to": row["occurred_to"],
             "coverage_ingest_seq": row["coverage_ingest_seq"] or 0} for row in rows]


def read(scope: MemoryScope, dossier_id: str) -> dict[str, Any] | None:
    scope = _scope(scope); dossier_id = _id(dossier_id, "dossier_id"); path = _path(scope)
    if not path.exists(): return None
    with _lock(path), _connect(path, readonly=True) as connection:
        row = connection.execute("SELECT * FROM dossiers WHERE dossier_id=?", (dossier_id,)).fetchone()
        if row is None: return None
        understanding = connection.execute("SELECT * FROM understandings WHERE understanding_id=?", (row["active_understanding_id"],)).fetchone() if row["active_understanding_id"] else None
        occurrences = connection.execute("""SELECT o.* FROM occurrences o JOIN memberships m ON m.occurrence_id=o.occurrence_id
          WHERE m.dossier_id=? AND m.status='active' ORDER BY o.occurred_to DESC,o.created_at DESC""", (dossier_id,)).fetchall()
        relations = connection.execute("SELECT * FROM relations WHERE (from_dossier_id=? OR to_dossier_id=?) AND status='active'", (dossier_id, dossier_id)).fetchall()
        confirmed_count = connection.execute("""SELECT COUNT(*) FROM occurrences o
          JOIN memberships m ON m.occurrence_id=o.occurrence_id
          WHERE m.dossier_id=? AND m.status='active' AND o.status='active'
            AND o.experience_state='confirmed'""", (dossier_id,)).fetchone()[0]
    result = dict(row); result["aliases"] = json.loads(result.pop("aliases_json")); result["occurrences"] = [dict(item) for item in occurrences]
    result["relations"] = [dict(item) for item in relations]; result["understanding"] = dict(understanding) if understanding else None
    result["confirmed_occurrence_count"] = int(confirmed_count)
    if result["understanding"]:
        for key in ("conditions_json", "supporting_json", "counterexamples_json"):
            result["understanding"][key.removesuffix("_json")] = json.loads(result["understanding"].pop(key))
    return result


def operation_receipt(scope: MemoryScope, operation_id: str) -> dict[str, Any] | None:
    scope = _scope(scope); operation_id = _id(operation_id, "operation_id"); path = _path(scope)
    if not path.exists(): return None
    with _lock(path), _connect(path, readonly=True) as connection:
        row = connection.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
    if row is None: return None
    result = dict(row); result["result"] = json.loads(result.pop("result_json")); return result


def dossier_events(scope: MemoryScope, dossier_id: str, *, offset: int = 0,
                   limit: int = 20) -> dict[str, Any]:
    """Return bounded occurrence/evidence metadata; never copies evidence text."""
    scope = _scope(scope); dossier_id = _id(dossier_id, "dossier_id")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise DossierError("invalid_offset")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise DossierError("invalid_limit")
    path = _path(scope)
    if not path.exists(): return {"items": [], "next_offset": None, "truncated": False}
    with _lock(path), _connect(path, readonly=True) as connection:
        _row(connection, "dossiers", "dossier_id", dossier_id)
        rows = connection.execute("""SELECT o.* FROM occurrences o
          JOIN memberships m ON m.occurrence_id=o.occurrence_id
          WHERE m.dossier_id=? AND m.status='active'
          ORDER BY COALESCE(o.occurred_to,o.occurred_from,o.created_at) DESC,o.occurrence_id
          LIMIT ? OFFSET ?""", (dossier_id, limit + 1, offset)).fetchall()
        items = []
        for row in rows[:limit]:
            evidence = connection.execute("""SELECT reference_kind,source_id,source_revision,valid
              FROM occurrence_evidence WHERE occurrence_id=? ORDER BY reference_kind,source_id""",
              (row["occurrence_id"],)).fetchall()
            item = dict(row); item["participants"] = json.loads(item.pop("participants_json"))
            item["evidence"] = [dict(ref) for ref in evidence]; items.append(item)
    truncated = len(rows) > limit
    return {"items": items, "next_offset": offset + limit if truncated else None, "truncated": truncated}


def _query_terms(query: str) -> list[str]:
    value = str(query or "").strip().casefold()
    if not value: return []
    terms = [item for item in re.split(r"[\s,.;:!?，。；：！？、]+", value) if len(item) >= 2]
    return terms[:8] or [value[:80]]


def _unprocessed_evidence(scope: MemoryScope, *, after_sequence: int, query: str,
                          limit: int = 4) -> list[dict[str, Any]]:
    """Read late/new evidence by ledger ingest order (SQLite rowid), fail closed."""
    from core.memory import event_store, source_policy
    event_path = resolve_path(scope, "event_store")
    if not event_path.exists(): return []
    terms = _query_terms(query)
    clauses = ["LOWER(COALESCE(NULLIF(memory_text,''),visible_text)) LIKE ?" for _ in terms]
    params: list[Any] = [scope.uid, scope.character_id, scope.domain, int(after_sequence)]
    source_clause, source_params = source_policy.sql_predicate()
    params.extend(source_params)
    params.extend(f"%{term}%" for term in terms)
    params.append(limit)
    query_clause = " AND (" + " OR ".join(clauses) + ")" if clauses else ""
    try:
        with event_store._lock_for(event_path), sqlite3.connect(
            f"{event_path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25,
        ) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("""SELECT rowid AS ingest_sequence,event_id,occurred_at,
                actor,kind,COALESCE(NULLIF(memory_text,''),visible_text) AS text
              FROM events WHERE uid=? AND char_id=? AND realm=? AND rowid>?""" +
              source_clause + query_clause + " ORDER BY rowid DESC LIMIT ?", params).fetchall()
        return [{"ingest_sequence": int(row["ingest_sequence"]), "event_id": row["event_id"],
                 "occurred_at": row["occurred_at"], "actor": row["actor"], "kind": row["kind"],
                 "text": str(row["text"] or "")[:240]} for row in rows]
    except (OSError, sqlite3.Error):
        return []


def build_recall_context(scope: MemoryScope, query: str, *, max_dossiers: int = 3,
                         max_chars: int = 1200) -> dict[str, Any]:
    """Build the bounded automatic layer with current and unprocessed evidence."""
    max_dossiers = min(3, max(1, int(max_dossiers)))
    max_chars = min(1200, max(200, int(max_chars)))
    rows = search(scope, query, limit=max_dossiers)
    if not rows: return {"text": "", "dossier_ids": [], "truncated": False, "unreviewed": False}
    parts: list[str] = []
    remaining = max_chars
    unreviewed = False
    for row in rows:
        line = f"[{row['title']}] {row['summary'] or '尚无稳定理解'}"
        if row["occurred_from"] is not None or row["occurred_to"] is not None:
            line += f"；覆盖时间 {row['occurred_from'] or '?'}..{row['occurred_to'] or '?'}"
        new_events = _unprocessed_evidence(scope, after_sequence=int(row["coverage_ingest_seq"] or 0), query=query)
        if new_events:
            unreviewed = True
            line += "\n未整理的新证据（与旧理解并列，尚未归纳）：" + "；".join(
                f"{item['event_id']} {item['text']}" for item in new_events
            )
        line += f"\n详情入口 dossier_id={row['dossier_id']}"
        if len(line) > remaining:
            line = line[:max(0, remaining - 1)] + "…"
        if line:
            parts.append(line); remaining -= len(line) + 1
        if remaining <= 1: break
    text = "\n".join(parts)
    return {"text": text, "dossier_ids": [row["dossier_id"] for row in rows[:len(parts)]],
            "truncated": len(text) >= max_chars - 1, "unreviewed": unreviewed}


def status_snapshot(scope: MemoryScope) -> dict[str, Any]:
    """Content-free scope status used by tools and the later control plane."""
    scope = _scope(scope); status = schema_status(scope); path = _path(scope)
    result = {**status.to_dict(), "scope": {"char_id": scope.character_id, "realm": "reality"},
              "dossiers": 0, "active": 0, "needs_recompute": 0, "operations": 0,
              "pending_sources": 0, "latest_operation_at": None}
    if not status.healthy or not path.exists(): return result
    with _lock(path), _connect(path, readonly=True) as connection:
        row = connection.execute("SELECT COUNT(*),SUM(status='active'),SUM(needs_recompute) FROM dossiers").fetchone()
        op = connection.execute("SELECT COUNT(*),MAX(committed_at) FROM operations WHERE status='committed'").fetchone()
        pending = connection.execute("SELECT COUNT(*) FROM source_items WHERE status IN ('pending','running','retryable_failed')").fetchone()[0]
    result.update({"dossiers": int(row[0] or 0), "active": int(row[1] or 0),
                   "needs_recompute": int(row[2] or 0), "operations": int(op[0] or 0),
                   "latest_operation_at": op[1], "pending_sources": int(pending or 0)})
    return result


def maintenance_checkpoint(scope: MemoryScope) -> int:
    scope = _scope(scope); path = _path(scope)
    if not path.exists(): return 0
    with _lock(path), _connect(path) as connection:
        _initialize(connection)
        connection.commit()
        row = connection.execute("SELECT value_json FROM maintenance_state WHERE state_key='event_checkpoint'").fetchone()
    try: return max(0, int(json.loads(row[0]))) if row else 0
    except (TypeError, ValueError, json.JSONDecodeError): return 0


def maintenance_candidates(scope: MemoryScope, *, limit: int = 100,
                           max_chars: int = 24000) -> list[dict[str, Any]]:
    """Read a bounded source batch after the durable ingest checkpoint."""
    from core.memory import event_store, source_policy
    scope = _scope(scope); checkpoint = maintenance_checkpoint(scope)
    event_path = resolve_path(scope, "event_store")
    if not event_path.exists(): return []
    limit = min(100, max(1, int(limit))); max_chars = min(48000, max(1000, int(max_chars)))
    source_clause, source_params = source_policy.sql_predicate()
    with event_store._lock_for(event_path):
        try:
            with sqlite3.connect(f"{event_path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute("""SELECT rowid AS ingest_sequence,event_id,occurred_at,
                  ingested_at,actor,kind,source,redaction_state,
                  COALESCE(NULLIF(memory_text,''),visible_text) AS text
                  FROM events WHERE uid=? AND char_id=? AND realm=? AND rowid>?""" + source_clause +
                  " ORDER BY rowid ASC LIMIT ?", (scope.uid, scope.character_id, scope.domain,
                  checkpoint, *source_params, limit)).fetchall()
        except sqlite3.Error as exc:
            raise DossierError("evidence_read_failed") from exc
    result, used = [], 0
    for row in rows:
        text = str(row["text"] or "")[:1000]
        size = len(text)
        if result and used + size > max_chars: break
        revision = hashlib.sha256(_json({"event_id": row["event_id"], "ingested_at": row["ingested_at"],
            "redaction_state": row["redaction_state"], "text": text}).encode("utf-8")).hexdigest()
        result.append({"store_kind": "event", "source_id": str(row["event_id"]),
            "source_revision": revision, "ingest_sequence": int(row["ingest_sequence"]),
            "occurred_at": float(row["occurred_at"] or 0), "actor": str(row["actor"] or ""),
            "kind": str(row["kind"] or ""), "text": text,
            "input_digest": hashlib.sha256(text.encode("utf-8")).hexdigest()})
        used += size
    return result


def reopen_evidence_only(scope: MemoryScope, *, source_ids: list[str] | None = None) -> int:
    """Requeue explicit evidence-only receipts for a later semantic pass."""
    scope = _scope(scope); path = _path(scope)
    if not path.exists():
        return 0
    with _lock(path), _connect(path) as connection:
        _initialize(connection)
        params: list[Any] = []
        clause = "store_kind='event' AND status='committed' AND semantic_outcomes_json LIKE '%evidence_only%'"
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            clause += f" AND source_id IN ({placeholders})"
            params.extend(str(value) for value in source_ids)
        rows = connection.execute(
            f"SELECT source_id,ingest_sequence FROM source_items WHERE {clause}", params,
        ).fetchall()
        if not rows:
            return 0
        connection.execute(
            f"UPDATE source_items SET status='pending',semantic_outcomes_json='[\"evidence_only\"]',last_error='reopened_for_semantic_pass' WHERE {clause}",
            params,
        )
        minimum = min(int(row[1]) for row in rows)
        connection.execute(
            "INSERT INTO maintenance_state VALUES('event_checkpoint',?,?) "
            "ON CONFLICT(state_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
            (_json(max(0, minimum - 1)), time.time()),
        )
        connection.commit()
    return len(rows)


_SOURCE_ITEM_STATES = ("pending", "running", "committed", "retryable_failed", "deferred", "excluded")


def _source_item_select_extras(connection: sqlite3.Connection) -> str:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(source_items)")}
    extras = [name for name in ("rule_version", "target_revision", "lease_until", "task_id")
              if name in columns]
    return ("," + ",".join(extras)) if extras else ""


def _source_item_payload(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        outcomes = json.loads(item.pop("semantic_outcomes_json") or "[]")
    except json.JSONDecodeError:
        outcomes = []
    item["semantic_outcomes"] = outcomes if isinstance(outcomes, list) else []
    item.setdefault("rule_version", "")
    item.setdefault("target_revision", "")
    item.setdefault("lease_until", 0.0)
    item.setdefault("task_id", "")
    return item


def seed_source_items(scope: MemoryScope, items: list[Mapping[str, Any]], *,
                      now: float | None = None) -> dict[str, int]:
    """Insert pending source-item receipts without copying source prose.

    Existing rows for the same store_kind/source_id/source_revision keep their
    processing status. A changed source revision leaves the old row in place
    and opens a new pending row.
    """
    scope = _scope(scope)
    if not isinstance(items, list) or len(items) > 10_000:
        raise DossierError("invalid_source_item_batch")
    timestamp = time.time() if now is None else float(now)
    inserted = skipped = 0
    path = _path(scope)
    with _lock(path), _connect(path) as connection:
        _initialize(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            for item in items:
                if not isinstance(item, Mapping):
                    raise DossierError("invalid_processing_item")
                store_kind = _text(item.get("store_kind"), required=True, limit=64)
                source_id = _text(item.get("source_id"), required=True, limit=512)
                source_revision = _text(item.get("source_revision"), required=True, limit=256)
                ingest_sequence = int(item.get("ingest_sequence") or 0)
                input_digest = _text(item.get("input_digest") or source_revision, required=True, limit=128)
                rule_version = _text(item.get("rule_version") or RULES_REVISION, required=True, limit=128)
                revisit = _text(item.get("revisit_condition") or "source_revision_changed", limit=128)
                existing = connection.execute(
                    "SELECT status FROM source_items WHERE store_kind=? AND source_id=? AND source_revision=?",
                    (store_kind, source_id, source_revision),
                ).fetchone()
                if existing is not None:
                    skipped += 1
                    continue
                connection.execute(
                    """INSERT INTO source_items
                      (store_kind,source_id,source_revision,ingest_sequence,status,semantic_outcomes_json,
                       attempt,operation_id,input_digest,last_error,revisit_condition,updated_at,
                       rule_version,target_revision)
                      VALUES(?,?,?,?,'pending','[]',0,'',?, '', ?, ?, ?, '')""",
                    (store_kind, source_id, source_revision, ingest_sequence, input_digest,
                     revisit, timestamp, rule_version),
                )
                inserted += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"inserted": inserted, "skipped": skipped, "total": inserted + skipped}


def list_source_items(scope: MemoryScope, *, store_kind: str = "", status: str = "",
                      source_id: str = "", offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Return a bounded, content-free page of source-item processing receipts."""
    scope = _scope(scope)
    path = _path(scope)
    offset = max(0, int(offset))
    limit = min(100, max(1, int(limit)))
    empty = {"total": 0, "offset": offset, "limit": limit, "items": []}
    if not path.exists():
        return empty
    filters = ["1=1"]
    params: list[Any] = []
    if store_kind:
        filters.append("store_kind=?")
        params.append(_text(store_kind, required=True, limit=64))
    if status:
        if status not in _SOURCE_ITEM_STATES:
            raise DossierError("invalid_source_item_status")
        filters.append("status=?")
        params.append(status)
    if source_id:
        filters.append("source_id=?")
        params.append(_text(source_id, required=True, limit=512))
    where = " AND ".join(filters)
    with _lock(path), _connect(path, readonly=True) as connection:
        extras = _source_item_select_extras(connection)
        total = int(connection.execute(f"SELECT COUNT(*) FROM source_items WHERE {where}", params).fetchone()[0])
        rows = connection.execute(
            f"""SELECT store_kind,source_id,source_revision,ingest_sequence,status,semantic_outcomes_json,
                       attempt,operation_id,input_digest,last_error,revisit_condition,updated_at{extras}
                FROM source_items WHERE {where}
                ORDER BY store_kind,ingest_sequence,source_id LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
    return {"total": total, "offset": offset, "limit": limit,
            "items": [_source_item_payload(row) for row in rows]}


def source_item_counts(scope: MemoryScope) -> dict[str, int]:
    """Return processing-status counts over the per-source-item ledger."""
    scope = _scope(scope)
    path = _path(scope)
    counts = {name: 0 for name in _SOURCE_ITEM_STATES}
    if not path.exists():
        return counts
    with _lock(path), _connect(path, readonly=True) as connection:
        for status, total in connection.execute("SELECT status,COUNT(*) FROM source_items GROUP BY status"):
            if status in counts:
                counts[str(status)] = int(total)
    return counts


def _reconcile_source_item_leases_unlocked(connection: sqlite3.Connection, now: float) -> dict[str, int]:
    """Recover expired running rows from durable receipts before any retry."""
    extras = _source_item_select_extras(connection)
    rows = connection.execute(
        f"""SELECT store_kind,source_id,source_revision,ingest_sequence,status,semantic_outcomes_json,
                   attempt,operation_id,input_digest,last_error,revisit_condition,updated_at{extras}
            FROM source_items WHERE status='running' AND lease_until>0 AND lease_until<?""",
        (now,),
    ).fetchall()
    committed = released = 0
    for row in rows:
        receipt = connection.execute(
            """SELECT operation_id FROM processing_commits
               WHERE store_kind=? AND source_id=? AND source_revision=?
               ORDER BY created_at DESC LIMIT 1""",
            (row["store_kind"], row["source_id"], row["source_revision"]),
        ).fetchone()
        if receipt is not None:
            connection.execute(
                """UPDATE source_items SET status='committed',operation_id=?,last_error='',
                   lease_until=0,updated_at=?
                   WHERE store_kind=? AND source_id=? AND source_revision=? AND status='running'""",
                (str(receipt["operation_id"]), now, row["store_kind"], row["source_id"],
                 row["source_revision"]),
            )
            committed += 1
            continue
        connection.execute(
            """UPDATE source_items SET status='retryable_failed',last_error='lease_expired',
               lease_until=0,updated_at=?
               WHERE store_kind=? AND source_id=? AND source_revision=? AND status='running'""",
            (now, row["store_kind"], row["source_id"], row["source_revision"]),
        )
        released += 1
    return {"committed": committed, "released": released, "inspected": len(rows)}


def reconcile_source_item_leases(scope: MemoryScope, *, now: float | None = None) -> dict[str, int]:
    """Mark expired running items from receipts; otherwise return them to retryable_failed."""
    scope = _scope(scope)
    path = _path(scope)
    timestamp = time.time() if now is None else float(now)
    empty = {"committed": 0, "released": 0, "inspected": 0}
    if not path.exists():
        return empty
    with _lock(path), _connect(path) as connection:
        _initialize(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            result = _reconcile_source_item_leases_unlocked(connection, timestamp)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return result


def claim_source_items(scope: MemoryScope, *, limit: int = 10, lease_seconds: int = DEFAULT_LEASE_SECONDS,
                       task_id: str = "", store_kind: str = "", now: float | None = None) -> dict[str, Any]:
    """Claim a bounded, stably ordered source-item batch with a recoverable lease."""
    scope = _scope(scope)
    limit = min(MAX_CLAIM_BATCH, max(1, int(limit)))
    lease_seconds = min(3600, max(1, int(lease_seconds)))
    task = _text(task_id, limit=64) if task_id else ""
    kind = _text(store_kind, required=True, limit=64) if store_kind else ""
    timestamp = time.time() if now is None else float(now)
    lease_until = timestamp + lease_seconds
    path = _path(scope)
    empty = {"items": [], "count": 0, "reconciled": {"committed": 0, "released": 0, "inspected": 0}}
    if not path.exists():
        return empty
    with _lock(path), _connect(path) as connection:
        _initialize(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            reconciled = _reconcile_source_item_leases_unlocked(connection, timestamp)
            filters = ["status IN ('pending','retryable_failed')"]
            params: list[Any] = []
            if kind:
                filters.append("store_kind=?")
                params.append(kind)
            extras = _source_item_select_extras(connection)
            rows = connection.execute(
                f"""SELECT store_kind,source_id,source_revision,ingest_sequence,status,semantic_outcomes_json,
                           attempt,operation_id,input_digest,last_error,revisit_condition,updated_at{extras}
                    FROM source_items WHERE {' AND '.join(filters)}
                    ORDER BY store_kind,ingest_sequence,source_id LIMIT ?""",
                (*params, limit),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                connection.execute(
                    """UPDATE source_items SET status='running',attempt=attempt+1,lease_until=?,
                       task_id=?,last_error='',updated_at=?
                       WHERE store_kind=? AND source_id=? AND source_revision=?
                         AND status IN ('pending','retryable_failed')""",
                    (lease_until, task, timestamp, row["store_kind"], row["source_id"],
                     row["source_revision"]),
                )
                item = _source_item_payload(row)
                item.update({"status": "running", "attempt": int(row["attempt"] or 0) + 1,
                             "lease_until": lease_until, "task_id": task, "updated_at": timestamp,
                             "last_error": ""})
                items.append(item)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"items": items, "count": len(items), "reconciled": reconciled}


def fail_source_item_claim(scope: MemoryScope, items: list[Mapping[str, Any]], *,
                           error: str, now: float | None = None) -> int:
    """Return a claimed batch to retryable_failed without recording a commit."""
    scope = _scope(scope)
    path = _path(scope)
    if not path.exists() or not items:
        return 0
    timestamp = time.time() if now is None else float(now)
    message = _text(error, required=True, limit=128)
    updated = 0
    with _lock(path), _connect(path) as connection:
        _initialize(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                cursor = connection.execute(
                    """UPDATE source_items SET status='retryable_failed',last_error=?,lease_until=0,
                       updated_at=?
                       WHERE store_kind=? AND source_id=? AND source_revision=? AND status='running'""",
                    (message, timestamp,
                     _text(item.get("store_kind"), required=True, limit=64),
                     _text(item.get("source_id"), required=True, limit=512),
                     _text(item.get("source_revision"), required=True, limit=256)),
                )
                updated += int(cursor.rowcount or 0)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return updated


def related_dossiers_for_sources(scope: MemoryScope, refs: list[Mapping[str, Any]], *,
                                 limit: int = 20) -> list[dict[str, Any]]:
    """Return dossiers that already cite these source identities.

    Titles, aliases and membership lists are presentation only and are not
    idempotency keys. Callers must use dossier_id plus revision. Evidence
    prose is never copied.
    """
    scope = _scope(scope)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise DossierError("invalid_limit")
    if not isinstance(refs, list) or not refs:
        return []
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs[:MAX_CLAIM_BATCH]:
        if not isinstance(ref, Mapping):
            continue
        kind = str(ref.get("store_kind") or ref.get("reference_kind") or "").strip()
        source_id = str(ref.get("source_id") or "").strip()
        if not kind or not source_id:
            continue
        pair = (_text(kind, required=True, limit=64), _text(source_id, required=True, limit=512))
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(pair)
    if not pairs:
        return []
    path = _path(scope)
    if not path.exists():
        return []
    clauses = " OR ".join("(e.reference_kind=? AND e.source_id=?)" for _ in pairs)
    params: list[Any] = [item for pair in pairs for item in pair]
    with _lock(path), _connect(path, readonly=True) as connection:
        rows = connection.execute(
            f"""SELECT d.dossier_id,d.title,d.status,d.revision,d.needs_recompute,
                       COALESCE(u.summary,'') AS summary,e.reference_kind,e.source_id,e.occurrence_id
                FROM occurrence_evidence e
                JOIN memberships m ON m.occurrence_id=e.occurrence_id AND m.status='active'
                JOIN dossiers d ON d.dossier_id=m.dossier_id
                LEFT JOIN understandings u ON u.understanding_id=d.active_understanding_id
                WHERE d.status IN ('active','dormant') AND e.valid=1 AND ({clauses})
                ORDER BY d.updated_at DESC,d.dossier_id""",
            params,
        ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = grouped.get(row["dossier_id"])
        if item is None:
            if len(grouped) >= limit:
                continue
            item = {"dossier_id": row["dossier_id"], "title": row["title"], "status": row["status"],
                    "revision": int(row["revision"]), "needs_recompute": int(row["needs_recompute"] or 0),
                    "summary": str(row["summary"] or "")[:320], "matching_source_ids": [],
                    "occurrence_ids": []}
            grouped[row["dossier_id"]] = item
        if row["source_id"] not in item["matching_source_ids"]:
            item["matching_source_ids"].append(str(row["source_id"]))
        if row["occurrence_id"] not in item["occurrence_ids"]:
            item["occurrence_ids"].append(str(row["occurrence_id"]))
    return list(grouped.values())


def begin_maintenance_run(scope: MemoryScope, *, run_id: str, task_id: str,
                          work_session_id: str, input_count: int, input_chars: int,
                          token_budget: int, model: str, preset: str,
                          identity_revision: str, prompt_revision: str,
                          now: float | None = None) -> None:
    scope = _scope(scope); run_id = _id(run_id, "run_id"); path = _path(scope)
    timestamp = time.time() if now is None else float(now)
    with _lock(path), _connect(path) as connection:
        _initialize(connection)
        connection.execute("INSERT OR IGNORE INTO maintenance_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
          (run_id, _id(task_id, "task_id"), _id(work_session_id, "work_session_id"), "running",
           int(input_count), int(input_chars), int(token_budget), str(model)[:160], str(preset)[:160],
           str(identity_revision)[:128], str(prompt_revision)[:128], RULES_REVISION, "", timestamp, None))
        connection.commit()


def finish_maintenance_run(scope: MemoryScope, run_id: str, *, status: str,
                           error_code: str = "", wall_seconds: float = 0,
                           now: float | None = None) -> None:
    scope = _scope(scope); run_id = _id(run_id, "run_id"); path = _path(scope)
    timestamp = time.time() if now is None else float(now)
    with _lock(path), _connect(path) as connection:
        connection.execute("UPDATE maintenance_runs SET status=?,error_code=?,finished_at=?,wall_seconds=? WHERE run_id=?",
                           (str(status)[:32], str(error_code)[:64], timestamp, max(0.0, float(wall_seconds)), run_id))
        connection.commit()


def maintenance_budget(scope: MemoryScope, *, since: float) -> dict[str, float | int]:
    scope = _scope(scope); path = _path(scope)
    if not path.exists(): return {"calls": 0, "tokens": 0, "wall_seconds": 0.0}
    with _lock(path), _connect(path, readonly=True) as connection:
        row = connection.execute("SELECT COUNT(*),COALESCE(SUM(token_budget),0),COALESCE(SUM(wall_seconds),0) FROM maintenance_runs WHERE started_at>=?", (float(since),)).fetchone()
    return {"calls": int(row[0] or 0), "tokens": int(row[1] or 0), "wall_seconds": float(row[2] or 0)}


def committed_maintenance_run(scope: MemoryScope, task_id: str) -> dict[str, Any] | None:
    scope = _scope(scope); task_id = _id(task_id, "task_id"); path = _path(scope)
    if not path.exists(): return None
    with _lock(path), _connect(path, readonly=True) as connection:
        row = connection.execute("SELECT * FROM maintenance_runs WHERE task_id=? AND status='committed' ORDER BY finished_at DESC LIMIT 1", (task_id,)).fetchone()
    return dict(row) if row else None


def latest_maintenance_run(scope: MemoryScope, task_id: str) -> dict[str, Any] | None:
    """Return the latest durable run receipt for outcome reconciliation."""
    scope = _scope(scope); task_id = _id(task_id, "task_id"); path = _path(scope)
    if not path.exists():
        return None
    with _lock(path), _connect(path, readonly=True) as connection:
        row = connection.execute(
            "SELECT * FROM maintenance_runs WHERE task_id=? ORDER BY started_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    return dict(row) if row else None


def maintenance_status(scope: MemoryScope) -> dict[str, Any]:
    """Return content-free consolidation progress for the authenticated control plane."""
    from core.memory import event_store, source_policy

    scope = _scope(scope)
    checkpoint = maintenance_checkpoint(scope)
    path = _path(scope)
    source_counts: dict[str, int] = {}
    run_counts: dict[str, int] = {}
    rows: list[sqlite3.Row] = []
    if path.exists():
        with _lock(path), _connect(path, readonly=True) as connection:
            source_counts = {str(row[0]): int(row[1]) for row in connection.execute(
                "SELECT status,COUNT(*) FROM source_items GROUP BY status"
            )}
            run_counts = {str(row[0]): int(row[1]) for row in connection.execute(
                "SELECT status,COUNT(*) FROM maintenance_runs GROUP BY status"
            )}
            rows = connection.execute(
                """SELECT run_id,task_id,work_session_id,status,input_count,input_chars,
                          token_budget,model,preset,identity_revision,prompt_revision,
                          rules_revision,error_code,started_at,finished_at,wall_seconds
                   FROM maintenance_runs ORDER BY started_at DESC LIMIT 20"""
            ).fetchall()
    backlog = 0
    event_path = resolve_path(scope, "event_store")
    if event_path.exists():
        source_clause, source_params = source_policy.sql_predicate()
        try:
            with event_store._lock_for(event_path), sqlite3.connect(
                f"{event_path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25,
            ) as connection:
                backlog = int(connection.execute(
                    "SELECT COUNT(*) FROM events WHERE uid=? AND char_id=? AND realm=? AND rowid>?" +
                    source_clause,
                    (scope.uid, scope.character_id, scope.domain, checkpoint, *source_params),
                ).fetchone()[0])
        except sqlite3.Error:
            backlog = -1
    runs = []
    for row in rows:
        item = dict(row)
        item["run_digest"] = hashlib.sha256(item.pop("run_id").encode()).hexdigest()[:16]
        item["task_digest"] = hashlib.sha256(item.pop("task_id").encode()).hexdigest()[:16]
        item["work_session_digest"] = hashlib.sha256(
            item.pop("work_session_id").encode()
        ).hexdigest()[:16]
        runs.append(item)
    return {
        "coverage_ingest_sequence": checkpoint,
        "backlog": backlog,
        "source_status_counts": dict(sorted(source_counts.items())),
        "run_status_counts": dict(sorted(run_counts.items())),
        "current_batch": next((item for item in runs if item["status"] == "running"), None),
        "recent_runs": runs,
        # A running batch is visible as current_batch. Only an explicitly
        # unknown durable outcome is counted as unverified by the worker.
        "unverified": 0,
    }
