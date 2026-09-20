"""Content-free inventory and source-item ledger for Brief 259 history reconciliation.

Inventory and dry-run manifests never copy source prose or call a model.
Creating a manifest seeds pending `source_items` in the scoped dossier store
so later authorized batches can resume from stable identities. Source files
remain the authority for bodies.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope
from core.safe_write import safe_write_json

SCHEMA_VERSION = "memory-history-inventory.v2"
SOURCES = ("event_store", "event_log", "mid_term", "episodic", "storyline", "user_identity")
STATES = ("pending", "running", "committed", "retryable_failed", "deferred", "excluded")
RULES_VERSION = "history-reconciliation-rules.v1"
FIRST_NIGHT_WINDOW_SECONDS = 30 * 24 * 3600
CALIBRATION_SCHEMA = "memory-history-calibration.v1"
ADMISSION_SCHEMA = "memory-history-admission.v1"
DEFAULT_COLD_THEME_SHARE = 0.25
HEADROOM_RATE = 0.25
HEADROOM_FAIL = 0.15
HEADROOM_FOREGROUND = 0.20
QUALITY_CLASSES = (
    "duplicate_facts", "feeling_as_fact", "false_discard",
    "classification_fragmentation", "stale_conclusion",
)
RESTORE_STRATEGIES = ("verified_snapshot_rollback",)
PRECONDITION_KEYS = (
    "brief_258_b_e",
    "recovery_drill",
    "spot_check",
)
_DAY_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_OFFSET_RE = re.compile(r"^UTC([+-])(\d{2}):(\d{2})$")
_KNOWN_ZONE_OFFSETS = {
    "UTC": 0,
    "Etc/UTC": 0,
    "Asia/Shanghai": 8 * 3600,
    "Asia/Hong_Kong": 8 * 3600,
    "Asia/Taipei": 8 * 3600,
    "Asia/Tokyo": 9 * 3600,
    "America/New_York": -5 * 3600,
}


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
    event_log = resolve_path(scope, "event_log")
    return {
        "event_store": [resolve_path(scope, "event_store")],
        "event_log": sorted(event_log.glob("*.md")) if event_log.is_dir() else [],
        "mid_term": [resolve_path(scope, "mid_term")],
        "episodic": [resolve_path(scope, "episodic")],
        "storyline": [resolve_path(scope, "storyline"), resolve_path(scope, "storyline_inbox")],
        "user_identity": [resolve_path(scope, "identity")],
        "memory_digest": [resolve_path(scope, "memory_digest")],
        "storyline_archive": [resolve_path(scope, "storyline_archive")],
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "item_count": 0, "legacy_unknown": 0, "lineage_missing": 0, "old_format": 0,
        "time_min": None, "time_max": None, "first_night_candidates": 0,
        "remaining_history": 0, "estimated_tokens": 0, "independent_experiences": "unknown",
        "denominator": "derived_summaries",
    }


def _merge_times(metrics: dict[str, Any], value: float | None) -> None:
    if value is None:
        return
    stamp = float(value)
    current_min = metrics["time_min"]
    current_max = metrics["time_max"]
    metrics["time_min"] = stamp if current_min is None else min(float(current_min), stamp)
    metrics["time_max"] = stamp if current_max is None else max(float(current_max), stamp)


def _window(metrics: dict[str, Any], stamp: float | None, cutoff: float) -> None:
    if stamp is None:
        metrics["remaining_history"] += 1
        return
    if float(stamp) >= cutoff:
        metrics["first_night_candidates"] += 1
    else:
        metrics["remaining_history"] += 1


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def _has_lineage(value: Any) -> bool:
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    return bool(str(value or "").strip())


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _event_store_metrics(path: Path, cutoff: float) -> dict[str, Any]:
    metrics = _empty_metrics()
    metrics["denominator"] = "evidence_rows"
    if not path.exists():
        return metrics
    from core.memory import event_store
    with event_store._lock_for(path), sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25,
    ) as connection:
        row = connection.execute(
            "SELECT COUNT(*),MIN(ingested_at),MAX(ingested_at),"
            "SUM(CASE WHEN ingested_at>=? THEN 1 ELSE 0 END),"
            "SUM(CASE WHEN ingested_at-occurred_at>=86400 THEN 1 ELSE 0 END),"
            "COALESCE(SUM(LENGTH(COALESCE(NULLIF(memory_text,''),visible_text))),0) "
            "FROM events",
            (cutoff,),
        ).fetchone()
    metrics["item_count"] = int(row[0] or 0)
    if row[1] is not None:
        metrics["time_min"] = float(row[1])
        metrics["time_max"] = float(row[2])
    metrics["first_night_candidates"] = int(row[3] or 0)
    metrics["remaining_history"] = metrics["item_count"] - metrics["first_night_candidates"]
    metrics["late_arrivals"] = int(row[4] or 0)
    metrics["estimated_tokens"] = max(0, (int(row[5] or 0) + 3) // 4)
    metrics["independent_experiences"] = "unknown"
    return metrics


def _event_log_metrics(paths: list[Path], cutoff: float) -> dict[str, Any]:
    metrics = _empty_metrics()
    metrics["denominator"] = "archive_files"
    for path in paths:
        info = _file_info(path)
        if not info["exists"] or not info["readable"]:
            continue
        metrics["item_count"] += 1
        metrics["estimated_tokens"] += max(1, (int(info["bytes"]) + 3) // 4)
        match = _DAY_NAME_RE.match(path.name)
        if match:
            day = datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            stamp = day.timestamp()
            _merge_times(metrics, stamp)
            _window(metrics, stamp, cutoff)
            continue
        metrics["old_format"] += 1
        _window(metrics, None, cutoff)
    return metrics


def _mid_term_metrics(path: Path, cutoff: float) -> dict[str, Any]:
    metrics = _empty_metrics()
    metrics["denominator"] = "derived_summaries"
    if not path.exists():
        return metrics
    raw = _load_json(path)
    events = raw.get("events", []) if isinstance(raw, dict) else []
    for item in events:
        if not isinstance(item, dict):
            continue
        metrics["item_count"] += 1
        stamp = item.get("occurred_at") if isinstance(item.get("occurred_at"), (int, float)) else item.get("ts")
        stamp = float(stamp) if isinstance(stamp, (int, float)) else None
        _merge_times(metrics, stamp)
        _window(metrics, stamp, cutoff)
        if not _has_lineage(item.get("source_event_ids")):
            metrics["legacy_unknown"] += 1
            metrics["lineage_missing"] += 1
        metrics["estimated_tokens"] += _estimate_tokens(str(item.get("summary") or ""))
    return metrics


def _episodic_metrics(path: Path, cutoff: float) -> dict[str, Any]:
    metrics = _empty_metrics()
    metrics["denominator"] = "derived_summaries"
    if not path.exists():
        return metrics
    raw = _load_json(path)
    if not isinstance(raw, list):
        raise ValueError("episodic_not_list")
    for item in raw:
        if not isinstance(item, dict):
            continue
        metrics["item_count"] += 1
        stamp = item.get("event_time") if isinstance(item.get("event_time"), (int, float)) else item.get("timestamp")
        stamp = float(stamp) if isinstance(stamp, (int, float)) else None
        _merge_times(metrics, stamp)
        _window(metrics, stamp, cutoff)
        if not _has_lineage(item.get("source_event_ids")) and not _has_lineage(item.get("source_mid_ids")):
            metrics["legacy_unknown"] += 1
            metrics["lineage_missing"] += 1
        if "narrative_summary" not in item and "summary" in item:
            metrics["old_format"] += 1
        text = str(item.get("narrative_summary") or item.get("summary") or "")
        metrics["estimated_tokens"] += _estimate_tokens(text)
    return metrics


def _storyline_metrics(paths: list[Path], cutoff: float) -> dict[str, Any]:
    metrics = _empty_metrics()
    metrics["denominator"] = "derived_summaries"
    for path in paths:
        if not path.exists():
            continue
        raw = _load_json(path)
        if isinstance(raw, dict):
            arcs = raw.get("arcs", [])
            for arc in arcs if isinstance(arcs, list) else []:
                if not isinstance(arc, dict):
                    continue
                metrics["item_count"] += 1
                stamp = arc.get("updated_at") if isinstance(arc.get("updated_at"), (int, float)) else arc.get("created_at")
                stamp = float(stamp) if isinstance(stamp, (int, float)) else None
                _merge_times(metrics, stamp)
                _window(metrics, stamp, cutoff)
                nodes = arc.get("nodes") if isinstance(arc.get("nodes"), list) else []
                lineage = any(_has_lineage(node.get("source_ids") or node.get("source_event_ids"))
                              for node in nodes if isinstance(node, dict))
                if not lineage:
                    metrics["legacy_unknown"] += 1
                    metrics["lineage_missing"] += 1
                metrics["estimated_tokens"] += _estimate_tokens(str(arc.get("title") or ""))
                for node in nodes:
                    if isinstance(node, dict):
                        metrics["estimated_tokens"] += _estimate_tokens(str(node.get("summary") or node.get("text") or ""))
            continue
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                metrics["item_count"] += 1
                stamp = item.get("timestamp") if isinstance(item.get("timestamp"), (int, float)) else item.get("ts")
                stamp = float(stamp) if isinstance(stamp, (int, float)) else None
                _merge_times(metrics, stamp)
                _window(metrics, stamp, cutoff)
                if not _has_lineage(item.get("source_event_ids")):
                    metrics["legacy_unknown"] += 1
                    metrics["lineage_missing"] += 1
                metrics["estimated_tokens"] += _estimate_tokens(str(item.get("summary") or item.get("narrative_summary") or ""))
    return metrics


def _identity_metrics(path: Path, cutoff: float) -> dict[str, Any]:
    metrics = _empty_metrics()
    metrics["denominator"] = "derived_summaries"
    if not path.exists():
        return metrics
    import yaml
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError("identity_unreadable") from exc
    if not isinstance(raw, dict):
        raise ValueError("identity_not_mapping")
    for key, value in raw.items():
        metrics["item_count"] += 1
        stamp = None
        if isinstance(value, dict):
            for field in ("updated_at", "ts", "timestamp"):
                if isinstance(value.get(field), (int, float)):
                    stamp = float(value[field])
                    break
            if not _has_lineage(value.get("source_event_ids")):
                metrics["legacy_unknown"] += 1
                metrics["lineage_missing"] += 1
            metrics["estimated_tokens"] += _estimate_tokens(str(value.get("text") or value.get("summary") or key))
        else:
            metrics["legacy_unknown"] += 1
            metrics["lineage_missing"] += 1
            metrics["estimated_tokens"] += _estimate_tokens(str(value))
        _merge_times(metrics, stamp)
        _window(metrics, stamp, cutoff)
    return metrics


def _archive_metrics(path: Path) -> dict[str, Any]:
    info = _file_info(path)
    return {
        "exists": bool(info["exists"]),
        "bytes": int(info["bytes"]),
        "readable": bool(info["readable"]),
        "source_revision": str(info["revision"]),
        "file_count": 1 if info["exists"] else 0,
        "denominator": "archive_files",
        "isolated": True,
    }


def build_inventory(uid: str, char_id: str, *, now: float | None = None) -> dict[str, Any]:
    """Return a deterministic, redacted source inventory for one scope."""
    if not str(uid).strip() or not str(char_id).strip():
        raise ValueError("uid_and_char_id_required")
    generated_at = float(time.time() if now is None else now)
    cutoff = generated_at - FIRST_NIGHT_WINDOW_SECONDS
    files = _scope_files(str(uid), str(char_id))
    items: list[dict[str, Any]] = []
    denominators = {"evidence_rows": 0, "derived_summaries": 0, "archive_files": 0, "independent_experiences": "unknown"}
    unknown: list[str] = []
    for kind in SOURCES:
        entries = [_file_info(path) for path in files[kind]]
        try:
            if kind == "event_store":
                metrics = _event_store_metrics(files[kind][0], cutoff)
            elif kind == "event_log":
                metrics = _event_log_metrics(files[kind], cutoff)
            elif kind == "mid_term":
                metrics = _mid_term_metrics(files[kind][0], cutoff)
            elif kind == "episodic":
                metrics = _episodic_metrics(files[kind][0], cutoff)
            elif kind == "storyline":
                metrics = _storyline_metrics(files[kind], cutoff)
            else:
                metrics = _identity_metrics(files[kind][0], cutoff)
        except (OSError, ValueError, TypeError, json.JSONDecodeError, sqlite3.Error, UnicodeDecodeError):
            metrics = _empty_metrics()
            metrics["readable"] = False
            unknown.append(kind)
        item = {
            "store_kind": kind,
            "file_count": sum(int(entry["exists"]) for entry in entries),
            "bytes": sum(int(entry["bytes"]) for entry in entries),
            "source_revision": _digest(entries),
            "readable": all(bool(entry.get("readable")) for entry in entries) and metrics.get("readable", True),
            "isolated": True,
            **metrics,
        }
        items.append(item)
        if item["denominator"] in denominators and isinstance(denominators[item["denominator"]], int):
            denominators[item["denominator"]] += int(item["item_count"])
        if int(item["legacy_unknown"]) or int(item["lineage_missing"]):
            unknown.append(f"{kind}:legacy_unknown")
    archives = {
        "memory_digest": _archive_metrics(files["memory_digest"][0]),
        "storyline_archive": _archive_metrics(files["storyline_archive"][0]),
    }
    denominators["archive_files"] += sum(int(item["file_count"]) for item in archives.values())
    first_night = sum(int(item["first_night_candidates"]) for item in items)
    remaining = sum(int(item["remaining_history"]) for item in items)
    times = [item["time_min"] for item in items if item.get("time_min") is not None]
    times_max = [item["time_max"] for item in items if item.get("time_max") is not None]
    cross_year = False
    if times and times_max:
        start = datetime.fromtimestamp(min(float(value) for value in times), tz=timezone.utc)
        end = datetime.fromtimestamp(max(float(value) for value in times_max), tz=timezone.utc)
        cross_year = start.year != end.year
    sample_class_hits = {
        "recent": first_night > 0,
        "stale": remaining > 0,
        "duplicate": "unknown",
        "contradiction": "unknown",
        "missing_lineage": any(int(item["lineage_missing"]) for item in items),
        "cross_year": cross_year,
        "late_arrival": any(int(item.get("late_arrivals") or 0) for item in items),
        "active_theme_chain": any(
            item["store_kind"] == "storyline" and int(item["item_count"]) > 0 for item in items
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {"uid_digest": _digest(str(uid))[:16], "char_id": str(char_id), "realm": "reality"},
        "generated_at": generated_at,
        "read_only": True,
        "model_calls": 0,
        "total_sources": len(items),
        "items": items,
        "denominators": denominators,
        "watermark": {
            "inventory_revision": _digest(items),
            "generated_at": generated_at,
            "first_night_cutoff": cutoff,
            "first_night_window_seconds": FIRST_NIGHT_WINDOW_SECONDS,
        },
        "ranges": {
            "first_night_candidates": first_night,
            "remaining_history": remaining,
            "unknown": sorted(set(unknown)),
        },
        "sample_classes": list(sample_class_hits),
        "sample_class_hits": sample_class_hits,
        "estimated_tokens": sum(int(item["estimated_tokens"]) for item in items),
        "archives": archives,
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


def _event_priority_flags(scope: MemoryScope) -> dict[str, dict[str, bool]]:
    """Return content-free correction / active-theme flags keyed by event ID."""
    path = resolve_path(scope, "event_store")
    flags: dict[str, dict[str, bool]] = {}
    if not path.exists():
        return flags
    from core.memory import dossiers
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute(
            """SELECT event_id,redaction_state,relation_hints_json FROM events
               WHERE uid=? AND char_id=? AND realm=?""",
            (scope.uid, scope.character_id, scope.domain),
        ):
            event_id = str(row["event_id"] or "")
            hints = {}
            try:
                parsed = json.loads(str(row["relation_hints_json"] or "{}"))
                if isinstance(parsed, dict):
                    hints = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                hints = {}
            flags[event_id] = {
                "correction": (
                    str(row["redaction_state"] or "") == "tombstoned"
                    or bool(str(hints.get("correction_of") or "").strip())
                ),
                "active_theme": False,
            }
    event_ids = list(flags)
    related: list[dict[str, Any]] = []
    for offset in range(0, len(event_ids), 100):
        related.extend(dossiers.related_dossiers_for_sources(
            scope,
            [{"store_kind": "event", "source_id": event_id} for event_id in event_ids[offset:offset + 100]],
            limit=50,
        ))
    for item in related:
        if item.get("status") != "active" or int(item.get("needs_recompute") or 0):
            continue
        for source_id in item.get("matching_source_ids") or []:
            current = flags.setdefault(str(source_id), {"correction": False, "active_theme": False})
            current["active_theme"] = True
    return flags


def _priority_for_item(store_kind: str, source_id: str, stamp: float | None, cutoff: float,
                       flags: dict[str, dict[str, bool]]) -> str:
    if store_kind == "storyline":
        if stamp is not None and float(stamp) >= cutoff:
            return "active_theme"
        return "remaining"
    if store_kind == "event":
        current = flags.get(source_id) or {}
        if current.get("correction"):
            return "correction"
        if current.get("active_theme"):
            return "active_theme"
    if stamp is not None and float(stamp) >= cutoff:
        return "recent"
    return "remaining"


def _event_log_source_items(scope: MemoryScope) -> list[dict[str, Any]]:
    """Enumerate event-log files by filename and metadata hash; never store paths or bodies."""
    directory = resolve_path(scope, "event_log")
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for sequence, path in enumerate(sorted(directory.glob("*.md"), key=lambda item: item.name), start=1):
        info = _file_info(path)
        if not info["exists"] or not info["readable"]:
            continue
        match = _DAY_NAME_RE.match(path.name)
        stamp = None
        if match:
            day = datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            stamp = day.timestamp()
        items.append({
            "store_kind": "event_log",
            "source_id": path.name[:512],
            "source_revision": str(info["revision"]),
            "ingest_sequence": sequence,
            "input_digest": str(info["revision"]),
            "rule_version": RULES_VERSION,
            "revisit_condition": "source_revision_changed",
            "occurred_at": stamp,
        })
    return items


def enumerate_source_items(scope: MemoryScope, *, now: float | None = None) -> list[dict[str, Any]]:
    """Return content-free source-item identities for the frozen inventory kinds."""
    generated_at = float(time.time() if now is None else now)
    cutoff = generated_at - FIRST_NIGHT_WINDOW_SECONDS
    inventory = {item["store_kind"]: item for item in build_inventory(scope.uid, scope.character_id, now=generated_at)["items"]}
    records: list[dict[str, Any]] = []
    for store_kind in ("event_store", "mid_term", "episodic", "storyline", "user_identity"):
        revision = str((inventory.get(store_kind) or {}).get("source_revision") or "")
        records.extend(_derived_source_items(scope, store_kind, revision))
    records.extend(_event_log_source_items(scope))
    flags = _event_priority_flags(scope)
    for item in records:
        item.setdefault("rule_version", RULES_VERSION)
        item.setdefault("revisit_condition", "source_revision_changed")
        stamp = item.get("occurred_at")
        if not isinstance(stamp, (int, float)):
            stamp = item.get("ingested_at")
        item["priority_class"] = _priority_for_item(
            str(item["store_kind"]), str(item["source_id"]),
            float(stamp) if isinstance(stamp, (int, float)) else None,
            cutoff, flags,
        )
    records.sort(key=lambda item: (str(item["store_kind"]), int(item["ingest_sequence"]), str(item["source_id"])))
    return records


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
    source_records = enumerate_source_items(scope, now=now)
    from core.memory import dossiers
    seeded = {"inserted": 0, "skipped": 0, "total": 0}
    for offset in range(0, len(source_records), 1000):
        chunk = dossiers.seed_source_items(scope, source_records[offset:offset + 1000], now=now)
        seeded["inserted"] += int(chunk["inserted"])
        seeded["skipped"] += int(chunk["skipped"])
        seeded["total"] += int(chunk["total"])
    source_counts = dossiers.source_item_counts(scope)
    manifest = {"schema_version": "memory-reconciliation-manifest.v1", "inventory": inventory,
                "items": items, "dry_run": True, "created_at": float(time.time() if now is None else now),
                "manifest_revision": _digest(items),
                "source_item_total": int(sum(source_counts.values())),
                "source_items_seeded": seeded}
    state = {"schema_version": "memory-reconciliation-state.v1", "manifest": manifest,
             "items": items, "paused": bool(old.get("paused")), "updated_at": manifest["created_at"],
             "source_item_counts": source_counts}
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
    """Freeze one manifest revision and its first-night priority range."""
    state = read_state(scope)
    manifest = state.get("manifest") if isinstance(state.get("manifest"), dict) else None
    revision = str((manifest or {}).get("manifest_revision") or "")
    if not revision or (manifest_revision and manifest_revision != revision):
        raise ValueError("manifest_revision_mismatch")
    from core.memory import dossiers
    inventory = (manifest or {}).get("inventory") if isinstance((manifest or {}).get("inventory"), dict) else {}
    watermark = inventory.get("watermark") if isinstance(inventory.get("watermark"), dict) else {}
    ranges = inventory.get("ranges") if isinstance(inventory.get("ranges"), dict) else {}
    first_night_range = {
        "priority_order": ["correction", "active_theme", "recent", "remaining"],
        "first_night_cutoff": watermark.get("first_night_cutoff"),
        "first_night_window_seconds": int(
            watermark.get("first_night_window_seconds") or FIRST_NIGHT_WINDOW_SECONDS
        ),
        "inventory_first_night_candidates": int(ranges.get("first_night_candidates") or 0),
        "inventory_remaining_history": int(ranges.get("remaining_history") or 0),
        "source_item_priority_counts": dossiers.source_item_priority_counts(scope),
        "cold_theme_share": DEFAULT_COLD_THEME_SHARE,
        "note": "30-day recent window is the initial suggestion; freeze uses this inventory snapshot.",
    }
    state["frozen_manifest_revision"] = revision
    state["frozen_at"] = time.time()
    state["updated_at"] = state["frozen_at"]
    state["first_night_range"] = first_night_range
    if not safe_write_json(_state_path(scope), state, keep_bak=True):
        raise OSError("history_reconciliation_state_write_failed")
    return {"frozen": True, "manifest_revision": revision, "frozen_at": state["frozen_at"],
            "first_night_range": first_night_range}


def _parse_go_live_date(value: Any) -> str:
    text = str(value or "").strip()
    match = _DATE_RE.fullmatch(text)
    if not match:
        raise ValueError("invalid_go_live_date")
    year, month, day = (int(part) for part in match.groups())
    try:
        datetime(year, month, day)
    except ValueError as exc:
        raise ValueError("invalid_go_live_date") from exc
    return text


def _parse_timezone(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("invalid_timezone")
    try:
        ZoneInfo(name)
        return name
    except (ZoneInfoNotFoundError, ValueError):
        pass
    if name in _KNOWN_ZONE_OFFSETS or _OFFSET_RE.fullmatch(name):
        return name
    raise ValueError("invalid_timezone")


def _timezone_info(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if name in _KNOWN_ZONE_OFFSETS:
            return timezone(timedelta(seconds=_KNOWN_ZONE_OFFSETS[name]))
        match = _OFFSET_RE.fullmatch(name)
        if match:
            sign = 1 if match.group(1) == "+" else -1
            seconds = sign * (int(match.group(2)) * 3600 + int(match.group(3)) * 60)
            return timezone(timedelta(seconds=seconds))
        raise ValueError("invalid_timezone")


def _positive_int(value: Any, *, field: str, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(field) from exc
    if number <= 0 or number > high:
        raise ValueError(field)
    return number


def _preconditions(raw: Any) -> dict[str, bool]:
    payload = raw if isinstance(raw, dict) else {}
    result = {key: bool(payload.get(key)) for key in PRECONDITION_KEYS}
    if not all(result.values()):
        raise ValueError("preconditions_incomplete")
    return result


def _range_totals(frozen_range: dict[str, Any]) -> dict[str, int]:
    counts = frozen_range.get("source_item_priority_counts")
    counts = counts if isinstance(counts, dict) else {}
    return {
        "first_night_candidates": int(frozen_range.get("inventory_first_night_candidates") or 0),
        "remaining_history": int(frozen_range.get("inventory_remaining_history") or 0),
        "correction": int(counts.get("correction") or 0),
        "active_theme": int(counts.get("active_theme") or 0),
        "recent": int(counts.get("recent") or 0),
        "remaining": int(counts.get("remaining") or 0),
    }


def _go_live_deadline(go_live_date: str, timezone_name: str, stop_at_local: str) -> float:
    hour, minute = (int(part) for part in str(stop_at_local).split(":", 1))
    local = datetime.fromisoformat(f"{go_live_date}T{hour:02d}:{minute:02d}:00").replace(
        tzinfo=_timezone_info(timezone_name),
    )
    return local.timestamp()


def admit_first_night(
    scope: MemoryScope,
    *,
    go_live_date: str,
    timezone_name: str,
    manifest_revision: str | None = None,
    grant_revision: int,
    preset: str = "便宜小模型grok-see",
    daily_call_budget: int,
    daily_token_budget: int,
    daily_cost_budget: float,
    stop_at_local: str = "07:00",
    restore_strategy: str = "verified_snapshot_rollback",
    preconditions: dict[str, bool] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Freeze production-admission artifacts. This does not run first-night."""
    date = _parse_go_live_date(go_live_date)
    zone = _parse_timezone(timezone_name)
    stop = str(stop_at_local or "").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", stop):
        raise ValueError("invalid_stop_at_local")
    strategy = str(restore_strategy or "").strip()
    if strategy not in RESTORE_STRATEGIES:
        raise ValueError("invalid_restore_strategy")
    grant = _positive_int(grant_revision, field="invalid_grant_revision", high=1_000_000)
    calls = _positive_int(daily_call_budget, field="invalid_call_budget", high=100)
    tokens = _positive_int(daily_token_budget, field="invalid_token_budget", high=100_000)
    try:
        cost = float(daily_cost_budget)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_cost_budget") from exc
    if cost <= 0 or cost > 10_000:
        raise ValueError("invalid_cost_budget")
    named_preset = str(preset or "").strip()[:128]
    if not named_preset:
        raise ValueError("invalid_preset")
    checks = _preconditions(preconditions)
    state = read_state(scope)
    frozen = str(state.get("frozen_manifest_revision") or "")
    revision = str(manifest_revision or frozen)
    if not frozen or frozen != revision:
        raise ValueError("manifest_not_frozen")
    frozen_range = state.get("first_night_range") if isinstance(state.get("first_night_range"), dict) else {}
    if not frozen_range:
        raise ValueError("first_night_range_missing")
    calibration = state.get("last_calibration") if isinstance(state.get("last_calibration"), dict) else {}
    if calibration.get("unlimited_run_allowed") is not False:
        raise ValueError("calibration_incomplete")
    if calibration.get("budget_unset") is True:
        raise ValueError("budget_unset")
    from core.memory import consolidation_worker
    cfg = consolidation_worker.config()
    if int(cfg["grant_revision"]) != grant:
        raise ValueError("grant_revision_mismatch")
    deadline = _go_live_deadline(date, zone, stop)
    admitted_at = float(time.time() if now is None else now)
    admission = {
        "schema_version": ADMISSION_SCHEMA,
        "admitted": True,
        "production_first_night": False,
        "go_live_date": date,
        "timezone": zone,
        "manifest_revision": frozen,
        "range_totals": _range_totals(frozen_range),
        "grant_revision": grant,
        "preset": named_preset,
        "hard_budgets": {
            "daily_call_budget": calls,
            "daily_token_budget": tokens,
            "daily_cost_budget": round(cost, 4),
        },
        "stop_at_local": stop,
        "stop_at": deadline,
        "restore_strategy": strategy,
        "preconditions": checks,
        "admitted_at": admitted_at,
        "note": "Admission freezes go-live artifacts; it does not enable the scheduler or run first-night.",
    }
    state["admission"] = admission
    state["updated_at"] = admitted_at
    if not safe_write_json(_state_path(scope), state, keep_bak=True):
        raise OSError("history_reconciliation_state_write_failed")
    return admission


def _require_admission(scope: MemoryScope, *, manifest_revision: str) -> dict[str, Any]:
    state = read_state(scope)
    admission = state.get("admission") if isinstance(state.get("admission"), dict) else None
    if not admission or admission.get("admitted") is not True:
        return {"ok": False, "reason": "not_admitted"}
    if str(admission.get("schema_version") or "") != ADMISSION_SCHEMA:
        return {"ok": False, "reason": "admission_schema_mismatch"}
    if str(state.get("frozen_manifest_revision") or "") != str(manifest_revision):
        return {"ok": False, "reason": "manifest_not_frozen"}
    if str(admission.get("manifest_revision") or "") != str(manifest_revision):
        return {"ok": False, "reason": "admission_manifest_mismatch"}
    if admission.get("unlimited_run_allowed") is True:
        return {"ok": False, "reason": "unlimited_run_forbidden"}
    budgets = admission.get("hard_budgets") if isinstance(admission.get("hard_budgets"), dict) else {}
    if not all(float(budgets.get(name) or 0) > 0 for name in ("daily_call_budget", "daily_token_budget", "daily_cost_budget")):
        return {"ok": False, "reason": "budget_unset"}
    if str(admission.get("restore_strategy") or "") not in RESTORE_STRATEGIES:
        return {"ok": False, "reason": "invalid_restore_strategy"}
    checks = admission.get("preconditions") if isinstance(admission.get("preconditions"), dict) else {}
    if not all(bool(checks.get(key)) for key in PRECONDITION_KEYS):
        return {"ok": False, "reason": "preconditions_incomplete"}
    from core.memory import consolidation_worker
    if int(consolidation_worker.config()["grant_revision"]) != int(admission.get("grant_revision") or 0):
        return {"ok": False, "reason": "grant_revision_mismatch"}
    if float(admission.get("stop_at") or 0) <= 0:
        return {"ok": False, "reason": "invalid_stop_at"}
    return {"ok": True, "admission": admission}


def _inspect_quality(operations: list[dict[str, Any]], events: list[dict[str, Any]],
                     related: list[dict[str, Any]]) -> dict[str, Any]:
    """Flag structural quality risks; this is not a semantic score."""
    hits = {name: False for name in QUALITY_CLASSES}
    source_ids = [str(item.get("source_id") or "") for item in events]
    related_sources = {
        str(source_id)
        for item in related
        for source_id in (item.get("matching_source_ids") or [])
    }
    titles: list[str] = []
    cited: list[str] = []
    created_dossiers = 0
    revises = 0
    retires = 0
    for operation in operations:
        action = str(operation.get("action") or "")
        if action == "create_dossier":
            created_dossiers += 1
            titles.append(str(operation.get("title") or "").strip().lower())
        elif action == "create_occurrence":
            evidence_ids = [
                str(ref.get("source_id") or "")
                for ref in (operation.get("evidence") or [])
                if isinstance(ref, dict)
            ]
            cited.extend(evidence_ids)
            if not operation.get("occurrence_key") and set(evidence_ids) & related_sources:
                hits["duplicate_facts"] = True
            if str(operation.get("assertion_kind") or "") == "user_stated" and operation.get("character_feeling"):
                hits["feeling_as_fact"] = True
        elif action == "revise_understanding":
            revises += 1
            if operation.get("character_feeling"):
                hits["feeling_as_fact"] = True
        elif action == "set_dossier_status" and str(operation.get("status") or "") == "retired":
            retires += 1
    if len(titles) != len(set(title for title in titles if title)):
        hits["classification_fragmentation"] = True
    if created_dossiers > 1 or (created_dossiers and related):
        hits["classification_fragmentation"] = True
    if cited:
        from collections import Counter
        if any(count > 1 for count in Counter(cited).values()):
            hits["duplicate_facts"] = True
    needs_recompute = any(int(item.get("needs_recompute") or 0) for item in related)
    if needs_recompute and revises == 0 and retires == 0:
        hits["stale_conclusion"] = True
    correction_sources = {str(item.get("source_id") or "") for item in events if item.get("priority_class") == "correction"}
    if not operations and correction_sources:
        hits["false_discard"] = True
    if operations and not cited and source_ids and created_dossiers == 0 and revises == 0:
        hits["false_discard"] = True
    return {
        "classes": QUALITY_CLASSES,
        "hits": hits,
        "operations": len(operations),
        "empty_patch": not operations,
        "note": "structural flags only; empty patch is evidence_only unless it drops a correction",
    }


def _rate_band(samples: list[dict[str, Any]], *, remaining_tokens: int,
               daily_token_budget: int, daily_call_budget: int,
               daily_wall_seconds: int) -> dict[str, Any]:
    calls = len(samples)
    budget_unset = daily_token_budget <= 0 or daily_call_budget <= 0 or daily_wall_seconds <= 0
    remaining = max(0, int(remaining_tokens))
    headroom = 1.0 / ((1.0 - HEADROOM_RATE) * (1.0 - HEADROOM_FAIL) * (1.0 - HEADROOM_FOREGROUND))
    empty = {
        "sample_calls": 0,
        "mean_wall_seconds": 0.0,
        "mean_input_tokens": 0,
        "mean_output_tokens": 0,
        "retry_rate": 0.0,
        "measured_tokens_per_second": 0.0,
        "admitted_tokens_per_second": 0.0,
        "headroom": {
            "rate_limit": HEADROOM_RATE,
            "failure": HEADROOM_FAIL,
            "foreground": HEADROOM_FOREGROUND,
            "combined": round(headroom, 4),
        },
        "remaining_tokens": remaining,
        "estimated_seconds": None,
        "daily_token_budget": int(daily_token_budget),
        "daily_call_budget": int(daily_call_budget),
        "daily_wall_seconds": int(daily_wall_seconds),
        "budget_unset": budget_unset,
        "unlimited_run_allowed": False,
        "binding_limit": "no_sample" if not calls else ("budget_unset" if budget_unset else "admitted_rate"),
        "notes": "Expected hours use measured tokens/latency plus quota, failure and foreground headroom; record counts are not a rate.",
    }
    if not calls:
        return empty
    latencies = [float(item.get("wall_seconds") or 0) for item in samples]
    input_tokens = [int(item.get("input_tokens") or 0) for item in samples]
    output_tokens = [int(item.get("output_tokens") or 0) for item in samples]
    retries = sum(int(item.get("retries") or 0) for item in samples)
    mean_latency = sum(latencies) / calls
    mean_input = sum(input_tokens) / calls
    mean_output = sum(output_tokens) / calls
    retry_rate = retries / max(1, calls + retries)
    tokens_per_call = max(1.0, mean_input + mean_output)
    seconds_per_call = max(0.001, mean_latency) * (1.0 + retry_rate)
    measured_tokens_per_second = tokens_per_call / seconds_per_call
    admitted_tokens_per_second = measured_tokens_per_second / headroom
    estimated_seconds = remaining / admitted_tokens_per_second if admitted_tokens_per_second else None
    empty.update({
        "sample_calls": calls,
        "mean_wall_seconds": round(mean_latency, 4),
        "mean_input_tokens": int(round(mean_input)),
        "mean_output_tokens": int(round(mean_output)),
        "retry_rate": round(retry_rate, 4),
        "measured_tokens_per_second": round(measured_tokens_per_second, 4),
        "admitted_tokens_per_second": round(admitted_tokens_per_second, 4),
        "estimated_seconds": None if estimated_seconds is None else round(estimated_seconds, 2),
        "binding_limit": "budget_unset" if budget_unset else "admitted_rate",
    })
    return empty


async def calibrate_side_chain(
    scope: MemoryScope,
    *,
    sample_size: int = 3,
    preset: str = "便宜小模型grok-see",
    chat=None,
    now: float | None = None,
) -> dict[str, Any]:
    """Run an isolated same-character side-chain sample; never a production first-night."""
    sample_size = min(8, max(1, int(sample_size)))
    generated_at = float(time.time() if now is None else now)
    inventory = build_inventory(scope.uid, scope.character_id, now=generated_at)
    from core.memory import consolidation_worker, dossiers
    cfg = consolidation_worker.config()
    events = dossiers.maintenance_candidates(scope, limit=sample_size, max_chars=int(cfg["max_input_chars"]))
    related = dossiers.related_dossiers_for_sources(scope, events) if events else []
    identity, identity_revision = consolidation_worker._identity_context(scope.character_id)
    current = [{**item, "summary": str(item.get("summary") or "")[:320]}
               for item in dossiers.search(scope, "", limit=20)]
    flags = _event_priority_flags(scope)
    for event in events:
        event["priority_class"] = _priority_for_item(
            "event", str(event["source_id"]), event.get("ingested_at") or event.get("occurred_at"),
            generated_at - FIRST_NIGHT_WINDOW_SECONDS, flags,
        )
    samples: list[dict[str, Any]] = []
    quality = _inspect_quality([], events, related)
    if events:
        prompt = consolidation_worker._prompt(identity, current, events, related)
        input_tokens = _estimate_tokens(prompt)
        raw = ""
        retries = 0
        operations: list[dict[str, Any]] = []
        error = ""
        started = time.monotonic()
        caller = chat
        if caller is None:
            from core import llm_client
            async def caller(messages, **kwargs):
                return await llm_client.chat(
                    messages, max_tokens_override=int(cfg["max_tokens_per_call"]),
                    call_category="consolidation", char_id=scope.character_id,
                    preset_name=preset,
                )
        for attempt in range(2):
            try:
                raw = await caller([{"role": "system", "content": prompt}])
                operations = consolidation_worker._parse(raw)
                error = ""
                break
            except Exception as exc:
                retries += 1
                error = type(exc).__name__[:64]
                raw = ""
                operations = []
        wall_seconds = time.monotonic() - started
        output_tokens = _estimate_tokens(str(raw or ""))
        quality = _inspect_quality(operations, events, related)
        samples.append({
            "wall_seconds": round(wall_seconds, 4),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "retries": retries,
            "error": error,
            "operations": len(operations),
            "applied": False,
        })
    rate = _rate_band(
        samples,
        remaining_tokens=int(inventory.get("estimated_tokens") or 0),
        daily_token_budget=int(cfg["daily_token_budget"]),
        daily_call_budget=int(cfg["daily_call_budget"]),
        daily_wall_seconds=int(cfg["daily_wall_seconds"]),
    )
    freeze = None
    state = read_state(scope)
    if not isinstance((state.get("manifest") or {}), dict) or not (state.get("manifest") or {}).get("manifest_revision"):
        create_manifest(scope, now=generated_at)
        state = read_state(scope)
    try:
        freeze = freeze_manifest(scope, manifest_revision=str((state.get("manifest") or {}).get("manifest_revision") or "") or None)
    except ValueError:
        freeze = {"frozen": False, "reason": "manifest_revision_mismatch"}
    report = {
        "schema_version": CALIBRATION_SCHEMA,
        "isolated": True,
        "production_first_night": False,
        "preset": str(preset)[:128],
        "identity_revision": identity_revision,
        "sample_size": len(events),
        "model_calls": 1 if events else 0,
        "samples": samples,
        "quality": quality,
        "rate_band": rate,
        "first_night_range": None if freeze is None else freeze.get("first_night_range"),
        "frozen": bool((freeze or {}).get("frozen")),
        "inventory_revision": inventory.get("inventory_revision"),
        "note": "Calibration records redacted latency/token/retry facts only; it does not admit production first-night.",
    }
    state = read_state(scope)
    state["last_calibration"] = {
        "schema_version": CALIBRATION_SCHEMA,
        "isolated": True,
        "production_first_night": False,
        "preset": report["preset"],
        "sample_size": report["sample_size"],
        "model_calls": report["model_calls"],
        "mean_wall_seconds": rate["mean_wall_seconds"],
        "mean_input_tokens": rate["mean_input_tokens"],
        "mean_output_tokens": rate["mean_output_tokens"],
        "retry_rate": rate["retry_rate"],
        "admitted_tokens_per_second": rate["admitted_tokens_per_second"],
        "budget_unset": rate["budget_unset"],
        "unlimited_run_allowed": False,
        "quality_hits": quality["hits"],
        "frozen": report["frozen"],
        "first_night_cutoff": None if freeze is None else (freeze.get("first_night_range") or {}).get("first_night_cutoff"),
        "updated_at": generated_at,
    }
    if not safe_write_json(_state_path(scope), state, keep_bak=True):
        raise OSError("history_reconciliation_state_write_failed")
    return report


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
    """Read a derived store and produce content-free, evidence-only receipts.

    ``source_revision`` on each item is the item digest, not the store-file
    watermark. A later store rewrite therefore only opens pending rows for
    identities whose content hash actually changed.
    """
    path = resolve_path(scope, {"event_store": "event_store", "mid_term": "mid_term",
                                "episodic": "episodic", "storyline": "storyline",
                                "user_identity": "identity"}[store_kind])
    values: list[tuple[str, Any]] = []
    item_store_kind = "event" if store_kind == "event_store" else store_kind
    if store_kind == "event_store":
        if path.exists():
            with sqlite3.connect(path) as connection:
                connection.row_factory = sqlite3.Row
                event_rows = connection.execute(
                    """SELECT rowid AS ingest_sequence,event_id,ingested_at,occurred_at,redaction_state,
                              COALESCE(NULLIF(memory_text,''),visible_text) AS text
                       FROM events ORDER BY rowid"""
                ).fetchall()
            result = []
            store_watermark = str(source_revision or "")
            for row in event_rows:
                text = str(row["text"] or "")[:1000]
                payload = {"event_id": row["event_id"], "ingested_at": row["ingested_at"],
                           "redaction_state": str(row["redaction_state"] or ""), "text": text}
                revision = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                                     separators=(",", ":")).encode("utf-8")).hexdigest()
                result.append({"store_kind": item_store_kind, "source_id": str(row["event_id"])[:512],
                               "source_revision": revision, "ingest_sequence": int(row["ingest_sequence"]),
                               "input_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                               "store_watermark": store_watermark, "semantic_outcomes": ["evidence_only"],
                               "ingested_at": float(row["ingested_at"] or 0),
                               "occurred_at": float(row["occurred_at"] or 0)})
            return result
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
    store_watermark = str(source_revision or "")
    for sequence, (source_id, value) in enumerate(values, start=1):
        digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
        stamp = None
        if isinstance(value, dict):
            for field in ("occurred_at", "event_time", "updated_at", "created_at", "timestamp", "ts"):
                if isinstance(value.get(field), (int, float)):
                    stamp = float(value[field])
                    break
        result.append({"store_kind": item_store_kind, "source_id": source_id[:512],
                       "source_revision": digest, "ingest_sequence": sequence,
                       "input_digest": digest, "store_watermark": store_watermark,
                       "semantic_outcomes": ["evidence_only"], "occurred_at": stamp})
    return result


def claim_batch(scope: MemoryScope, *, batch_size: int = 10, store_kind: str = "",
                task_id: str = "", now: float | None = None,
                cold_theme_share: float | None = None) -> dict[str, Any]:
    """Claim a bounded history batch and inspect already-linked dossiers.

    Titles, aliases and membership lists are not used as idempotency keys.
    The lookup only returns dossier IDs, revisions and matching source IDs.
    """
    from core.memory import dossiers
    share = DEFAULT_COLD_THEME_SHARE if cold_theme_share is None else float(cold_theme_share)
    state = read_state(scope)
    frozen_range = state.get("first_night_range") if isinstance(state.get("first_night_range"), dict) else {}
    if cold_theme_share is None and isinstance(frozen_range.get("cold_theme_share"), (int, float)):
        share = float(frozen_range["cold_theme_share"])
    claimed = dossiers.claim_source_items(
        scope, limit=max(1, int(batch_size)), store_kind=store_kind, task_id=task_id, now=now,
        cold_theme_share=share,
    )
    related = dossiers.related_dossiers_for_sources(scope, claimed["items"])
    return {**claimed, "related_dossiers": related}


def _item_store_kind(store_kind: str) -> str:
    return "event" if store_kind == "event_store" else store_kind


def _commit_derived_source_batch(scope: MemoryScope, store_kind: str, item: dict[str, Any],
                                 *, backup: dict[str, Any], batch_size: int) -> dict[str, Any]:
    if backup.get("verified") is not True:
        return {"status": "deferred", "reason": "backup_not_verified"}
    from core.memory import dossiers
    item_kind = _item_store_kind(store_kind)
    claimed = claim_batch(scope, batch_size=max(1, int(batch_size)), store_kind=item_kind)
    batch = claimed["items"]
    related = claimed["related_dossiers"]
    if not batch:
        return {"status": "completed", "next_offset": int(item.get("next_offset") or 0),
                "total": int(item.get("total") or 0), "processed": 0,
                "related_dossiers": related, "reconciled": claimed["reconciled"]}
    processing_items = []
    for row in batch:
        outcomes = row.get("semantic_outcomes") or ["evidence_only"]
        processing_items.append({
            "store_kind": row["store_kind"], "source_id": row["source_id"],
            "source_revision": row["source_revision"], "ingest_sequence": row["ingest_sequence"],
            "input_digest": row["input_digest"], "semantic_outcomes": outcomes,
        })
    operation_id = hashlib.sha256(
        (
            f"history:{scope.uid}:{scope.character_id}:{item_kind}:"
            + ":".join(f"{row['source_id']}:{row['source_revision']}" for row in batch)
        ).encode("utf-8")
    ).hexdigest()[:32]
    try:
        result = dossiers.apply_operations(
            scope, [], operation_id=operation_id,
            actor=f"character:{scope.character_id}", chain="maintenance",
            processing_items=processing_items,
        )
    except (OSError, ValueError, TypeError, sqlite3.Error, dossiers.DossierError) as exc:
        dossiers.fail_source_item_claim(scope, batch, error=type(exc).__name__[:128])
        return {"status": "retryable_failed", "reason": type(exc).__name__,
                "processed": 0, "related_dossiers": related,
                "reconciled": claimed["reconciled"]}
    leftover = 0
    for status in ("pending", "retryable_failed", "running"):
        leftover += int(dossiers.list_source_items(
            scope, store_kind=item_kind, status=status, limit=1,
        )["total"])
    processed = int(result.get("processed") or len(batch))
    next_offset = int(item.get("next_offset") or 0) + processed
    total = next_offset + leftover
    return {"status": "completed" if leftover == 0 else "committed",
            "next_offset": next_offset, "total": total, "processed": processed,
            "operation_id": operation_id, "related_dossiers": related,
            "reconciled": claimed["reconciled"]}


async def consolidate_imported_events(
    scope: MemoryScope,
    *,
    preset: str = "便宜小模型grok-see",
    stop_at: float | None = None,
) -> dict[str, Any]:
    """Run one bounded dossier pass for this imported scope.

    The existing consolidation capability owns model calls, grants, budgets,
    foreground yielding, and atomic dossier commits. This explicit operator
    wrapper never enables the global scheduler or sends a conversation
    message; it bypasses only the night-window/enabled gate.
    """
    from core.agent_runtime.models import TaskPrincipal
    from core.memory import consolidation_worker

    return await consolidation_worker.run_operator_pass(
        TaskPrincipal.reality(scope.uid, scope.character_id),
        preset_override=preset,
        stop_at=stop_at,
    )


def _batch_conservation(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare store-level and source-item counts without treating outcomes as completion."""
    errors: list[str] = []
    for label, key in (("store", "counts"), ("source_item", "source_item_counts")):
        previous = before.get(key) if isinstance(before.get(key), dict) else {}
        current = after.get(key) if isinstance(after.get(key), dict) else {}
        before_total = sum(int(previous.get(name) or 0) for name in STATES)
        after_total = sum(int(current.get(name) or 0) for name in STATES)
        if after_total < before_total:
            errors.append(f"{label}_total_regressed")
        if int(current.get("committed") or 0) < int(previous.get("committed") or 0):
            errors.append(f"{label}_committed_regressed")
    frozen = str(before.get("frozen_manifest_revision") or "")
    if frozen and frozen != str(after.get("frozen_manifest_revision") or ""):
        errors.append("manifest_revision_changed")
    return {"ok": not errors, "errors": errors}


def _closeout_report(scope: MemoryScope, *, batches: int, dossier_passes: int,
                     last: dict[str, Any], reason: str = "") -> dict[str, Any]:
    current = status(scope)
    from core.memory import dossiers
    source_counts = current.get("source_item_counts") if isinstance(current.get("source_item_counts"), dict) else {}
    executable = int(source_counts.get("pending") or 0) + int(source_counts.get("retryable_failed") or 0) + int(source_counts.get("running") or 0)
    outcomes = dossiers.source_item_outcome_counts(scope)
    understood = int(source_counts.get("committed") or 0)
    deferred = int(source_counts.get("deferred") or 0)
    excluded = int(source_counts.get("excluded") or 0)
    maintenance = dossiers.maintenance_status(scope)
    closeout = {
        "status": "stopped",
        "reason": str(reason or "")[:128],
        "batches": batches,
        "dossier_passes": dossier_passes,
        "stopped_at": datetime.now(timezone.utc).isoformat(),
        "processed": {
            "committed": int(source_counts.get("committed") or 0),
            "retryable_failed": int(source_counts.get("retryable_failed") or 0),
            "deferred": deferred,
            "excluded": excluded,
            "pending": int(source_counts.get("pending") or 0),
            "running": int(source_counts.get("running") or 0),
            "unprocessed": executable,
        },
        "semantic_outcomes": outcomes,
        "understood_complete": False,
        "note": "excluded/deferred are not treated as understood; pending/failed remain unprocessed.",
        "dossiers": {
            "coverage_ingest_sequence": maintenance.get("coverage_ingest_sequence"),
            "backlog": maintenance.get("backlog"),
            "needs_recompute": int((dossiers.status_snapshot(scope).get("needs_recompute") or 0)),
        },
        "denominator": {
            "source_item_total": current.get("source_item_total"),
            "frozen_manifest_revision": current.get("frozen_manifest_revision"),
            "range_totals": ((current.get("admission") or {}) if isinstance(current.get("admission"), dict) else {}).get("range_totals"),
        },
        "ledger": current,
        "last": {key: value for key, value in last.items() if key != "migration"},
        "conversation_messages": 0,
        "scheduler_enabled": False,
    }
    finished_cleanly = reason in {"no_work", "completed", "succeeded"} or (
        reason == "morning_cutoff" and (batches > 0 or dossier_passes > 0)
    )
    closeout["status"] = "completed" if executable == 0 and finished_cleanly else "stopped"
    closeout["understood_complete"] = (
        executable == 0 and deferred == 0 and excluded == 0 and understood > 0
        and finished_cleanly
    )
    return closeout


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
    batch reuses the verified snapshot, frozen manifest, and production
    admission gates. The report is metadata-only and deliberately distinguishes
    imported event evidence from dossier passes and deferred source adapters.
    Admission itself never enables the scheduler or sends a conversation
    message.
    """
    if not 1 <= int(batch_size) <= 100:
        raise ValueError("invalid_batch_size")
    verification = verify_backup_snapshot(Path(backup_snapshot))
    if not verification["verified"]:
        return {"status": "deferred", "reason": "backup_not_verified", "errors": verification["errors"]}
    gate = _require_admission(scope, manifest_revision=str(manifest_revision))
    if not gate["ok"]:
        return {"status": "deferred", "reason": gate["reason"]}
    admission = gate["admission"]
    admitted_stop = float(admission["stop_at"])
    requested = float(stop_at) if stop_at is not None else admitted_stop
    deadline = min(requested, admitted_stop)
    stamp = time.time()
    if stamp >= admitted_stop:
        return {"status": "deferred", "reason": "stop_deadline_passed"}
    preset = str(admission.get("preset") or preset)[:128]
    batches = 0
    dossier_passes = 0
    last: dict[str, Any] = {}
    stop_reason = "morning_cutoff"
    while time.time() < deadline:
        before = status(scope)
        if before.get("paused"):
            stop_reason = str(before.get("pause_reason") or "paused")
            break
        leftover = (
            int(before.get("source_item_executable") or 0)
            + int((before.get("source_item_counts") or {}).get("running") or 0)
            + int(before.get("executable") or 0)
        )
        if leftover > 0:
            result = apply_batch(
                scope, backup={"verified": True, "backup_path": str(backup_snapshot)},
                batch_size=int(batch_size), dry_run=False,
            )
            last = result
            batches += 1
            after = status(scope)
            conservation = _batch_conservation(before, after)
            last["conservation"] = conservation
            if not conservation["ok"]:
                stop_reason = conservation["errors"][0]
                break
            if result.get("status") not in {"committed", "completed"}:
                stop_reason = str(result.get("reason") or result.get("status") or "batch_stopped")
                break
            continue
        if time.time() >= deadline:
            stop_reason = "morning_cutoff"
            break
        dossier_result = await consolidate_imported_events(scope, preset=preset, stop_at=deadline)
        last["dossier_pass"] = dossier_result
        dossier_passes += 1
        if int(dossier_result.get("model_calls") or 0) <= 0:
            stop_reason = str(dossier_result.get("status") or "no_work")
            break
        if str(dossier_result.get("status") or "") in {"paused", "stopped", "backoff", "budget_exhausted", "busy", "foreground_active", "outcome_unknown"}:
            stop_reason = str(dossier_result.get("status") or "dossier_pass")
            break
    closeout = _closeout_report(
        scope, batches=batches, dossier_passes=dossier_passes, last=last, reason=stop_reason,
    )
    state = read_state(scope)
    state["last_closeout"] = closeout
    safe_write_json(_state_path(scope), state, keep_bak=True)
    return {**closeout, "last": last}


def status(scope: MemoryScope) -> dict[str, Any]:
    state = read_state(scope); items = state.get("items") or {}
    counts = {name: sum(1 for item in items.values() if item.get("status") == name) for name in STATES}
    total = sum(counts.values())
    ratios = {name: (counts[name] / total if total else 0.0) for name in STATES}
    from core.memory import dossiers
    source_counts = dossiers.source_item_counts(scope)
    source_total = sum(source_counts.values())
    source_ratios = {name: (source_counts[name] / source_total if source_total else 0.0) for name in STATES}
    return {"schema_version": "memory-reconciliation-status.v1", "paused": bool(state.get("paused")),
            "pause_reason": str(state.get("pause_reason") or ""), "counts": counts,
            "ratios": ratios, "executable": counts["pending"] + counts["retryable_failed"],
            "incremental_pending": counts["pending"], "total": total,
            "source_item_counts": source_counts, "source_item_total": source_total,
            "source_item_ratios": source_ratios,
            "source_item_executable": source_counts["pending"] + source_counts["retryable_failed"],
            "manifest_revision": (state.get("manifest") or {}).get("manifest_revision", ""),
            "frozen_manifest_revision": str(state.get("frozen_manifest_revision") or ""),
            "frozen_at": state.get("frozen_at"),
            "first_night_range": state.get("first_night_range") if isinstance(state.get("first_night_range"), dict) else None,
            "source_item_priority_counts": dossiers.source_item_priority_counts(scope),
            "last_calibration": state.get("last_calibration") if isinstance(state.get("last_calibration"), dict) else None,
            "admission": state.get("admission") if isinstance(state.get("admission"), dict) else None,
            "source_item_outcomes": dossiers.source_item_outcome_counts(scope),
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
