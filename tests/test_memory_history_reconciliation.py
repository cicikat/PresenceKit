from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fixtures.public_assets import TEST_CHAR_ID


def test_inventory_is_redacted_and_revisioned(sandbox):
    from core.memory.history_reconciliation import build_inventory
    from core.memory.scope import MemoryScope
    from core.memory.event_store import append_event

    scope = MemoryScope.reality_scope("inventory-owner", TEST_CHAR_ID)
    assert append_event(scope, {
        "event_id": "inventory-event", "turn_id": "inventory-event", "seq": 1,
        "occurred_at": 1.0, "ingested_at": 2.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "chat",
        "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": "secret evidence", "memory_text": "secret evidence",
        "redaction_state": "scrubbed",
    }).ok
    result = build_inventory(scope.uid, scope.character_id, now=10)
    assert result["schema_version"] == "memory-history-inventory.v1"
    assert result["read_only"] is True
    assert result["model_calls"] == 0
    assert result["scope"]["uid_digest"] != scope.uid
    assert "secret evidence" not in str(result)
    assert {item["store_kind"] for item in result["items"]} >= {"event_store", "event_log", "episodic"}


def test_inventory_endpoint_requires_state_read_and_is_empty_without_data(sandbox):
    import yaml
    from admin import token_registry
    from admin.routers.memory_consolidation import router

    path = sandbox.auth_tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"tokens": [{"label": "state", "hash": token_registry.hash_token("state"), "scopes": ["state.read"]}]}), encoding="utf-8")
    token_registry._records = None; token_registry._mtime = None
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    response = client.get(f"/observability/memory-history-inventory?uid=inventory-empty&char_id={TEST_CHAR_ID}", headers={"Authorization": "Bearer state"})
    assert response.status_code == 200
    assert response.json()["read_only"] is True
    assert client.get(f"/observability/memory-history-inventory?uid=inventory-empty&char_id={TEST_CHAR_ID}").status_code == 401


def test_manifest_and_control_are_resumable_and_default_deferred(sandbox):
    from core.memory import history_reconciliation
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("reconcile-owner", TEST_CHAR_ID)
    manifest = history_reconciliation.create_manifest(scope, now=1)
    assert manifest["dry_run"] is True
    result = history_reconciliation.apply_dry_run(scope, backup_verified=False)
    assert result["status"] == "deferred"
    snapshot = history_reconciliation.status(scope)
    assert snapshot["counts"]["deferred"] >= 1
    assert snapshot["total"] == sum(snapshot["counts"].values())
    assert snapshot["ratios"]["deferred"] > 0
    assert snapshot["executable"] == 0
    history_reconciliation.set_paused(scope, True, reason="test")
    assert history_reconciliation.apply_dry_run(scope)["status"] == "paused"


def test_backup_helper_records_verified_snapshot(monkeypatch, tmp_path):
    from core.memory import history_reconciliation

    calls = {}

    def fake_create_snapshot(installation, output, *, protection_mode):
        calls.update({"installation": installation, "output": output, "protection_mode": protection_mode})
        return {"ok": True, "backup_id": "backup-fixture", "backup_path": str(output), "file_count": 7}

    monkeypatch.setattr("core.backup_state.create_snapshot", fake_create_snapshot)
    result = history_reconciliation.create_verified_backup(tmp_path / "snapshot")

    assert result == {
        "verified": True,
        "backup_id": "backup-fixture",
        "backup_path": str(tmp_path / "snapshot"),
        "file_count": 7,
    }
    assert calls["protection_mode"] == "protected_volume"


def test_recovery_drill_verifies_before_restoring(monkeypatch, tmp_path):
    from core.memory import history_reconciliation

    calls = []

    def fake_verify(snapshot):
        calls.append(("verify", snapshot))
        return {"ok": True, "errors": []}

    def fake_restore(installation, snapshot, target, *, startup_check):
        calls.append(("restore", installation, snapshot, target, startup_check))
        return {"ok": True, "backup_id": "backup-fixture", "target_path": str(target)}

    monkeypatch.setattr("core.backup_state.verify_snapshot", fake_verify)
    monkeypatch.setattr("core.backup_state.restore_snapshot", fake_restore)
    snapshot = tmp_path / "snapshot"
    target = tmp_path / "restored"
    result = history_reconciliation.recovery_drill(snapshot, target)

    assert result["ok"] is True
    assert result["stage"] == "restore"
    assert calls[0] == ("verify", snapshot)
    assert calls[1] == ("restore", __import__("pathlib").Path.cwd(), snapshot, target, False)


def test_verified_backup_releases_deferred_items_and_commits_ledger(sandbox, monkeypatch):
    from core.memory import history_reconciliation
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("reconcile-transition", TEST_CHAR_ID)
    manifest = history_reconciliation.create_manifest(scope, now=1)
    history_reconciliation.freeze_manifest(scope, manifest_revision=manifest["manifest_revision"])
    deferred = history_reconciliation.apply_dry_run(scope, backup_verified=False)
    assert deferred["status"] == "deferred"

    monkeypatch.setattr("core.memory.event_migration.scan_legacy", lambda _scope: {
        "indeterminate": False,
        "source_digest": "source-fixture",
        "entries": [],
        "would_write": 0,
        "comparison_status": "comparable",
    })
    result = history_reconciliation.apply_dry_run(scope, backup_verified=True)

    assert result["status"] == "committed"
    assert history_reconciliation.status(scope)["counts"]["committed"] >= 1


def test_apply_batch_commits_the_corresponding_ledger_item(sandbox, monkeypatch):
    from core.memory import history_reconciliation
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("reconcile-apply", TEST_CHAR_ID)
    manifest = history_reconciliation.create_manifest(scope, now=1)
    history_reconciliation.freeze_manifest(scope, manifest_revision=manifest["manifest_revision"])
    monkeypatch.setattr("core.memory.event_migration.scan_legacy", lambda _scope: {
        "indeterminate": False,
        "source_digest": "source-fixture",
        "entries": [],
        "would_write": 0,
        "comparison_status": "comparable",
    })
    monkeypatch.setattr("core.memory.event_migration.apply_batch", lambda *args, **kwargs: {
        "status": "committed",
        "written": 0,
    })

    result = history_reconciliation.apply_batch(
        scope,
        backup={"verified": True, "backup_id": "backup-fixture"},
        dry_run=False,
    )

    assert result["status"] == "committed"
    assert history_reconciliation.status(scope)["counts"]["committed"] >= 1


def test_apply_batch_records_derived_sources_without_rewriting_them(sandbox, monkeypatch):
    import json
    from core.memory import history_reconciliation
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("reconcile-derived", TEST_CHAR_ID)
    mid_path = resolve_path(scope, "mid_term")
    mid_path.parent.mkdir(parents=True, exist_ok=True)
    original = {"events": [{"mid_id": "mid-fixture", "summary": "evidence"}]}
    mid_path.write_text(json.dumps(original), encoding="utf-8")
    manifest = history_reconciliation.create_manifest(scope, now=1)
    history_reconciliation.freeze_manifest(scope, manifest_revision=manifest["manifest_revision"])
    monkeypatch.setattr("core.memory.event_migration.scan_legacy", lambda _scope: {
        "indeterminate": False, "source_digest": "source-fixture", "entries": [],
        "would_write": 0, "comparison_status": "comparable",
    })
    monkeypatch.setattr("core.memory.event_migration.apply_batch", lambda *args, **kwargs: {
        "status": "completed", "written": 0, "next_offset": 0, "total": 0,
    })
    result = history_reconciliation.apply_batch(
        scope, backup={"verified": True, "backup_id": "backup-fixture"},
        batch_size=10, dry_run=False,
    )
    assert result["status"] == "completed"
    assert history_reconciliation.status(scope)["counts"]["pending"] == 0
    assert json.loads(mid_path.read_text(encoding="utf-8")) == original


def test_verified_apply_requires_frozen_manifest(sandbox, monkeypatch):
    from core.memory import history_reconciliation
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("reconcile-unfrozen", TEST_CHAR_ID)
    history_reconciliation.create_manifest(scope, now=1)
    monkeypatch.setattr("core.memory.event_migration.scan_legacy", lambda _scope: {"entries": [], "indeterminate": False})
    result = history_reconciliation.apply_batch(
        scope, backup={"verified": True}, dry_run=False,
    )
    assert result == {"status": "deferred", "reason": "manifest_not_frozen"}


def test_first_night_runner_stops_at_cutoff_and_uses_verified_gate(sandbox, monkeypatch, tmp_path):
    from core.memory import history_reconciliation
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("first-night-owner", TEST_CHAR_ID)
    manifest = history_reconciliation.create_manifest(scope, now=1)
    history_reconciliation.freeze_manifest(scope, manifest_revision=manifest["manifest_revision"])
    monkeypatch.setattr(history_reconciliation, "verify_backup_snapshot", lambda path: {"verified": True, "errors": []})
    calls = {"count": 0}

    def fake_apply(*args, **kwargs):
        calls["count"] += 1
        return {"status": "committed", "migration": {"next_offset": 1, "total": 1}}

    async def fake_consolidate(*args, **kwargs):
        return {"status": "disabled", "model_calls": 0}

    monkeypatch.setattr(history_reconciliation, "apply_batch", fake_apply)
    monkeypatch.setattr(history_reconciliation, "consolidate_imported_events", fake_consolidate)
    result = __import__("asyncio").run(history_reconciliation.run_first_night(
        scope, backup_snapshot=tmp_path / "snapshot", manifest_revision=manifest["manifest_revision"],
        stop_at=0,
    ))
    assert result["status"] == "stopped"
    assert result["batches"] == 0
    assert calls["count"] == 0
    assert history_reconciliation.status(scope)["last_closeout"]["status"] == "stopped"


def test_imported_event_consolidation_pins_scope_and_bulk_preset(monkeypatch):
    from core.memory import history_reconciliation
    from core.memory.scope import MemoryScope

    seen = {}

    async def fake_tick(*, only_principal, preset_override, **kwargs):
        seen.update(principal=only_principal, preset=preset_override)
        return {"status": "disabled", "model_calls": 0}

    monkeypatch.setattr("core.memory.consolidation_worker.tick", fake_tick)
    scope = MemoryScope.reality_scope("history-owner", TEST_CHAR_ID)
    import asyncio
    result = asyncio.run(history_reconciliation.consolidate_imported_events(scope))

    assert result["status"] == "disabled"
    assert seen["principal"].uid == scope.uid
    assert seen["principal"].char_id == scope.character_id
    assert seen["preset"] == "便宜小模型grok-see"
def test_settle_evidence_only_requires_reason_and_closes_backlog(sandbox):
    import pytest
    from core.memory import dossiers, history_reconciliation
    from core.memory.event_store import append_event
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("evidence-only-owner", TEST_CHAR_ID)
    assert append_event(scope, {
        "event_id": "evidence-only-event", "turn_id": "evidence-only-turn", "seq": 1,
        "occurred_at": 1.0, "ingested_at": 2.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "chat",
        "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": "fixture", "memory_text": "fixture",
    }).ok
    with pytest.raises(ValueError, match="reason"):
        history_reconciliation.settle_evidence_only(scope, reason="")
    result = history_reconciliation.settle_evidence_only(scope, reason="provider_timeout")
    assert result["status"] == "committed"
    assert result["processed"] == 1
    assert dossiers.maintenance_status(scope)["backlog"] == 0


def test_manifest_seeds_per_source_item_ledger_without_copying_bodies(sandbox):
    import json
    from core.memory import dossiers, history_reconciliation
    from core.memory.event_store import append_event
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("source-item-owner", TEST_CHAR_ID)
    secret = "PRIVATE_SOURCE_BODY_2591"
    assert append_event(scope, {
        "event_id": "source-item-event", "turn_id": "source-item-turn", "seq": 1,
        "occurred_at": 1.0, "ingested_at": 2.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "chat",
        "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": secret, "memory_text": secret,
    }).ok
    mid_path = resolve_path(scope, "mid_term")
    mid_path.parent.mkdir(parents=True, exist_ok=True)
    mid_path.write_text(json.dumps({"events": [{"mid_id": "mid-source", "summary": secret}]}), encoding="utf-8")
    manifest = history_reconciliation.create_manifest(scope, now=1)
    snapshot = history_reconciliation.status(scope)
    listed = dossiers.list_source_items(scope, store_kind="event")
    mid_listed = dossiers.list_source_items(scope, store_kind="mid_term")
    assert manifest["source_item_total"] >= 2
    assert snapshot["source_item_counts"]["pending"] >= 2
    assert snapshot["source_item_total"] == sum(snapshot["source_item_counts"].values())
    assert listed["total"] == 1
    assert listed["items"][0]["source_id"] == "source-item-event"
    assert listed["items"][0]["status"] == "pending"
    assert listed["items"][0]["rule_version"]
    assert listed["items"][0]["operation_id"] == ""
    assert mid_listed["items"][0]["source_id"] == "mid-source"
    assert secret not in json.dumps(listed)
    assert secret not in json.dumps(mid_listed)
    assert secret not in json.dumps(snapshot)
    assert secret not in str(resolve_path(scope, "memory_dossiers").read_bytes())


def test_manifest_seed_is_conservative_and_revision_change_opens_new_pending(sandbox):
    import json
    from core.memory import dossiers, history_reconciliation
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("source-item-revision", TEST_CHAR_ID)
    mid_path = resolve_path(scope, "mid_term")
    mid_path.parent.mkdir(parents=True, exist_ok=True)
    mid_path.write_text(json.dumps({"events": [{"mid_id": "mid-rev", "summary": "first"}]}), encoding="utf-8")
    first = history_reconciliation.create_manifest(scope, now=1)
    second = history_reconciliation.create_manifest(scope, now=2)
    assert second["source_items_seeded"]["inserted"] == 0
    assert second["source_items_seeded"]["skipped"] >= first["source_items_seeded"]["inserted"]
    before = dossiers.list_source_items(scope, store_kind="mid_term", source_id="mid-rev")
    assert before["total"] == 1
    mid_path.write_text(json.dumps({"events": [{"mid_id": "mid-rev", "summary": "changed"}]}), encoding="utf-8")
    history_reconciliation.create_manifest(scope, now=3)
    after = dossiers.list_source_items(scope, store_kind="mid_term", source_id="mid-rev")
    revisions = {item["source_revision"] for item in after["items"]}
    assert after["total"] == 2
    assert len(revisions) == 2
    assert all(item["status"] == "pending" for item in after["items"])
    assert before["items"][0]["source_revision"] in revisions


def test_json_state_write_failure_does_not_duplicate_source_items(sandbox, monkeypatch):
    from core.memory import dossiers, history_reconciliation
    from core.memory.event_store import append_event
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("source-item-write-fail", TEST_CHAR_ID)
    assert append_event(scope, {
        "event_id": "write-fail-event", "turn_id": "write-fail-turn", "seq": 1,
        "occurred_at": 1.0, "ingested_at": 2.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "chat",
        "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": "fixture", "memory_text": "fixture",
    }).ok
    monkeypatch.setattr("core.memory.history_reconciliation.safe_write_json", lambda *args, **kwargs: False)
    try:
        history_reconciliation.create_manifest(scope, now=1)
    except OSError as exc:
        assert "history_reconciliation_state_write_failed" in str(exc)
    else:
        raise AssertionError("expected write failure")
    listed = dossiers.list_source_items(scope, store_kind="event")
    assert listed["total"] == 1
    assert listed["items"][0]["status"] == "pending"
    assert listed["items"][0]["operation_id"] == ""
    assert history_reconciliation.status(scope)["manifest_revision"] == ""
    monkeypatch.setattr(
        "core.memory.history_reconciliation.safe_write_json",
        __import__("core.safe_write", fromlist=["safe_write_json"]).safe_write_json,
    )
    retry = history_reconciliation.create_manifest(scope, now=2)
    assert retry["source_items_seeded"]["inserted"] == 0
    assert dossiers.list_source_items(scope, store_kind="event")["total"] == 1


def test_source_item_endpoint_requires_memory_read_and_is_metadata_only(sandbox):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import yaml
    from admin import token_registry
    from admin.routers.memory_consolidation import router
    from core.memory import history_reconciliation
    from core.memory.event_store import append_event
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("source-item-api", TEST_CHAR_ID)
    secret = "PRIVATE_SOURCE_BODY_2592"
    assert append_event(scope, {
        "event_id": "api-source-event", "turn_id": "api-source-turn", "seq": 1,
        "occurred_at": 1.0, "ingested_at": 2.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "chat",
        "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": secret, "memory_text": secret,
    }).ok
    history_reconciliation.create_manifest(scope, now=1)
    path = sandbox.auth_tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"tokens": [
        {"label": "state", "hash": token_registry.hash_token("state"), "scopes": ["state.read"]},
        {"label": "memory", "hash": token_registry.hash_token("memory"), "scopes": ["memory.read"]},
    ]}), encoding="utf-8")
    token_registry._records = None
    token_registry._mtime = None
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    query = f"/memory/history-source-items?uid={scope.uid}&char_id={scope.character_id}&store_kind=event"
    denied = client.get(query, headers={"Authorization": "Bearer state"})
    allowed = client.get(query, headers={"Authorization": "Bearer memory"})
    assert denied.status_code == 403
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["total"] == 1
    assert payload["items"][0]["source_id"] == "api-source-event"
    assert payload["items"][0]["status"] == "pending"
    assert secret not in allowed.text


def test_rollback_reopens_evidence_receipts_without_touching_source(sandbox):
    from core.memory import dossiers, history_reconciliation
    from core.memory.event_store import append_event
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("rollback-owner", TEST_CHAR_ID)
    assert append_event(scope, {
        "event_id": "rollback-event", "turn_id": "rollback-turn", "seq": 1,
        "occurred_at": 1.0, "ingested_at": 2.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "chat",
        "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": "fixture", "memory_text": "fixture",
    }).ok
    assert history_reconciliation.settle_evidence_only(scope, reason="calibration_pause")["processed"] == 1
    result = history_reconciliation.rollback_batch(scope, reason="operator_review")
    assert result["status"] == "rolled_back"
    assert result["reopened"] == 1
    assert dossiers.maintenance_status(scope)["backlog"] == 1


def test_claim_batch_is_bounded_and_returns_related_dossier_ids(sandbox):
    from core.memory import dossiers, history_reconciliation
    from core.memory.event_store import append_event
    from core.memory.scope import MemoryScope
    import uuid

    scope = MemoryScope.reality_scope("history-claim-owner", TEST_CHAR_ID)
    secret = "PRIVATE_SOURCE_BODY_2593"
    assert append_event(scope, {
        "event_id": "history-claim-event", "turn_id": "history-claim-turn", "seq": 1,
        "occurred_at": 1.0, "ingested_at": 2.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "chat",
        "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": secret, "memory_text": secret,
    }).ok
    history_reconciliation.create_manifest(scope, now=1)
    dossier_id = uuid.uuid4().hex
    occurrence_id = uuid.uuid4().hex
    dossiers.apply_operations(scope, [
        {"action": "create_dossier", "dossier_id": dossier_id, "title": "Shared Title",
         "aliases": [], "description": ""},
        {"action": "create_occurrence", "occurrence_id": occurrence_id, "participants": [],
         "time_certainty": "unknown", "assertion_kind": "user_stated",
         "evidence": [{"reference_kind": "event", "source_id": "history-claim-event",
                       "source_revision": "1"}]},
        {"action": "attach_occurrence", "dossier_id": dossier_id, "occurrence_id": occurrence_id,
         "expected_revision": 1},
    ], operation_id=uuid.uuid4().hex, actor="character", chain="owner_chat")
    claimed = history_reconciliation.claim_batch(scope, batch_size=1, store_kind="event", now=2)
    assert claimed["count"] == 1
    assert claimed["items"][0]["source_id"] == "history-claim-event"
    assert claimed["items"][0]["status"] == "running"
    assert [item["dossier_id"] for item in claimed["related_dossiers"]] == [dossier_id]
    assert secret not in str(claimed)
    leftover = dossiers.list_source_items(scope, store_kind="event", status="pending")
    assert leftover["total"] == 0


def test_derived_commit_write_failure_releases_claim_without_receipt(sandbox, monkeypatch):
    from core.memory import dossiers, history_reconciliation
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    import json

    scope = MemoryScope.reality_scope("history-fail-owner", TEST_CHAR_ID)
    mid_path = resolve_path(scope, "mid_term")
    mid_path.parent.mkdir(parents=True, exist_ok=True)
    mid_path.write_text(json.dumps({"events": [{"mid_id": "mid-fail", "summary": "fixture"}]}), encoding="utf-8")
    history_reconciliation.create_manifest(scope, now=1)
    monkeypatch.setattr("core.memory.dossiers.apply_operations", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")))
    result = history_reconciliation._commit_derived_source_batch(
        scope, "mid_term", {"source_revision": "unused", "next_offset": 0},
        backup={"verified": True}, batch_size=10,
    )
    assert result["status"] == "retryable_failed"
    listed = dossiers.list_source_items(scope, store_kind="mid_term")
    assert listed["items"][0]["status"] == "retryable_failed"
    assert listed["items"][0]["operation_id"] == ""
    assert dossiers.operation_receipt(scope, "a" * 32) is None


def test_expired_running_batch_is_reconciled_before_retry(sandbox):
    from core.memory import dossiers, history_reconciliation
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("history-reconcile-owner", TEST_CHAR_ID)
    dossiers.seed_source_items(scope, [
        {"store_kind": "event", "source_id": "hist-lease", "source_revision": "r1",
         "ingest_sequence": 1, "input_digest": "d1"},
    ], now=1)
    first = history_reconciliation.claim_batch(scope, batch_size=1, store_kind="event", now=10)
    assert first["count"] == 1
    recovered = dossiers.reconcile_source_item_leases(scope, now=10 + 901)
    assert recovered["released"] == 1
    retry = history_reconciliation.claim_batch(scope, batch_size=1, store_kind="event", now=920)
    assert retry["count"] == 1
    assert retry["items"][0]["source_id"] == "hist-lease"
    assert retry["reconciled"]["released"] == 0
