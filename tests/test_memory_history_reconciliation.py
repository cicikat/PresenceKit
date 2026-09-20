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
    assert result["schema_version"] == "memory-history-inventory.v2"
    assert result["read_only"] is True
    assert result["model_calls"] == 0
    assert result["scope"]["uid_digest"] != scope.uid
    assert "secret evidence" not in str(result)
    assert {item["store_kind"] for item in result["items"]} >= {"event_store", "event_log", "episodic"}


def test_inventory_separates_denominators_and_first_night_range(sandbox):
    import json
    from core.memory.history_reconciliation import FIRST_NIGHT_WINDOW_SECONDS, build_inventory
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    from core.memory.event_store import append_event

    now = 2_000_000_000.0
    scope = MemoryScope.reality_scope("inventory-range-owner", TEST_CHAR_ID)
    secret = "PRIVATE_SOURCE_BODY_259A"
    assert append_event(scope, {
        "event_id": "inventory-recent", "turn_id": "inventory-recent", "seq": 1,
        "occurred_at": now - 3 * 86_400, "ingested_at": now - 3_600, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "chat",
        "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": secret, "memory_text": secret,
    }).ok
    assert append_event(scope, {
        "event_id": "inventory-stale", "turn_id": "inventory-stale", "seq": 2,
        "occurred_at": now - FIRST_NIGHT_WINDOW_SECONDS - 86_400,
        "ingested_at": now - FIRST_NIGHT_WINDOW_SECONDS - 3_600,
        "uid": scope.uid, "char_id": scope.character_id, "realm": "reality",
        "kind": "chat", "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": secret, "memory_text": secret,
    }).ok
    mid_path = resolve_path(scope, "mid_term")
    mid_path.parent.mkdir(parents=True, exist_ok=True)
    mid_path.write_text(json.dumps({"events": [
        {"mid_id": "mid-legacy", "summary": secret, "occurred_at": now - 10_000, "source_event_ids": []},
        {"mid_id": "mid-linked", "summary": secret, "occurred_at": now - FIRST_NIGHT_WINDOW_SECONDS - 10,
         "source_event_ids": ["inventory-stale"]},
    ]}), encoding="utf-8")
    event_log = resolve_path(scope, "event_log")
    event_log.mkdir(parents=True, exist_ok=True)
    (event_log / "2020-01-01.md").write_text("# day\n", encoding="utf-8")
    (event_log / "full_log.md").write_text(secret, encoding="utf-8")
    digest = resolve_path(scope, "memory_digest")
    digest.write_text("old digest", encoding="utf-8")
    result = build_inventory(scope.uid, scope.character_id, now=now)
    by_kind = {item["store_kind"]: item for item in result["items"]}
    assert result["denominators"]["evidence_rows"] == 2
    assert result["denominators"]["derived_summaries"] >= 2
    assert result["denominators"]["archive_files"] >= 2
    assert result["denominators"]["independent_experiences"] == "unknown"
    assert by_kind["event_store"]["first_night_candidates"] == 1
    assert by_kind["event_store"]["remaining_history"] == 1
    assert by_kind["event_store"]["late_arrivals"] == 1
    assert by_kind["mid_term"]["legacy_unknown"] == 1
    assert by_kind["event_log"]["old_format"] == 1
    assert result["ranges"]["first_night_candidates"] >= 1
    assert result["ranges"]["remaining_history"] >= 1
    assert "mid_term:legacy_unknown" in result["ranges"]["unknown"]
    assert result["sample_class_hits"]["missing_lineage"] is True
    assert result["sample_class_hits"]["late_arrival"] is True
    assert result["archives"]["memory_digest"]["exists"] is True
    assert result["watermark"]["first_night_cutoff"] == now - FIRST_NIGHT_WINDOW_SECONDS
    assert secret not in str(result)
    assert str(mid_path) not in str(result)


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


def _admit(scope, monkeypatch, *, now=1_700_000_000.0, stop_at_local="07:00", **overrides):
    from core.memory import history_reconciliation
    from core.memory.consolidation_worker import _DEFAULTS

    monkeypatch.setattr("core.memory.consolidation_worker.config", lambda: {
        **_DEFAULTS, "enabled": False, "grant_revision": 1,
        "daily_call_budget": 8, "daily_token_budget": 9600, "daily_wall_seconds": 600,
    })
    kwargs = {
        "go_live_date": "2099-01-02",
        "timezone_name": "Asia/Shanghai",
        "grant_revision": 1,
        "preset": "便宜小模型grok-see",
        "daily_call_budget": 8,
        "daily_token_budget": 9600,
        "daily_cost_budget": 1.0,
        "stop_at_local": stop_at_local,
        "restore_strategy": "verified_snapshot_rollback",
        "preconditions": {"brief_258_b_e": True, "recovery_drill": True, "spot_check": True},
        "now": now,
    }
    kwargs.update(overrides)
    return history_reconciliation.admit_first_night(scope, **kwargs)


def test_first_night_runner_requires_admission_before_verified_backup(sandbox, monkeypatch, tmp_path):
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

    monkeypatch.setattr(history_reconciliation, "apply_batch", fake_apply)
    blocked = __import__("asyncio").run(history_reconciliation.run_first_night(
        scope, backup_snapshot=tmp_path / "snapshot", manifest_revision=manifest["manifest_revision"],
        stop_at=0,
    ))
    assert blocked == {"status": "deferred", "reason": "not_admitted"}
    assert calls["count"] == 0
    assert history_reconciliation.status(scope)["admission"] is None


def test_first_night_runner_stops_at_cutoff_and_uses_verified_gate(sandbox, monkeypatch, tmp_path):
    from core.memory import history_reconciliation
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("first-night-owner", TEST_CHAR_ID)
    manifest = history_reconciliation.create_manifest(scope, now=1)
    history_reconciliation.freeze_manifest(scope, manifest_revision=manifest["manifest_revision"])
    state = history_reconciliation.read_state(scope)
    state["last_calibration"] = {
        "unlimited_run_allowed": False, "budget_unset": False, "isolated": True,
    }
    history_reconciliation.safe_write_json(history_reconciliation._state_path(scope), state, keep_bak=True)
    admission = _admit(scope, monkeypatch, now=1_700_000_000.0)
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
    assert admission["admitted"] is True
    assert history_reconciliation.status(scope)["last_closeout"]["status"] == "stopped"
    assert history_reconciliation.status(scope)["admission"]["go_live_date"] == "2099-01-02"


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


def test_first_night_priority_orders_corrections_before_recent_and_keeps_cold_share(sandbox):
    from core.memory import dossiers, history_reconciliation
    from core.memory.event_store import append_event
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    import json
    import uuid

    now = 2_000_000_000.0
    scope = MemoryScope.reality_scope("priority-owner", TEST_CHAR_ID)
    secret = "PRIVATE_SOURCE_BODY_259C"
    assert append_event(scope, {
        "event_id": "priority-old", "turn_id": "priority-old", "seq": 1,
        "occurred_at": now - history_reconciliation.FIRST_NIGHT_WINDOW_SECONDS - 86_400,
        "ingested_at": now - history_reconciliation.FIRST_NIGHT_WINDOW_SECONDS - 3_600,
        "uid": scope.uid, "char_id": scope.character_id, "realm": "reality",
        "kind": "chat", "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": secret, "memory_text": secret,
    }).ok
    assert append_event(scope, {
        "event_id": "priority-recent", "turn_id": "priority-recent", "seq": 2,
        "occurred_at": now - 3_600, "ingested_at": now - 1_800,
        "uid": scope.uid, "char_id": scope.character_id, "realm": "reality",
        "kind": "chat", "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": secret, "memory_text": secret,
    }).ok
    assert append_event(scope, {
        "event_id": "priority-correction", "turn_id": "priority-correction", "seq": 3,
        "occurred_at": now - history_reconciliation.FIRST_NIGHT_WINDOW_SECONDS - 10_000,
        "ingested_at": now - history_reconciliation.FIRST_NIGHT_WINDOW_SECONDS - 9_000,
        "uid": scope.uid, "char_id": scope.character_id, "realm": "reality",
        "kind": "chat", "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": secret, "memory_text": secret,
        "correction_of": "priority-old",
    }).ok
    assert append_event(scope, {
        "event_id": "priority-stale", "turn_id": "priority-stale", "seq": 4,
        "occurred_at": now - history_reconciliation.FIRST_NIGHT_WINDOW_SECONDS - 200_000,
        "ingested_at": now - history_reconciliation.FIRST_NIGHT_WINDOW_SECONDS - 199_000,
        "uid": scope.uid, "char_id": scope.character_id, "realm": "reality",
        "kind": "chat", "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": secret, "memory_text": secret,
    }).ok
    dossier_id = uuid.uuid4().hex
    occurrence_id = uuid.uuid4().hex
    dossiers.apply_operations(scope, [
        {"action": "create_dossier", "dossier_id": dossier_id, "title": "Active Theme",
         "aliases": [], "description": ""},
        {"action": "create_occurrence", "occurrence_id": occurrence_id, "participants": [],
         "time_certainty": "unknown", "assertion_kind": "user_stated",
         "evidence": [{"reference_kind": "event", "source_id": "priority-old",
                       "source_revision": "1"}]},
        {"action": "attach_occurrence", "dossier_id": dossier_id, "occurrence_id": occurrence_id,
         "expected_revision": 1},
    ], operation_id=uuid.uuid4().hex, actor="character", chain="owner_chat")
    storyline = resolve_path(scope, "storyline")
    storyline.parent.mkdir(parents=True, exist_ok=True)
    storyline.write_text(json.dumps({"arcs": [{"arc_id": "arc-cold", "title": "cold",
                                               "updated_at": now - history_reconciliation.FIRST_NIGHT_WINDOW_SECONDS - 1}]}),
                         encoding="utf-8")
    manifest = history_reconciliation.create_manifest(scope, now=now)
    freeze = history_reconciliation.freeze_manifest(scope, manifest_revision=manifest["manifest_revision"])
    claimed = history_reconciliation.claim_batch(scope, batch_size=4, now=now)
    classes = [item["priority_class"] for item in claimed["items"] if item["store_kind"] == "event"]
    assert classes[:3] == ["correction", "active_theme", "recent"]
    assert freeze["first_night_range"]["cold_theme_share"] == 0.25
    assert freeze["first_night_range"]["source_item_priority_counts"]["remaining"] >= 1
    leftover = history_reconciliation.claim_batch(scope, batch_size=8, now=now + 1)
    leftover_classes = [item["priority_class"] for item in leftover["items"]]
    assert leftover_classes.count("remaining") >= 1
    snapshot = history_reconciliation.status(scope)
    assert snapshot["first_night_range"]["priority_order"][0] == "correction"
    assert secret not in str(freeze)
    assert secret not in str(claimed)


def test_isolated_calibration_records_latency_tokens_and_does_not_apply(sandbox, monkeypatch):
    from core.memory import dossiers, history_reconciliation
    from core.memory.event_store import append_event
    from core.memory.scope import MemoryScope
    import asyncio
    import json

    scope = MemoryScope.reality_scope("calibrate-owner", TEST_CHAR_ID)
    secret = "PRIVATE_SOURCE_BODY_259C2"
    assert append_event(scope, {
        "event_id": "calibrate-event", "turn_id": "calibrate-event", "seq": 1,
        "occurred_at": 10.0, "ingested_at": 11.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "chat",
        "actor": "user", "channel": "test", "source": "fixture",
        "visible_text": secret, "memory_text": secret,
    }).ok
    from core.memory.consolidation_worker import _DEFAULTS
    monkeypatch.setattr("core.memory.consolidation_worker.config", lambda: {
        **_DEFAULTS,
        "enabled": False, "daily_token_budget": 9600, "daily_call_budget": 8,
        "daily_wall_seconds": 600, "max_input_chars": 24000, "max_tokens_per_call": 1200,
    })
    monkeypatch.setattr(
        "core.memory.consolidation_worker._identity_context",
        lambda _char_id: ('{"name":"fixture"}', "identity-revision"),
    )
    calls = {"count": 0}

    async def fake_chat(messages, **_kwargs):
        calls["count"] += 1
        assert secret in messages[0]["content"]
        return json.dumps([{
            "action": "create_dossier", "title": "Duplicate Title", "aliases": [], "description": "",
        }, {
            "action": "create_dossier", "title": "Duplicate Title", "aliases": [], "description": "",
        }])

    result = asyncio.run(history_reconciliation.calibrate_side_chain(
        scope, sample_size=1, chat=fake_chat, now=20,
    ))
    assert result["isolated"] is True
    assert result["production_first_night"] is False
    assert result["model_calls"] == 1
    assert calls["count"] == 1
    assert result["samples"][0]["applied"] is False
    assert result["samples"][0]["input_tokens"] > 0
    assert result["rate_band"]["unlimited_run_allowed"] is False
    assert result["rate_band"]["budget_unset"] is False
    assert result["quality"]["hits"]["classification_fragmentation"] is True
    assert result["frozen"] is True
    assert result["first_night_range"]["priority_order"][0] == "correction"
    snapshot = history_reconciliation.status(scope)
    assert snapshot["last_calibration"]["model_calls"] == 1
    assert snapshot["last_calibration"]["unlimited_run_allowed"] is False
    assert secret not in str(snapshot["last_calibration"])
    assert dossiers.maintenance_checkpoint(scope) == 0


def test_calibration_quality_flags_feeling_as_fact_and_stale_conclusion():
    from core.memory import history_reconciliation

    quality = history_reconciliation._inspect_quality(
        [{"action": "create_occurrence", "assertion_kind": "user_stated",
          "character_feeling": True, "evidence": [{"source_id": "evt-1"}]}],
        [{"source_id": "evt-1", "priority_class": "recent"}],
        [{"matching_source_ids": ["evt-1"], "needs_recompute": 1}],
    )
    assert quality["hits"]["feeling_as_fact"] is True
    assert quality["hits"]["stale_conclusion"] is True
    empty_correction = history_reconciliation._inspect_quality(
        [], [{"source_id": "evt-2", "priority_class": "correction"}], [],
    )
    assert empty_correction["hits"]["false_discard"] is True


def test_rate_band_keeps_headroom_and_refuses_unset_budget():
    from core.memory import history_reconciliation

    measured = history_reconciliation._rate_band(
        [{"wall_seconds": 2.0, "input_tokens": 100, "output_tokens": 20, "retries": 0}],
        remaining_tokens=1200, daily_token_budget=9600, daily_call_budget=8, daily_wall_seconds=600,
    )
    assert measured["sample_calls"] == 1
    assert measured["unlimited_run_allowed"] is False
    assert measured["admitted_tokens_per_second"] < measured["measured_tokens_per_second"]
    unset = history_reconciliation._rate_band(
        [{"wall_seconds": 2.0, "input_tokens": 100, "output_tokens": 20, "retries": 0}],
        remaining_tokens=1200, daily_token_budget=0, daily_call_budget=8, daily_wall_seconds=600,
    )
    assert unset["budget_unset"] is True
    assert unset["unlimited_run_allowed"] is False
    assert unset["binding_limit"] == "budget_unset"
    empty = history_reconciliation._rate_band(
        [], remaining_tokens=1200, daily_token_budget=9600, daily_call_budget=8, daily_wall_seconds=600,
    )
    assert empty["binding_limit"] == "no_sample"
    assert empty["estimated_seconds"] is None


def test_admit_first_night_freezes_go_live_artifacts_without_running(sandbox, monkeypatch):
    from core.memory import history_reconciliation
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("admit-owner", TEST_CHAR_ID)
    manifest = history_reconciliation.create_manifest(scope, now=1)
    history_reconciliation.freeze_manifest(scope, manifest_revision=manifest["manifest_revision"])
    state = history_reconciliation.read_state(scope)
    state["last_calibration"] = {"unlimited_run_allowed": False, "budget_unset": False}
    history_reconciliation.safe_write_json(history_reconciliation._state_path(scope), state, keep_bak=True)
    admission = _admit(scope, monkeypatch, now=1_700_000_000.0)
    snapshot = history_reconciliation.status(scope)
    assert admission["schema_version"] == "memory-history-admission.v1"
    assert admission["admitted"] is True
    assert admission["production_first_night"] is False
    assert admission["go_live_date"] == "2099-01-02"
    assert admission["timezone"] == "Asia/Shanghai"
    assert admission["stop_at_local"] == "07:00"
    assert admission["stop_at"] > admission["admitted_at"]
    assert admission["hard_budgets"]["daily_call_budget"] == 8
    assert admission["hard_budgets"]["daily_token_budget"] == 9600
    assert admission["hard_budgets"]["daily_cost_budget"] == 1.0
    assert admission["restore_strategy"] == "verified_snapshot_rollback"
    assert admission["preconditions"] == {
        "brief_258_b_e": True, "recovery_drill": True, "spot_check": True,
    }
    assert snapshot["admission"]["manifest_revision"] == manifest["manifest_revision"]
    assert snapshot["last_closeout"] is None


def test_admit_first_night_rejects_incomplete_preconditions_and_unset_budget(sandbox, monkeypatch):
    from core.memory import history_reconciliation
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("admit-block", TEST_CHAR_ID)
    try:
        history_reconciliation.admit_first_night(
            scope, go_live_date="2099-01-02", timezone_name="Asia/Shanghai",
            grant_revision=1, daily_call_budget=8, daily_token_budget=9600,
            daily_cost_budget=1.0,
            preconditions={"brief_258_b_e": True, "recovery_drill": True, "spot_check": True},
        )
        raise AssertionError("expected freeze before admit")
    except ValueError as exc:
        assert str(exc) == "manifest_not_frozen"
    manifest = history_reconciliation.create_manifest(scope, now=1)
    history_reconciliation.freeze_manifest(scope, manifest_revision=manifest["manifest_revision"])
    try:
        _admit(scope, monkeypatch)
        raise AssertionError("expected calibration before admit")
    except ValueError as exc:
        assert str(exc) == "calibration_incomplete"
    state = history_reconciliation.read_state(scope)
    state["last_calibration"] = {"unlimited_run_allowed": False, "budget_unset": True}
    history_reconciliation.safe_write_json(history_reconciliation._state_path(scope), state, keep_bak=True)
    try:
        _admit(scope, monkeypatch)
        raise AssertionError("expected budget before admit")
    except ValueError as exc:
        assert str(exc) == "budget_unset"
    state["last_calibration"] = {"unlimited_run_allowed": False, "budget_unset": False}
    history_reconciliation.safe_write_json(history_reconciliation._state_path(scope), state, keep_bak=True)
    try:
        _admit(scope, monkeypatch, preconditions={"brief_258_b_e": True, "recovery_drill": True, "spot_check": False})
        raise AssertionError("expected complete preconditions")
    except ValueError as exc:
        assert str(exc) == "preconditions_incomplete"
    try:
        _admit(scope, monkeypatch, grant_revision=9)
        raise AssertionError("expected matching grant")
    except ValueError as exc:
        assert str(exc) == "grant_revision_mismatch"
    try:
        _admit(scope, monkeypatch, timezone_name="Not/AZone")
        raise AssertionError("expected valid timezone")
    except ValueError as exc:
        assert str(exc) == "invalid_timezone"
