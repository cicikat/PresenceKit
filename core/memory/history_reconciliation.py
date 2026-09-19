"""Read-only, content-free inventory for Brief 259 history reconciliation.

This module deliberately does not create ledgers, call a model, or mutate any
memory store.  It produces a versioned inventory and stable source revisions so
an independently authorized batch worker can later consume it.
"""
from __future__ import annotations

import hashlib
import json
import time
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
    if not path.exists() or not path.is_file():
        return {"exists": False, "bytes": 0, "revision": "missing"}
    stat = path.stat()
    # Hash metadata only. Inventory never reads or emits private content.
    revision = _digest({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return {"exists": True, "bytes": int(stat.st_size), "revision": revision}


def _scope_files(uid: str, char_id: str) -> dict[str, list[Path]]:
    root = get_paths().memory_char_root(char_id=char_id) / str(uid)
    return {
        "event_store": [root / "event_store.sqlite3"],
        "event_log": sorted(root.glob("event_log*.jsonl")),
        "mid_term": [root / "mid_term.json"],
        "episodic": [root / "episodic.json"],
        "storyline": [root / "storyline.json", root / "storyline_inbox.json"],
        "user_identity": [root / "user_identity.json"],
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
            "readable": all(item["revision"] != "missing" or not item["exists"] for item in entries),
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
    from core.memory import event_migration
    plan = event_migration.scan_legacy(scope)
    if plan.get("indeterminate"):
        return {"status": "deferred", "reason": plan.get("comparison_status", "indeterminate")}
    result = event_migration.apply_batch(scope, plan, batch_size=batch_size, backup=backup)
    state = read_state(scope)
    state["last_apply"] = {"status": result.get("status"), "source_digest": plan.get("source_digest", ""),
                            "updated_at": time.time()}
    safe_write_json(_state_path(scope), state, keep_bak=True)
    return {"status": result.get("status", "retryable_failed"), "migration": result}


def status(scope: MemoryScope) -> dict[str, Any]:
    state = read_state(scope); items = state.get("items") or {}
    counts = {name: sum(1 for item in items.values() if item.get("status") == name) for name in STATES}
    return {"schema_version": "memory-reconciliation-status.v1", "paused": bool(state.get("paused")),
            "pause_reason": str(state.get("pause_reason") or ""), "counts": counts,
            "total": sum(counts.values()), "manifest_revision": (state.get("manifest") or {}).get("manifest_revision", ""),
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
