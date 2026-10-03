"""Evidence withdrawal cascade (S4b).

When a root event is tombstoned, conclusions derived from it must stop being
presented as fact.  Invalidation is reversible marking, never physical
deletion, and the ledger records IDs and counts only (no memory text).

Stores with event-level lineage are handled automatically (dossier, mid_term,
episodic, storyline).  Stores without lineage are reported as ``manual_review``
and never fuzzy-matched.  Event-store downstream edges are reported only.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from core.memory.scope import MemoryScope

logger = logging.getLogger(__name__)

EPISODIC_PARTIAL_STRENGTH_CAP = 0.3
MANUAL_REVIEW_STORES = ("user_identity", "important_facts", "user_facts", "relationship_facts")
MANUAL_REVIEW_NOTE = "该存储无事件级 lineage，无法自动定位，需人工复核"


def _ids(event_ids: Any) -> list[str]:
    from core.memory.lineage import normalize_source_event_ids
    return normalize_source_event_ids(list(event_ids or []))


def _ledger_path(scope: MemoryScope):
    from core.memory.path_resolver import resolve_path
    return resolve_path(scope, "memory_invalidations")


def _provenance(scope: MemoryScope, artifact: str, field: str, reason: str, event_ids: list[str]) -> None:
    try:
        from core.memory import provenance_log
        provenance_log.append(
            scope.uid, scope.character_id or "", artifact=artifact, field=field,
            trigger_signal=f"invalidation:{reason}", source_event_ids=event_ids,
            origin={"source": "invalidation"},
        )
    except Exception:
        pass


def _invalidate_mid_term(scope: MemoryScope, ids: set[str], reason: str, now: float) -> list[str]:
    from core.memory import mid_term
    from core.safe_write import safe_write_json
    path = mid_term._read_file(scope.uid, char_id=scope.character_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    events = data.get("events", [])
    changed: list[str] = []
    for entry in events:
        hit = sorted(ids & set(entry.get("source_event_ids") or []))
        if hit and not entry.get("invalidated"):
            entry["invalidated"] = {"reason": reason, "at": now, "event_ids": hit}
            changed.append(str(entry.get("mid_id", "")))
    if changed:
        safe_write_json(path, {"events": events})
    return changed


def _invalidate_episodic(scope: MemoryScope, ids: set[str], reason: str, now: float) -> dict[str, Any]:
    from core.memory import episodic_memory as ep
    out: dict[str, Any] = {"full": [], "partial": [], "unlinked": 0, "undo": []}
    try:
        memories = ep._load_memories(scope.uid, char_id=scope.character_id)
    except Exception:
        return out
    dirty = False
    for mem in memories:
        sources = set(mem.get("source_event_ids") or [])
        if not sources:
            out["unlinked"] += 1
            continue
        hit = ids & sources
        if not hit:
            continue
        prior_invalid = set(mem.get("invalid_source_event_ids") or [])
        if hit <= prior_invalid:
            continue  # nothing new: idempotent
        merged = prior_invalid | hit
        undo = {
            "id": mem.get("id"), "status": mem.get("status", "open"),
            "strength": mem.get("strength"), "has_invalid": "invalid_source_event_ids" in mem,
            "invalid_source_event_ids": sorted(prior_invalid),
            "invalidated_at_present": "invalidated_at" in mem,
        }
        mem["invalid_source_event_ids"] = sorted(merged)
        if merged >= sources:
            mem["status"] = "invalidated"
            mem["invalidated_at"] = now
            mem["invalidated_reason"] = reason
            out["full"].append(mem.get("id"))
            _delete_vector(scope, str(mem.get("id")))
        else:
            mem["strength"] = min(float(mem.get("strength", 0.5)), EPISODIC_PARTIAL_STRENGTH_CAP)
            out["partial"].append(mem.get("id"))
        out["undo"].append(undo)
        dirty = True
    if dirty:
        ep._save_memories(scope.uid, memories, char_id=scope.character_id)
        ep._rebuild_index(scope.uid, memories, char_id=scope.character_id)
    return out


def _delete_vector(scope: MemoryScope, ep_id: str) -> None:
    try:
        from core.memory import vector_store as _vs
        from core.sandbox import safe_user_id as _safe_uid
        _vs.delete(_safe_uid(scope.uid), scope.character_id, "episodic", ep_id)
    except Exception as exc:
        logger.warning("[invalidation] vector delete failed ep_id=%s: %s", ep_id, exc)


def _invalidate_storyline(scope: MemoryScope, ids: set[str], reason: str, now: float) -> dict[str, Any]:
    from core.memory import storyline
    out: dict[str, Any] = {"nodes": [], "arcs_unrecallable": []}
    try:
        data = storyline.load(scope.uid, char_id=scope.character_id)
    except Exception:
        return out
    dirty = False
    for arc in data.get("arcs", []):
        for node in arc.get("nodes", []):
            hit = sorted(ids & set(node.get("source_ids") or []))
            if hit and not node.get("invalidated"):
                node["invalidated"] = {"reason": reason, "at": now, "event_ids": hit}
                out["nodes"].append([arc.get("arc_id"), node.get("node_id")])
                dirty = True
        nodes = arc.get("nodes", [])
        if nodes and all(n.get("invalidated") for n in nodes) and any(
            arc.get("arc_id") == a for a, _ in out["nodes"]
        ):
            out["arcs_unrecallable"].append(arc.get("arc_id"))
    if dirty:
        storyline._save(scope.uid, data, char_id=scope.character_id)
    return out


def _downstream_events(scope: MemoryScope, ids: list[str]) -> list[str]:
    """Events that were derived from / corrected from the withdrawn ones (report only)."""
    try:
        from core.memory import event_store
        path = event_store._path(scope)
        if not path.exists() or not ids:
            return []
        marks = ",".join("?" for _ in ids)
        with event_store._lock_for(path):
            with event_store._connect(path) as conn:
                rows = conn.execute(
                    f"SELECT DISTINCT from_event_id FROM event_edges WHERE uid=? AND char_id=? "
                    f"AND relation_type IN ('derived_from','correction_of') AND to_event_id IN ({marks})",
                    (scope.uid, scope.character_id, *ids),
                ).fetchall()
        return sorted({str(r[0]) for r in rows} - set(ids))
    except (sqlite3.Error, Exception):
        return []


def _append_ledger(scope: MemoryScope, record: dict[str, Any]) -> None:
    path = _ledger_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def invalidate_by_events(scope: MemoryScope, event_ids: list[str], *, reason: str, actor: str) -> dict[str, Any]:
    """Mark derived conclusions that rest on ``event_ids`` as invalid. Fail-open per store."""
    ids = _ids(event_ids)
    summary: dict[str, Any] = {
        "dossiers": 0, "mid_term": 0, "episodic": 0, "storyline_nodes": 0,
        "storyline_arcs_unrecallable": 0, "partial": 0, "unlinked": 0,
        "downstream_events": [], "manual_review": [], "invalidation_id": "",
    }
    if not ids:
        return summary
    now = time.time()
    id_set = set(ids)
    undo: dict[str, Any] = {}

    try:
        from core.memory.dossiers import invalidate_source
        for event_id in ids:
            result = invalidate_source(scope, event_id, reason=reason)
            summary["dossiers"] += int(result.get("dossiers", 0))
    except Exception:
        logger.warning("[invalidation] dossier step failed", exc_info=True)

    try:
        mids = _invalidate_mid_term(scope, id_set, reason, now)
        summary["mid_term"] = len(mids)
        if mids:
            undo["mid_term"] = mids
            _provenance(scope, "mid_term", ",".join(mids)[:200], reason, ids)
    except Exception:
        logger.warning("[invalidation] mid_term step failed", exc_info=True)

    try:
        ep = _invalidate_episodic(scope, id_set, reason, now)
        summary["episodic"] = len(ep["full"])
        summary["partial"] = len(ep["partial"])
        summary["unlinked"] = ep["unlinked"]
        if ep["undo"]:
            undo["episodic"] = ep["undo"]
            for item in ep["undo"]:
                _provenance(scope, "episodic", str(item["id"]), reason, ids)
    except Exception:
        logger.warning("[invalidation] episodic step failed", exc_info=True)

    try:
        st = _invalidate_storyline(scope, id_set, reason, now)
        summary["storyline_nodes"] = len(st["nodes"])
        summary["storyline_arcs_unrecallable"] = len(st["arcs_unrecallable"])
        if st["nodes"]:
            undo["storyline"] = st["nodes"]
            for arc_id in sorted({a for a, _ in st["nodes"]}):
                _provenance(scope, "storyline", str(arc_id), reason, ids)
    except Exception:
        logger.warning("[invalidation] storyline step failed", exc_info=True)

    summary["downstream_events"] = _downstream_events(scope, ids)
    summary["manual_review"] = [
        {"store": name, "note": MANUAL_REVIEW_NOTE} for name in MANUAL_REVIEW_STORES
    ]

    changed = summary["dossiers"] or undo
    if changed:
        invalidation_id = f"inv_{uuid.uuid4().hex[:12]}"
        summary["invalidation_id"] = invalidation_id
        try:
            _append_ledger(scope, {
                "type": "invalidate", "invalidation_id": invalidation_id, "ts": now,
                "reason": reason, "actor": actor, "event_ids": ids,
                "summary": {k: v for k, v in summary.items() if k != "manual_review"},
                "undo": undo,
            })
        except Exception:
            logger.warning("[invalidation] ledger write failed", exc_info=True)
    return summary


def read_ledger(scope: MemoryScope, *, limit: int = 50) -> list[dict[str, Any]]:
    """Newest-first ledger records; undo payload is omitted (IDs/counts only)."""
    path = _ledger_path(scope)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        rec.pop("undo", None)
        records.append(rec)
    records.reverse()
    return records[: max(1, int(limit))]


def revert_invalidation(scope: MemoryScope, invalidation_id: str) -> dict[str, Any]:
    """Restore the markers set by one invalidation.  Dossiers recompute on their own;
    episodic vectors are not re-embedded (rebuild via the memory janitor)."""
    path = _ledger_path(scope)
    target = None
    reverted: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") == "revert":
                reverted.add(rec.get("invalidation_id"))
            elif rec.get("invalidation_id") == invalidation_id:
                target = rec
    if target is None:
        return {"ok": False, "error": "not_found"}
    if invalidation_id in reverted:
        return {"ok": False, "error": "already_reverted"}
    undo = target.get("undo") or {}
    restored = {"mid_term": 0, "episodic": 0, "storyline_nodes": 0}

    if undo.get("mid_term"):
        from core.memory import mid_term
        from core.safe_write import safe_write_json
        mpath = mid_term._read_file(scope.uid, char_id=scope.character_id)
        if mpath.exists():
            data = json.loads(mpath.read_text(encoding="utf-8"))
            for entry in data.get("events", []):
                if entry.get("mid_id") in undo["mid_term"] and entry.pop("invalidated", None) is not None:
                    restored["mid_term"] += 1
            safe_write_json(mpath, {"events": data.get("events", [])})

    if undo.get("episodic"):
        from core.memory import episodic_memory as ep
        memories = ep._load_memories(scope.uid, char_id=scope.character_id)
        by_id = {m.get("id"): m for m in memories}
        for item in reversed(undo["episodic"]):
            mem = by_id.get(item["id"])
            if mem is None:
                continue
            mem["status"] = item["status"]
            if item.get("strength") is not None:
                mem["strength"] = item["strength"]
            if item.get("has_invalid"):
                mem["invalid_source_event_ids"] = item["invalid_source_event_ids"]
            else:
                mem.pop("invalid_source_event_ids", None)
            for key in ("invalidated_at", "invalidated_reason"):
                mem.pop(key, None)
            restored["episodic"] += 1
        ep._save_memories(scope.uid, memories, char_id=scope.character_id)
        ep._rebuild_index(scope.uid, memories, char_id=scope.character_id)

    if undo.get("storyline"):
        from core.memory import storyline
        data = storyline.load(scope.uid, char_id=scope.character_id)
        wanted = {tuple(pair) for pair in undo["storyline"]}
        for arc in data.get("arcs", []):
            for node in arc.get("nodes", []):
                if (arc.get("arc_id"), node.get("node_id")) in wanted and node.pop("invalidated", None) is not None:
                    restored["storyline_nodes"] += 1
        storyline._save(scope.uid, data, char_id=scope.character_id)

    _append_ledger(scope, {"type": "revert", "invalidation_id": invalidation_id, "ts": time.time(),
                           "restored": restored})
    for artifact in ("mid_term", "episodic", "storyline"):
        if undo.get(artifact):
            _provenance(scope, artifact, invalidation_id, "revert", target.get("event_ids") or [])
    return {"ok": True, "restored": restored}
