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
    assert history_reconciliation.status(scope)["counts"]["deferred"] >= 1
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
