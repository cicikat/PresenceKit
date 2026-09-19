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

SCHEMA_VERSION = "memory-history-inventory.v1"
SOURCES = ("event_store", "event_log", "mid_term", "episodic", "storyline", "user_identity")


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
