"""Read-only, content-free inventory for Brief 259 history reconciliation.

This module deliberately does not create ledgers, call a model, or mutate any
memory store.  It produces a versioned inventory and stable source revisions so
an independently authorized batch worker can later consume it.
"""
from __future__ import annotations

import hashlib
import json
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
    result = event_migration.apply_batch(scope, plan, batch_size=batch_size, backup=backup)
    state = read_state(scope)
    items = state.get("items") if isinstance(state.get("items"), dict) else {}
    event_items = [item for item in items.values() if item.get("store_kind") == "event_log"]
    if event_items:
        migration_status = str(result.get("status") or "")
        if migration_status in {"completed", "committed"}:
            next_status = "committed"
            error = ""
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
    safe_write_json(_state_path(scope), state, keep_bak=True)
    return {"status": result.get("status", "retryable_failed"), "migration": result}


def verify_backup_snapshot(snapshot: Path) -> dict[str, Any]:
    """Verify an offline snapshot and return only safe metadata for apply admission."""
    from core.backup_state import verify_snapshot

    result = verify_snapshot(Path(snapshot))
    return {"verified": bool(result.get("ok")), "errors": result.get("errors", [])}


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
            break
        migration = result.get("migration") or {}
        if int(migration.get("next_offset", 0)) >= int(migration.get("total", 0)):
            dossier_result = await consolidate_imported_events(scope, preset=preset)
            dossier_passes += int(dossier_result.get("model_calls") or 0)
            last["dossier_pass"] = dossier_result
            break
    current = status(scope)
    terminal = "completed" if current["counts"].get("pending", 0) == 0 and current["counts"].get("running", 0) == 0 else "stopped"
    return {"status": terminal, "batches": batches, "dossier_passes": dossier_passes,
            "stopped_at": datetime.now(timezone.utc).isoformat(), "ledger": current, "last": last}


def status(scope: MemoryScope) -> dict[str, Any]:
    state = read_state(scope); items = state.get("items") or {}
    counts = {name: sum(1 for item in items.values() if item.get("status") == name) for name in STATES}
    return {"schema_version": "memory-reconciliation-status.v1", "paused": bool(state.get("paused")),
            "pause_reason": str(state.get("pause_reason") or ""), "counts": counts,
            "total": sum(counts.values()), "manifest_revision": (state.get("manifest") or {}).get("manifest_revision", ""),
            "frozen_manifest_revision": str(state.get("frozen_manifest_revision") or ""),
            "frozen_at": state.get("frozen_at"),
            "last_error": str(state.get("last_error") or "")[:128]}


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
