"""S4b: tombstone -> derived conclusions invalidation cascade (reversible)."""
from __future__ import annotations

import json
import time

from tests.fixtures.public_assets import TEST_CHAR_ID
from tests.test_memory_dossiers import _event, _scope

UID = "s4b-owner"


def _seed(scope):
    from core.memory import episodic_memory as ep, mid_term, storyline
    from core.memory.path_resolver import resolve_path
    from core.safe_write import safe_write_json

    for eid in ("ev-a", "ev-b", "ev-c"):
        assert _event(scope, eid).ok
    now = time.time()
    base = {"timestamp": now, "occurred_at": now, "strength": 0.9, "status": "open",
            "tags": [], "retrieval_count": 0}
    safe_write_json(resolve_path(scope, "episodic"), [
        {**base, "id": "ep_full", "narrative_summary": "full SENTINEL", "source_event_ids": ["ev-a"]},
        {**base, "id": "ep_part", "narrative_summary": "part", "source_event_ids": ["ev-a", "ev-b"]},
        {**base, "id": "ep_none", "narrative_summary": "none"},
        {**base, "id": "ep_other", "narrative_summary": "other", "source_event_ids": ["ev-b"]},
    ])
    safe_write_json(resolve_path(scope, "mid_term"), {"events": [
        {"mid_id": "m1", "ts": now, "summary": "mid-a SENTINEL", "source_event_ids": ["ev-a"]},
        {"mid_id": "m2", "ts": now, "summary": "mid-c", "source_event_ids": ["ev-c"]},
    ]})
    arc = storyline.open_arc(UID, char_id=TEST_CHAR_ID, title="arc1")
    assert storyline.append_node(UID, char_id=TEST_CHAR_ID, arc_id=arc, summary="n1",
                                 ts=now, source_ids=["ev-a"])
    arc2 = storyline.open_arc(UID, char_id=TEST_CHAR_ID, title="arc2")
    storyline.append_node(UID, char_id=TEST_CHAR_ID, arc_id=arc2, summary="n2", ts=now, source_ids=["ev-a"])
    storyline.append_node(UID, char_id=TEST_CHAR_ID, arc_id=arc2, summary="n3", ts=now + 1, source_ids=["ev-c"])
    return arc, arc2


def _eps(scope):
    from core.memory import episodic_memory as ep
    return {m["id"]: m for m in ep._load_memories(UID, char_id=TEST_CHAR_ID)}


def test_cascade_revert_and_idempotence(sandbox, monkeypatch):
    from core.memory import event_store, invalidation, mid_term, storyline
    from core.memory.invalidation import read_ledger, revert_invalidation
    from core.memory.path_resolver import resolve_path

    scope = _scope(UID)
    arc1, arc2 = _seed(scope)
    deleted = []
    from core.memory import vector_store
    monkeypatch.setattr(vector_store, "delete", lambda u, c, s, i: deleted.append(i) or True)

    result = event_store.tombstone_event(scope, "ev-a")
    assert result.changed
    d = result.derived
    assert d["episodic"] == 1 and d["partial"] == 1 and d["unlinked"] == 1
    assert d["mid_term"] == 1 and d["storyline_nodes"] == 2
    assert d["storyline_arcs_unrecallable"] == 1
    assert {m["store"] for m in d["manual_review"]} >= {"user_identity", "important_facts"}
    assert deleted == ["ep_full"]

    eps = _eps(scope)
    assert eps["ep_full"]["status"] == "invalidated"
    assert eps["ep_part"]["status"] == "open" and eps["ep_part"]["strength"] <= 0.3
    assert eps["ep_part"]["invalid_source_event_ids"] == ["ev-a"]
    assert eps["ep_none"]["status"] == "open" and eps["ep_other"]["strength"] == 0.9

    text = mid_term.format_for_prompt(UID, char_id=TEST_CHAR_ID)
    assert "mid-a" not in text and "mid-c" in text
    arcs = {a["title"]: a for a in storyline.list_recallable_arcs(UID, char_id=TEST_CHAR_ID)}
    assert "arc1" not in arcs
    assert [n["summary"] for n in arcs["arc2"]["nodes"]] == ["n3"]

    ledger = read_ledger(scope)
    assert len(ledger) == 1 and "undo" not in ledger[0]
    raw = resolve_path(scope, "memory_invalidations").read_text(encoding="utf-8")
    assert "SENTINEL" not in raw  # IDs and counts only
    from core.memory import provenance_log
    assert provenance_log.query(UID, TEST_CHAR_ID, artifact="episodic")

    # idempotent: second run changes nothing and writes no new ledger row
    again = invalidation.invalidate_by_events(scope, ["ev-a"], reason="event_tombstoned", actor="t")
    assert again["episodic"] == again["mid_term"] == again["storyline_nodes"] == again["partial"] == 0
    assert len(read_ledger(scope)) == 1

    inv_id = ledger[0]["invalidation_id"]
    res = revert_invalidation(scope, inv_id)
    assert res["ok"] and res["restored"] == {"mid_term": 1, "episodic": 2, "storyline_nodes": 2}
    eps = _eps(scope)
    assert eps["ep_full"]["status"] == "open" and eps["ep_part"]["strength"] == 0.9
    assert "invalid_source_event_ids" not in eps["ep_part"]
    assert "mid-a" in mid_term.format_for_prompt(UID, char_id=TEST_CHAR_ID)
    assert {a["title"] for a in storyline.list_recallable_arcs(UID, char_id=TEST_CHAR_ID)} == {"arc1", "arc2"}
    assert revert_invalidation(scope, inv_id)["error"] == "already_reverted"


def test_partial_then_full_upgrade_and_downstream_report(sandbox, monkeypatch):
    from core.memory import invalidation

    scope = _scope("s4b-upgrade")
    for eid in ("ev-a", "ev-b"):
        assert _event(scope, eid).ok
    from core.memory.path_resolver import resolve_path
    from core.safe_write import safe_write_json
    now = time.time()
    safe_write_json(resolve_path(scope, "episodic"), [{
        "id": "ep1", "timestamp": now, "occurred_at": now, "strength": 0.8, "status": "open",
        "narrative_summary": "x", "tags": [], "source_event_ids": ["ev-a", "ev-b"]}])
    from core.memory import vector_store
    monkeypatch.setattr(vector_store, "delete", lambda *a: True)
    first = invalidation.invalidate_by_events(scope, ["ev-a"], reason="r", actor="t")
    assert first["partial"] == 1 and first["episodic"] == 0
    second = invalidation.invalidate_by_events(scope, ["ev-b"], reason="r", actor="t")
    assert second["episodic"] == 1
    from core.memory import episodic_memory as ep
    assert ep._load_memories("s4b-upgrade", char_id=TEST_CHAR_ID)[0]["status"] == "invalidated"
    # invalidated episodes never come back via fallback
    assert ep.retrieve_fallback("s4b-upgrade", [], char_id=TEST_CHAR_ID) == []


def test_observability_endpoint_registered():
    from admin.routers import observability
    paths = {r.path for r in observability.router.routes}
    assert "/observability/memory-invalidations" in paths
