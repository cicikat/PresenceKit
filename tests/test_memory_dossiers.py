from __future__ import annotations

import sqlite3
import uuid

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


def _scope(uid="dossier-owner", char_id=TEST_CHAR_ID):
    from core.memory.scope import MemoryScope
    return MemoryScope.reality_scope(uid, char_id)


def _event(scope, event_id, text="evidence"):
    from core.memory.event_store import append_event
    return append_event(scope, {
        "event_id": event_id, "turn_id": event_id, "seq": 1,
        "occurred_at": 100.0, "ingested_at": 101.0,
        "uid": scope.uid, "char_id": scope.character_id, "realm": "reality",
        "kind": "owner_chat", "actor": "user", "channel": "desktop",
        "source": "fixture", "raw_payload_json": {}, "raw_text": text,
        "visible_text": text, "memory_text": text,
        "media_refs_json": [], "redaction_state": "scrubbed",
    })


def _op_id():
    return uuid.uuid4().hex


def _create(scope, *, title="Topic", dossier_id=None, operation_id=None):
    from core.memory.dossiers import apply_operations
    dossier_id = dossier_id or uuid.uuid4().hex
    result = apply_operations(scope, [{"action": "create_dossier", "dossier_id": dossier_id,
        "title": title, "aliases": [], "description": ""}],
        operation_id=operation_id or _op_id(), actor="character", chain="owner_chat")
    return dossier_id, result


def test_dossier_store_is_scoped_and_read_status_does_not_create(sandbox):
    from core.memory.dossiers import initialize, schema_status
    from core.memory.path_resolver import resolve_path

    first, second = _scope(), _scope(char_id=TEST_PEER_CHAR_ID)
    assert schema_status(first).error_code == "not_initialized"
    assert not resolve_path(first, "memory_dossiers").exists()
    assert initialize(first).healthy
    assert resolve_path(first, "memory_dossiers").exists()
    assert not resolve_path(second, "memory_dossiers").exists()


def test_atomic_batch_cas_idempotency_and_no_evidence_copy(sandbox):
    from core.memory.dossiers import DossierError, apply_operations, read
    from core.memory.path_resolver import resolve_path

    scope = _scope(); event_id = "dossier-event-1"; secret = "PRIVATE_SOURCE_BODY_7931"; assert _event(scope, event_id, secret).ok
    dossier_id = uuid.uuid4().hex; occurrence_id = uuid.uuid4().hex; operation_id = _op_id()
    ops = [
        {"action": "create_dossier", "dossier_id": dossier_id, "title": "Drinks", "aliases": [], "description": ""},
        {"action": "create_occurrence", "occurrence_id": occurrence_id,
         "occurrence_key": "purchase:one", "participants": ["owner"],
         "occurred_from": 100, "occurred_to": 100, "time_certainty": "exact",
         "assertion_kind": "user_stated", "evidence": [{"reference_kind": "event", "source_id": event_id, "source_revision": "1"}]},
        {"action": "attach_occurrence", "dossier_id": dossier_id,
         "occurrence_id": occurrence_id, "expected_revision": 1},
        {"action": "revise_understanding", "dossier_id": dossier_id,
         "expected_revision": 2, "summary": "One confirmed purchase", "conditions": [],
         "supporting_occurrence_ids": [occurrence_id], "counterexample_occurrence_ids": [],
         "confidence_reason": "one statement", "coverage_ingest_seq": 1},
    ]
    first = apply_operations(scope, ops, operation_id=operation_id, actor="character", chain="owner_chat")
    assert apply_operations(scope, ops, operation_id=operation_id, actor="character", chain="owner_chat") == first
    assert read(scope, dossier_id)["understanding"]["summary"] == "One confirmed purchase"
    with pytest.raises(DossierError, match="idempotency_conflict"):
        apply_operations(scope, [{"action": "create_dossier", "title": "Different"}],
                         operation_id=operation_id, actor="character", chain="owner_chat")
    with pytest.raises(DossierError, match="revision_conflict"):
        apply_operations(scope, [{"action": "rename_dossier", "dossier_id": dossier_id,
                         "expected_revision": 1, "title": "Old overwrite"}],
                         operation_id=_op_id(), actor="worker", chain="maintenance")

    path = resolve_path(scope, "memory_dossiers")
    assert secret not in path.read_bytes().decode("utf-8", errors="ignore")


def test_invalid_batch_rolls_back_and_cross_scope_evidence_fails(sandbox):
    from core.memory.dossiers import DossierError, apply_operations, search

    scope = _scope(); other = _scope(char_id=TEST_PEER_CHAR_ID)
    assert _event(other, "other-scope-event").ok
    with pytest.raises(DossierError, match="evidence_not_found"):
        apply_operations(scope, [
            {"action": "create_dossier", "title": "Must roll back"},
            {"action": "create_occurrence", "participants": [], "time_certainty": "unknown",
             "assertion_kind": "observed", "evidence": [{"reference_kind": "event",
             "source_id": "other-scope-event", "source_revision": "1"}]},
        ], operation_id=_op_id(), actor="character", chain="owner_chat")
    assert search(scope) == []


def test_duplicate_candidate_split_merge_and_tentative_relation(sandbox):
    from core.memory.dossiers import DossierError, apply_operations, read

    scope = _scope(); assert _event(scope, "event-a").ok
    source, _ = _create(scope, title="Source"); target, _ = _create(scope, title="Target")
    occurrence = uuid.uuid4().hex
    apply_operations(scope, [{"action": "create_occurrence", "occurrence_id": occurrence,
        "occurrence_key": "same-experience", "participants": [], "time_certainty": "unknown",
        "assertion_kind": "user_stated", "evidence": [{"reference_kind": "event", "source_id": "event-a", "source_revision": "1"}]}],
        operation_id=_op_id(), actor="character", chain="owner_chat")
    with pytest.raises(DossierError, match="duplicate_occurrence_candidate"):
        apply_operations(scope, [{"action": "create_occurrence", "occurrence_key": "same-experience",
            "participants": [], "time_certainty": "unknown", "assertion_kind": "user_stated",
            "evidence": [{"reference_kind": "event", "source_id": "event-a", "source_revision": "1"}]}],
            operation_id=_op_id(), actor="character", chain="owner_chat")
    apply_operations(scope, [{"action": "attach_occurrence", "dossier_id": source,
        "occurrence_id": occurrence, "expected_revision": 1}], operation_id=_op_id(), actor="character", chain="owner_chat")
    split = uuid.uuid4().hex
    apply_operations(scope, [{"action": "split_dossier", "source_dossier_id": source,
        "expected_revision": 2, "new_dossier": {"dossier_id": split, "title": "Split"},
        "occurrence_ids": [occurrence]}], operation_id=_op_id(), actor="character", chain="owner_chat")
    apply_operations(scope, [{"action": "relate_dossiers", "from_dossier_id": split,
        "to_dossier_id": target, "expected_revision": 1, "relation_type": "tentative_cause"}],
        operation_id=_op_id(), actor="character", chain="owner_chat")
    assert read(scope, split)["relations"][0]["tentative"] == 1
    apply_operations(scope, [{"action": "merge_dossiers", "target_dossier_id": target,
        "expected_target_revision": 1, "source_dossier_ids": [split],
        "expected_source_revisions": {split: 2}}], operation_id=_op_id(), actor="character", chain="owner_chat")
    assert read(scope, split)["status"] == "merged"
    assert read(scope, split)["redirect_dossier_id"] == target


def test_tombstone_immediately_invalidates_active_understanding(sandbox):
    from core.memory import event_store
    from core.memory.dossiers import apply_operations, read, search

    scope = _scope(); event_id = "withdraw-me"; assert _event(scope, event_id).ok
    dossier, _ = _create(scope)
    occurrence = uuid.uuid4().hex
    apply_operations(scope, [
        {"action": "create_occurrence", "occurrence_id": occurrence, "participants": [],
         "time_certainty": "unknown", "assertion_kind": "user_stated",
         "evidence": [{"reference_kind": "event", "source_id": event_id, "source_revision": "1"}]},
        {"action": "attach_occurrence", "dossier_id": dossier, "occurrence_id": occurrence, "expected_revision": 1},
        {"action": "revise_understanding", "dossier_id": dossier, "expected_revision": 2,
         "summary": "Must disappear", "supporting_occurrence_ids": [occurrence],
         "counterexample_occurrence_ids": [], "conditions": [], "confidence_reason": "source"},
    ], operation_id=_op_id(), actor="character", chain="owner_chat")
    assert search(scope)[0]["summary"] == "Must disappear"
    assert event_store.tombstone_event(scope, event_id).changed
    assert search(scope) == []
    stored = read(scope, dossier)
    assert stored["needs_recompute"] == 1 and stored["active_understanding_id"] is None


def test_failed_batch_does_not_leave_partial_rows(sandbox):
    from core.memory.dossiers import DossierError, apply_operations
    from core.memory.path_resolver import resolve_path

    scope = _scope(); dossier = uuid.uuid4().hex
    with pytest.raises(DossierError):
        apply_operations(scope, [
            {"action": "create_dossier", "dossier_id": dossier, "title": "Partial"},
            {"action": "rename_dossier", "dossier_id": dossier, "expected_revision": 99, "title": "No"},
        ], operation_id=_op_id(), actor="character", chain="owner_chat")
    with sqlite3.connect(resolve_path(scope, "memory_dossiers")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dossiers").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
