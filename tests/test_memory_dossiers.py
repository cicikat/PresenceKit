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


def test_initialize_upgrades_v2_source_items_before_priority_index(sandbox):
    from core.memory import dossiers
    from core.memory.path_resolver import resolve_path

    scope = _scope(uid="v2-upgrade-owner")
    path = resolve_path(scope, "memory_dossiers")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE source_items (
              store_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_revision TEXT NOT NULL,
              ingest_sequence INTEGER NOT NULL, status TEXT NOT NULL, semantic_outcomes_json TEXT NOT NULL,
              attempt INTEGER NOT NULL DEFAULT 0, operation_id TEXT, input_digest TEXT NOT NULL,
              last_error TEXT NOT NULL, revisit_condition TEXT NOT NULL, updated_at REAL NOT NULL,
              PRIMARY KEY(store_kind, source_id, source_revision)
            );
            CREATE TABLE maintenance_state (
              state_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL
            );
            INSERT INTO source_items VALUES(
              'event','evt-old','r1',1,'pending','[]',0,NULL,'digest','','',1.0
            );
            PRAGMA user_version=2;
            """
        )
    assert dossiers.initialize(scope).healthy
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(source_items)")}
        assert "priority_class" in columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == dossiers.SCHEMA_VERSION
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(source_items)")}
        assert "idx_source_items_priority" in indexes
    assert dossiers.maintenance_checkpoint(scope) == 0
    claimed = dossiers.claim_source_items(scope, limit=1, now=20, cold_theme_share=0.0)
    assert claimed["count"] == 1
    assert claimed["items"][0]["source_id"] == "evt-old"
    assert claimed["items"][0]["priority_class"] == "remaining"


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


def test_claim_is_bounded_stable_and_does_not_use_title_as_key(sandbox):
    from core.memory import dossiers

    scope = _scope(uid="claim-owner")
    dossiers.seed_source_items(scope, [
        {"store_kind": "event", "source_id": "evt-b", "source_revision": "r1",
         "ingest_sequence": 2, "input_digest": "d2"},
        {"store_kind": "event", "source_id": "evt-a", "source_revision": "r1",
         "ingest_sequence": 1, "input_digest": "d1"},
        {"store_kind": "event", "source_id": "evt-c", "source_revision": "r1",
         "ingest_sequence": 3, "input_digest": "d3"},
        {"store_kind": "mid_term", "source_id": "mid-a", "source_revision": "r1",
         "ingest_sequence": 1, "input_digest": "d4"},
    ], now=10)
    first = dossiers.claim_source_items(scope, limit=2, task_id="a" * 32, now=20)
    assert first["count"] == 2
    assert [item["source_id"] for item in first["items"]] == ["evt-a", "evt-b"]
    assert all(item["status"] == "running" and item["task_id"] == "a" * 32 for item in first["items"])
    leftover = dossiers.list_source_items(scope, status="pending")
    assert leftover["total"] == 2
    second = dossiers.claim_source_items(scope, limit=10, now=21)
    assert [item["source_id"] for item in second["items"]] == ["evt-c", "mid-a"]
    assert dossiers.list_source_items(scope, status="pending")["total"] == 0


def test_claim_orders_priority_class_before_store_kind(sandbox):
    from core.memory import dossiers

    scope = _scope(uid="priority-claim-owner")
    dossiers.seed_source_items(scope, [
        {"store_kind": "event", "source_id": "evt-remaining", "source_revision": "r1",
         "ingest_sequence": 1, "input_digest": "d1", "priority_class": "remaining"},
        {"store_kind": "event", "source_id": "evt-recent", "source_revision": "r1",
         "ingest_sequence": 2, "input_digest": "d2", "priority_class": "recent"},
        {"store_kind": "event", "source_id": "evt-theme", "source_revision": "r1",
         "ingest_sequence": 3, "input_digest": "d3", "priority_class": "active_theme"},
        {"store_kind": "event", "source_id": "evt-fix", "source_revision": "r1",
         "ingest_sequence": 4, "input_digest": "d4", "priority_class": "correction"},
    ], now=10)
    claimed = dossiers.claim_source_items(scope, limit=4, now=20, cold_theme_share=0.0)
    assert [item["source_id"] for item in claimed["items"]] == [
        "evt-fix", "evt-theme", "evt-recent", "evt-remaining",
    ]
    assert dossiers.source_item_priority_counts(scope)["correction"] == 0


def test_claim_reserves_remaining_cold_share(sandbox):
    from core.memory import dossiers

    scope = _scope(uid="cold-share-owner")
    items = [
        {"store_kind": "event", "source_id": f"evt-hot-{index}", "source_revision": "r1",
         "ingest_sequence": index, "input_digest": f"h{index}", "priority_class": "recent"}
        for index in range(1, 5)
    ] + [
        {"store_kind": "event", "source_id": "evt-cold", "source_revision": "r1",
         "ingest_sequence": 99, "input_digest": "cold", "priority_class": "remaining"},
    ]
    dossiers.seed_source_items(scope, items, now=10)
    claimed = dossiers.claim_source_items(scope, limit=4, now=20, cold_theme_share=0.25)
    classes = [item["priority_class"] for item in claimed["items"]]
    assert classes.count("remaining") == 1
    assert "evt-cold" in [item["source_id"] for item in claimed["items"]]


def test_related_dossiers_follow_source_ids_not_titles(sandbox):
    from core.memory import dossiers

    scope = _scope(uid="related-owner")
    event_id = "related-event"
    assert _event(scope, event_id).ok
    dossier_id, _ = _create(scope, title="Shared Title")
    other_id, _ = _create(scope, title="Shared Title")
    occurrence_id = uuid.uuid4().hex
    dossiers.apply_operations(scope, [
        {"action": "create_occurrence", "occurrence_id": occurrence_id, "participants": [],
         "time_certainty": "unknown", "assertion_kind": "user_stated",
         "evidence": [{"reference_kind": "event", "source_id": event_id, "source_revision": "1"}]},
        {"action": "attach_occurrence", "dossier_id": dossier_id, "occurrence_id": occurrence_id,
         "expected_revision": 1},
    ], operation_id=_op_id(), actor="character", chain="owner_chat")
    related = dossiers.related_dossiers_for_sources(scope, [
        {"store_kind": "event", "source_id": event_id},
    ])
    assert [item["dossier_id"] for item in related] == [dossier_id]
    assert related[0]["revision"] >= 2
    assert event_id in related[0]["matching_source_ids"]
    assert other_id not in {item["dossier_id"] for item in related}


def test_expired_lease_reconciles_from_receipt_or_releases(sandbox):
    from core.memory import dossiers
    from core.memory.path_resolver import resolve_path

    scope = _scope(uid="lease-owner")
    dossiers.seed_source_items(scope, [
        {"store_kind": "event", "source_id": "lease-a", "source_revision": "r1",
         "ingest_sequence": 1, "input_digest": "d1"},
        {"store_kind": "event", "source_id": "lease-b", "source_revision": "r1",
         "ingest_sequence": 2, "input_digest": "d2"},
    ], now=1)
    claimed = dossiers.claim_source_items(scope, limit=2, lease_seconds=10, now=100)
    assert claimed["count"] == 2
    with sqlite3.connect(resolve_path(scope, "memory_dossiers")) as connection:
        connection.execute(
            "INSERT INTO processing_commits VALUES(?,?,?,?,?,?,?)",
            ("c" * 32, "o" * 32, "event", "lease-a", "r1", 1, 100),
        )
        connection.commit()
    recovered = dossiers.reconcile_source_item_leases(scope, now=200)
    assert recovered["committed"] == 1
    assert recovered["released"] == 1
    listed = {item["source_id"]: item for item in dossiers.list_source_items(scope, store_kind="event")["items"]}
    assert listed["lease-a"]["status"] == "committed"
    assert listed["lease-a"]["operation_id"] == "o" * 32
    assert listed["lease-b"]["status"] == "retryable_failed"
    assert listed["lease-b"]["last_error"] == "lease_expired"
    retry = dossiers.claim_source_items(scope, limit=10, now=201)
    assert [item["source_id"] for item in retry["items"]] == ["lease-b"]


def test_failed_commit_does_not_mark_claimed_items_committed(sandbox):
    from core.memory import dossiers

    scope = _scope(uid="fail-claim-owner")
    dossiers.seed_source_items(scope, [
        {"store_kind": "event", "source_id": "fail-a", "source_revision": "r1",
         "ingest_sequence": 1, "input_digest": "d1"},
    ], now=1)
    claimed = dossiers.claim_source_items(scope, limit=1, now=2)
    with pytest.raises(dossiers.DossierError, match="invalid_operation"):
        dossiers.apply_operations(scope, [{"action": "not-a-handler"}],
                                  operation_id=_op_id(), actor="character", chain="maintenance",
                                  processing_items=claimed["items"])
    released = dossiers.fail_source_item_claim(scope, claimed["items"], error="commit_error", now=3)
    assert released == 1
    listed = dossiers.list_source_items(scope, store_kind="event")
    assert listed["items"][0]["status"] == "retryable_failed"
    assert listed["items"][0]["last_error"] == "commit_error"
    assert dossiers.operation_receipt(scope, "f" * 32) is None


def test_tombstoned_event_cannot_be_cited_or_maintained(sandbox):
    from core.memory import event_store
    from core.memory.dossiers import DossierError, apply_operations, maintenance_candidates

    scope = _scope("s4a-owner"); event_id = "s4a-gone"; assert _event(scope, event_id).ok
    assert any(c["source_id"] == event_id for c in maintenance_candidates(scope))
    assert event_store.tombstone_event(scope, event_id).changed
    assert not any(c["source_id"] == event_id for c in maintenance_candidates(scope))
    with pytest.raises(DossierError, match="evidence_tombstoned"):
        apply_operations(scope, [
            {"action": "create_occurrence", "occurrence_id": uuid.uuid4().hex, "participants": [],
             "time_certainty": "unknown", "assertion_kind": "user_stated",
             "evidence": [{"reference_kind": "event", "source_id": event_id, "source_revision": "1"}]},
        ], operation_id=_op_id(), actor="character", chain="owner_chat")
