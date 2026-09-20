"""Read-only, content-free inventory for Brief 259 history reconciliation.

This module deliberately does not create ledgers, call a model, or mutate any
memory store.  It produces a versioned inventory and stable source revisions so
an independently authorized batch worker can later consume it.  Derived-store
receipts are recorded as evidence-only processing items; source files remain
untouched.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.sandbox import get_paths
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope
from core.safe_write import safe_write_json

SCHEMA_VERSION = "memory-history-inventory.v1"
SOURCES = ("event_store", "event_log", "mid_term", "episodic", "storyline", "user_identity")
STATES = ("pending", "running", "committed", "retryable_failed", "deferred", "excluded")
RULES_VERSION = "history-reconciliation-rules.v1"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "bytes": 0, "revision": "missing", "readable": True}
    if not path.is_file():
        return {"exists": True, "bytes": 0, "revision": "not_a_file", "readable": False}
    try:
        stat = path.stat()
    except OSError:
        return {"exists": True, "bytes": 0, "revision": "stat_failed", "readable": False}
    # Hash metadata only. Inventory never reads or emits private content.
    revision = _digest({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return {"exists": True, "bytes": int(stat.st_size), "revision": revision, "readable": True}


def _scope_files(uid: str, char_id: str) -> dict[str, list[Path]]:
    scope = MemoryScope.reality_scope(uid, char_id)
    root = get_paths().user_memory_root(uid, char_id=char_id)
    event_log = resolve_path(scope, "event_log")
    return {
        "event_store": [resolve_path(scope, "event_store")],
        "event_log": sorted(event_log.glob("*.md")) if event_log.is_dir() else [],
        "mid_term": [resolve_path(scope, "mid_term")],
        "episodic": [resolve_path(scope, "episodic")],
        "storyline": [resolve_path(scope, "storyline"), resolve_path(scope, "storyline_inbox")],
        "user_identity": [resolve_path(scope, "identity")],
    }


def build_inventory(uid: str, char_id: str, *, now: float | None = None) -> dict[str, Any]:
    """Return a deterministic, redacted source inventory for one scope."""
    if not str(uid).strip() or not str(char_id).strip():
        raise ValueError("uid_and_char_id_required")
    files = _scope_files(str(uid), str(char_id))
    items: list[dict[str, Any]] = []
    for kind in SOURCES:
        entries = [_file_info(path) for path in files[kind]]
        items.append({
            "store_kind": kind,
            "file_count": sum(int(item["exists"]) for item in entries),
            "bytes": sum(int(item["bytes"]) for item in entries),
            "source_revision": _digest(entries),
            "readable": all(bool(item.get("readable")) for item in entries),
            "isolated": kind in {"event_store", "event_log", "mid_term", "episodic", "storyline", "user_identity"},
        })
    total = len(items)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {"uid_digest": _digest(str(uid))[:16], "char_id": str(char_id), "realm": "reality"},
        "generated_at": float(time.time() if now is None else now),
        "read_only": True,
        "model_calls": 0,
        "total_sources": total,
        "items": items,
        "inventory_revision": _digest(items),
    }


def _state_path(scope: MemoryScope) -> Path:
    return resolve_path(scope, "history_reconciliation_state")


def read_state(scope: MemoryScope) -> dict[str, Any]:
    path = _state_path(scope)
    if not path.exists():
        return {"schema_version": "memory-reconciliation-state.v1", "items": {}, "paused": False}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"schema_version": "memory-reconciliation-state.v1", "items": {}, "paused": False,
                "last_error": "state_corrupt"}


def create_manifest(scope: MemoryScope, *, now: float | None = None) -> dict[str, Any]:
    """Create a persisted, content-free manifest; this is always a dry-run."""
    inventory = build_inventory(scope.uid, scope.character_id, now=now)
    old = read_state(scope)
    prior = old.get("items") if isinstance(old.get("items"), dict) else {}
    items: dict[str, Any] = {}
    for item in inventory["items"]:
        key = f"{item['store_kind']}:{item['source_revision']}"
        previous = prior.get(key) if isinstance(prior.get(key), dict) else {}
        status = previous.get("status") if previous.get("status") in STATES else "pending"
        items[key] = {"store_kind": item["store_kind"], "source_revision": item["source_revision"],
                      "status": status, "attempt": int(previous.get("attempt") or 0),
                      "target_revision": str(previous.get("target_revision") or ""),
                      "operation_receipt": str(previous.get("operation_receipt") or ""),
                      "rule_version": RULES_VERSION,
                      "input_digest": str(previous.get("input_digest") or item["source_revision"]),
                      "revisit_condition": str(previous.get("revisit_condition") or "source_revision_changed")[:128],
                      "last_error": str(previous.get("last_error") or "")[:128]}
    manifest = {"schema_version": "memory-reconciliation-manifest.v1", "inventory": inventory,
                "items": items, "dry_run": True, "created_at": float(time.time() if now is None else now),
                "manifest_revision": _digest(items)}
    state = {"schema_version": "memory-reconciliation-state.v1", "manifest": manifest,
             "items": items, "paused": bool(old.get("paused")), "updated_at": manifest["created_at"]}
    if not safe_write_json(_state_path(scope), state, keep_bak=True):
        raise OSError("history_reconciliation_state_write_failed")
    return manifest


def set_paused(scope: MemoryScope, paused: bool, *, reason: str = "") -> dict[str, Any]:
    state = read_state(scope)
    state["paused"] = bool(paused); state["pause_reason"] = str(reason or "")[:128]; state["updated_at"] = time.time()
    if not safe_write_json(_state_path(scope), state, keep_bak=True):
        raise OSError("history_reconciliation_state_write_failed")
    return {"paused": state["paused"], "pause_reason": state["pause_reason"]}


def freeze_manifest(scope: MemoryScope, *, manifest_revision: str | None = None) -> dict[str, Any]:
    """Freeze one manifest revision before any first-night apply."""
    state = read_state(scope)
    manifest = state.get("manifest") if isinstance(state.get("manifest"), dict) else None
    revision = str((manifest or {}).get("manifest_revision") or "")
    if not revision or (manifest_revision and manifest_revision != revision):
        raise ValueError("manifest_revision_mismatch")
    state["frozen_manifest_revision"] = revision
    state["frozen_at"] = time.time()
    state["updated_at"] = state["frozen_at"]
    if not safe_write_json(_state_path(scope), state, keep_bak=True):
        raise OSError("history_reconciliation_state_write_failed")
    return {"frozen": True, "manifest_revision": revision, "frozen_at": state["frozen_at"]}


def apply_dry_run(scope: MemoryScope, *, backup_verified: bool = False) -> dict[str, Any]:
    """Advance only the batch ledger; source stores remain untouched.

    The write/apply path is deliberately delegated to event_migration and
    requires an explicit verified backup.  Other stores stay deferred until
    their authority-specific adapter is authorized.
    """
    state = read_state(scope)
    if state.get("paused"):
        return {"status": "paused", "reason": state.get("pause_reason", "")}
    manifest = state.get("manifest") or create_manifest(scope)
    items = state.get("items") or manifest.get("items") or {}
    if not backup_verified:
        for item in items.values():
            if item.get("status") == "pending":
                item["status"] = "deferred"; item["last_error"] = "backup_not_verified"
        state["items"] = items; state["updated_at"] = time.time(); safe_write_json(_state_path(scope), state, keep_bak=True)
        return {"status": "deferred", "reason": "backup_not_verified", "items": items}
    from core.memory import event_migration
    plan = event_migration.scan_legacy(scope)
    result = {"status": "committed" if not plan.get("indeterminate") else "deferred",
              "source_digest": plan.get("source_digest", ""), "would_write": plan.get("would_write", 0),
              "plan_conflict": plan.get("plan_conflict", 0), "items": items}
    for item in items.values():
        if item.get("store_kind") == "event_log":
            item["status"] = result["status"]
            item["attempt"] = int(item.get("attempt") or 0) + 1
            item["last_error"] = "" if result["status"] == "committed" else str(plan.get("comparison_status") or "indeterminate")
        elif item.get("status") == "pending":
            item["status"] = "deferred"; item["last_error"] = "authority_adapter_pending"
    state["items"] = items; state["last_plan"] = {k: v for k, v in plan.items() if k != "entries"}; state["updated_at"] = time.time()
    if not safe_write_json(_state_path(scope), state, keep_bak=True):
        raise OSError("history_reconciliation_state_write_failed")
    return result


def apply_batch(scope: MemoryScope, *, backup: dict[str, Any], batch_size: int = 10,
                dry_run: bool = True) -> dict[str, Any]:
    """Apply one bounded event-log batch after an explicit verified backup.

    ``dry_run`` is the default and never writes source evidence.  A non-dry
    invocation delegates to the existing atomic event migration adapter; other
    source kinds remain deferred in this first A-C slice.
    """
    if dry_run:
        return apply_dry_run(scope, backup_verified=False)
    state = read_state(scope)
    if state.get("paused"):
        return {"status": "paused", "reason": state.get("pause_reason", "")}
    if not isinstance(backup, dict) or backup.get("verified") is not True:
        return apply_dry_run(scope, backup_verified=False)
    state = read_state(scope)
    manifest = state.get("manifest") if isinstance(state.get("manifest"), dict) else {}
    if str(state.get("frozen_manifest_revision") or "") != str(manifest.get("manifest_revision") or ""):
        return {"status": "deferred", "reason": "manifest_not_frozen"}
    from core.memory import event_migration
    plan = event_migration.scan_legacy(scope)
    if plan.get("indeterminate"):
        return {"status": "deferred", "reason": plan.get("comparison_status", "indeterminate")}
    result = event_migration.apply_batch(
        scope, plan, batch_size=batch_size, backup=backup, skip_conflicts=True,
    )
    if int(result.get("conflict") or 0) and str(result.get("status") or "") == "completed":
        try:
            result["conflict_preservation"] = event_migration.preserve_conflicts(
                scope, plan, backup=backup,
            )
        except (OSError, ValueError, TypeError):
            result["conflict_preservation"] = {"status": "retryable_failed", "preserved": 0}
    state = read_state(scope)
    items = state.get("items") if isinstance(state.get("items"), dict) else {}
    event_items = [item for item in items.values() if item.get("store_kind") == "event_log"]
    if event_items:
        migration_status = str(result.get("status") or "")
        if migration_status in {"completed", "committed"}:
            conflict_count = int(result.get("conflict") or 0)
            next_status = "deferred" if conflict_count else "committed"
            error = "conflict" if conflict_count else ""
        elif migration_status in {"paused", "deferred"}:
            next_status = "deferred"
            error = str(result.get("last_error") or result.get("status") or "deferred")[:128]
        else:
            next_status = "retryable_failed"
            error = str(result.get("last_error") or result.get("status") or "apply_failed")[:128]
        for item in event_items:
            item.update({"status": next_status, "attempt": int(item.get("attempt") or 0) + 1, "last_error": error})
        state["items"] = items
    state["last_apply"] = {"status": result.get("status"), "source_digest": plan.get("source_digest", ""),
                            "updated_at": time.time()}
    # Once the event adapter has passed its conflict gate, reconcile the
    # remaining derived stores in bounded evidence-only receipts.  Their
    # source files are never rewritten and no semantic dossier claim is made.
    if str(result.get("status") or "") in {"committed", "completed", "paused"}:
        derived_results: dict[str, Any] = {}
        for store_kind in ("event_store", "mid_term", "episodic", "storyline", "user_identity"):
            source_item = next((value for value in items.values()
                                if value.get("store_kind") == store_kind), None)
            if not isinstance(source_item, dict) or source_item.get("status") in {"committed", "excluded"}:
                continue
            try:
                derived = _commit_derived_source_batch(
                    scope, store_kind, source_item, backup=backup, batch_size=batch_size,
                )
            except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
                derived = {"status": "retryable_failed", "reason": type(exc).__name__}
            derived_results[store_kind] = derived
            source_item["next_offset"] = int(derived.get("next_offset") or source_item.get("next_offset") or 0)
            source_item["total"] = int(derived.get("total") or source_item.get("total") or 0)
            source_item["input_digest"] = _digest({"store_kind": store_kind,
                                                     "source_revision": source_item.get("source_revision"),
                                                     "next_offset": source_item["next_offset"]})
            source_item["target_revision"] = str(derived.get("target_revision") or "")
            source_item["operation_receipt"] = str(derived.get("operation_id") or "")
            source_item["rule_version"] = RULES_VERSION
            source_item["revisit_condition"] = "source_revision_changed" if derived.get("status") in {"committed", "completed"} else str(derived.get("reason") or "retryable")[:128]
            source_item["attempt"] = int(source_item.get("attempt") or 0) + 1
            if derived.get("status") in {"committed", "completed"} and source_item["next_offset"] >= source_item["total"]:
                source_item["status"] = "committed"
                source_item["last_error"] = ""
            elif derived.get("status") == "committed":
                source_item["status"] = "pending"
                source_item["last_error"] = ""
            elif derived.get("status") == "deferred":
                source_item["status"] = "deferred"
                source_item["last_error"] = str(derived.get("reason") or "deferred")[:128]
            else:
                source_item["status"] = "retryable_failed"
                source_item["last_error"] = str(derived.get("reason") or "apply_failed")[:128]
        if derived_results:
            state["derived"] = derived_results
            state["items"] = items
    safe_write_json(_state_path(scope), state, keep_bak=True)
    return {"status": result.get("status", "retryable_failed"), "migration": result,
            "derived": state.get("derived", {})}


def rollback_batch(scope: MemoryScope, *, source_ids: list[str] | None = None,
                   reason: str = "operator_rollback") -> dict[str, Any]:
    """Reopen derived receipts without deleting source evidence.

    Rollback is deliberately reversible: dossier evidence-only receipts are
    moved back to pending, while source ledgers and legacy files remain intact.
    """
    reason = str(reason or "").strip()[:128]
    if not reason:
        raise ValueError("rollback_reason_required")
    from core.memory import dossiers
    reopened = dossiers.reopen_evidence_only(scope, source_ids=source_ids)
    state = read_state(scope)
    items = state.get("items") if isinstance(state.get("items"), dict) else {}
    for item in items.values():
        if not isinstance(item, dict) or item.get("status") not in {"committed", "deferred"}:
            continue
        if source_ids and item.get("store_kind") not in {"event_store", "event_log"}:
            continue
        item["status"] = "pending"
        item["last_error"] = "rolled_back:" + reason
        item["revisit_condition"] = "operator_reopened"
    state["items"] = items
    state["last_rollback"] = {"reason": reason, "reopened": reopened, "updated_at": time.time()}
    if not safe_write_json(_state_path(scope), state, keep_bak=True):
        raise OSError("history_reconciliation_state_write_failed")
    return {"status": "rolled_back", "reopened": reopened, "reason": reason}


def verify_backup_snapshot(snapshot: Path) -> dict[str, Any]:
    """Verify an offline snapshot and return only safe metadata for apply admission."""
    from core.backup_state import verify_snapshot

    result = verify_snapshot(Path(snapshot))
    return {"verified": bool(result.get("ok")), "errors": result.get("errors", [])}


def _derived_source_items(scope: MemoryScope, store_kind: str, source_revision: str) -> list[dict[str, Any]]:
    """Read a derived store and produce content-free, evidence-only receipts."""
    path = resolve_path(scope, {"event_store": "event_store", "mid_term": "mid_term",
                                "episodic": "episodic", "storyline": "storyline",
                                "user_identity": "identity"}[store_kind])
    values: list[tuple[str, Any]] = []
    if store_kind == "event_store":
        if path.exists():
            with sqlite3.connect(path) as connection:
                values = [(str(row[0]), {"event_id": str(row[0])})
                          for row in connection.execute("SELECT event_id FROM events ORDER BY rowid")]
    elif store_kind == "mid_term":
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            values = [(str(item.get("mid_id") or f"mid:{index}"), item)
                      for index, item in enumerate(raw.get("events", [])) if isinstance(item, dict)]
    elif store_kind == "episodic":
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("episodic_not_list")
            values = [(str(item.get("id") or f"episode:{index}"), item)
                      for index, item in enumerate(raw) if isinstance(item, dict)]
    elif store_kind == "storyline":
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            arcs = raw.get("arcs", []) if isinstance(raw, dict) else []
            for index, arc in enumerate(arcs):
                if not isinstance(arc, dict):
                    continue
                arc_id = str(arc.get("arc_id") or arc.get("id") or f"arc:{index}")
                values.append((arc_id, arc))
                for node_index, node in enumerate(arc.get("nodes", []) or []):
                    if isinstance(node, dict):
                        values.append((str(node.get("node_id") or f"{arc_id}:node:{node_index}"), node))
    elif store_kind == "user_identity":
        if path.exists():
            import yaml
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise ValueError("identity_not_mapping")
            values = [(str(key), value) for key, value in sorted(raw.items())]
    result = []
    for sequence, (source_id, value) in enumerate(values, start=1):
        digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
        result.append({"store_kind": store_kind, "source_id": source_id[:512],
                       "source_revision": source_revision, "ingest_sequence": sequence,
                       "input_digest": digest, "semantic_outcomes": ["evidence_only"]})
    return result


def _commit_derived_source_batch(scope: MemoryScope, store_kind: str, item: dict[str, Any],
                                 *, backup: dict[str, Any], batch_size: int) -> dict[str, Any]:
    if backup.get("verified") is not True:
        return {"status": "deferred", "reason": "backup_not_verified"}
    records = _derived_source_items(scope, store_kind, str(item.get("source_revision") or ""))
    offset = max(0, int(item.get("next_offset") or 0))
    batch = records[offset:offset + max(1, int(batch_size))]
    if not batch:
        return {"status": "completed", "next_offset": offset, "total": len(records), "processed": 0}
    from core.memory import dossiers
    operation_id = hashlib.sha256(
        f"history:{scope.uid}:{scope.character_id}:{store_kind}:{item.get('source_revision')}:{offset}".encode()
    ).hexdigest()[:32]
    dossiers.apply_operations(scope, [], operation_id=operation_id,
                              actor=f"character:{scope.character_id}", chain="maintenance",
                              processing_items=batch)
    next_offset = offset + len(batch)
    return {"status": "committed" if next_offset < len(records) else "completed",
            "next_offset": next_offset, "total": len(records), "processed": len(batch)}


async def consolidate_imported_events(scope: MemoryScope, *, preset: str = "便宜小模型grok-see") -> dict[str, Any]:
    """Run one bounded dossier pass for this imported scope.

    The existing consolidation capability owns model calls, grants, budgets,
    foreground yielding, and atomic dossier commits. This wrapper only pins
    the scope and the requested cheap bulk preset; it never enables the global
    scheduler or sends a conversation message.
    """
    from core.agent_runtime.models import TaskPrincipal
    from core.memory import consolidation_worker

    return await consolidation_worker.tick(
        only_principal=TaskPrincipal.reality(scope.uid, scope.character_id),
        preset_override=preset,
    )


def settle_evidence_only(scope: MemoryScope, *, reason: str, operator: str = "admin") -> dict[str, Any]:
    """Close remaining dossier inputs without making semantic claims.

    This is an explicit operator action for provider outages or calibration
    pauses.  It records ``evidence_only`` processing receipts and leaves the
    source event and dossier tables unchanged.
    """
    reason = str(reason or "").strip()[:256]
    if not reason:
        raise ValueError("evidence_only_reason_required")
    from core.memory import dossiers
    events = dossiers.maintenance_candidates(scope, limit=100, max_chars=24000)
    if not events:
        return {"status": "no_work", "processed": 0, "reason": reason}
    items = [{"store_kind": event["store_kind"], "source_id": event["source_id"],
              "source_revision": event["source_revision"], "ingest_sequence": event["ingest_sequence"],
              "input_digest": event["input_digest"], "semantic_outcomes": ["evidence_only"]}
             for event in events]
    operation_id = hashlib.sha256(
        f"evidence-only:{scope.uid}:{scope.character_id}:{reason}:{items[-1]['ingest_sequence']}".encode()
    ).hexdigest()[:32]
    result = dossiers.apply_operations(
        scope, [], operation_id=operation_id, actor=f"character:{scope.character_id}",
        chain="admin_recovery", processing_items=items,
    )
    return {"status": "committed", "processed": result["processed"],
            "reason": reason, "operator": str(operator)[:128], "operation_id": operation_id}


async def run_first_night(
    scope: MemoryScope,
    *,
    backup_snapshot: Path,
    manifest_revision: str,
    batch_size: int = 10,
    stop_at: float | None = None,
    preset: str = "便宜小模型grok-see",
) -> dict[str, Any]:
    """Run bounded historical import batches until a cutoff or terminal state.

    This is an explicit operator action, never a scheduler default. Every
    batch reuses the verified snapshot and frozen manifest gate. The report is
    metadata-only and deliberately distinguishes imported event evidence from
    dossier passes and deferred source adapters.
    """
    if not 1 <= int(batch_size) <= 100:
        raise ValueError("invalid_batch_size")
    verification = verify_backup_snapshot(Path(backup_snapshot))
    if not verification["verified"]:
        return {"status": "deferred", "reason": "backup_not_verified", "errors": verification["errors"]}
    state = read_state(scope)
    if str(state.get("frozen_manifest_revision") or "") != str(manifest_revision):
        return {"status": "deferred", "reason": "manifest_not_frozen"}
    deadline = float(stop_at) if stop_at is not None else time.time() + 600
    batches = 0
    dossier_passes = 0
    last: dict[str, Any] = {}
    while time.time() < deadline:
        state = read_state(scope)
        if state.get("paused"):
            return {"status": "paused", "reason": state.get("pause_reason", ""), "batches": batches,
                    "dossier_passes": dossier_passes, "last": last}
        result = apply_batch(
            scope, backup={"verified": True, "backup_path": str(backup_snapshot)},
            batch_size=int(batch_size), dry_run=False,
        )
        last = result
        batches += 1
        if result.get("status") not in {"committed", "completed"}:
            migration = result.get("migration") or {}
            if result.get("status") == "paused" and (
                status(scope)["counts"].get("pending", 0) > 0
                or int(migration.get("next_offset", 0)) < int(migration.get("total", 0))
            ):
                continue
            break
        migration = result.get("migration") or {}
        if int(migration.get("next_offset", 0)) >= int(migration.get("total", 0)):
            dossier_result = await consolidate_imported_events(scope, preset=preset)
            dossier_passes += int(dossier_result.get("model_calls") or 0)
            last["dossier_pass"] = dossier_result
            break
    current = status(scope)
    terminal = "completed" if current["counts"].get("pending", 0) == 0 and current["counts"].get("running", 0) == 0 else "stopped"
    closeout = {"status": terminal, "batches": batches, "dossier_passes": dossier_passes,
                "stopped_at": datetime.now(timezone.utc).isoformat(), "ledger": current,
                "last": {key: value for key, value in last.items() if key != "migration"}}
    state = read_state(scope)
    state["last_closeout"] = closeout
    safe_write_json(_state_path(scope), state, keep_bak=True)
    return {**closeout, "last": last}


def status(scope: MemoryScope) -> dict[str, Any]:
    state = read_state(scope); items = state.get("items") or {}
    counts = {name: sum(1 for item in items.values() if item.get("status") == name) for name in STATES}
    return {"schema_version": "memory-reconciliation-status.v1", "paused": bool(state.get("paused")),
            "pause_reason": str(state.get("pause_reason") or ""), "counts": counts,
            "total": sum(counts.values()), "manifest_revision": (state.get("manifest") or {}).get("manifest_revision", ""),
            "frozen_manifest_revision": str(state.get("frozen_manifest_revision") or ""),
            "frozen_at": state.get("frozen_at"),
            "last_error": str(state.get("last_error") or "")[:128],
            "last_closeout": state.get("last_closeout") if isinstance(state.get("last_closeout"), dict) else None}


def create_verified_backup(output: Path) -> dict[str, Any]:
    """Create an offline private-state snapshot using the existing verifier."""
    from core.backup_state import PROTECTION_MODE_PROTECTED_VOLUME, create_snapshot
    result = create_snapshot(Path.cwd(), Path(output), protection_mode=PROTECTION_MODE_PROTECTED_VOLUME)
    return {"verified": bool(result.get("ok")), "backup_id": result.get("backup_id"),
            "backup_path": result.get("backup_path"), "file_count": result.get("file_count", 0)}


def recovery_drill(snapshot: Path, target: Path) -> dict[str, Any]:
    """Verify and restore only into a new, empty target directory."""
    from core.backup_state import restore_snapshot, verify_snapshot
    verified = verify_snapshot(Path(snapshot))
    if not verified.get("ok"):
        return {"ok": False, "stage": "verify", "errors": verified.get("errors", [])}
    restored = restore_snapshot(Path.cwd(), Path(snapshot), Path(target), startup_check=False)
    return {"ok": bool(restored.get("ok")), "stage": "restore", "backup_id": restored.get("backup_id"),
            "target_path": restored.get("target_path")}
