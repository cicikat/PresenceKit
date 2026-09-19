from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fixtures.public_assets import TEST_CHAR_ID

SECRET = "memory-event-migration-admin-secret"


def _scope(uid: str):
    from core.memory.scope import MemoryScope

    return MemoryScope.reality_scope(uid, TEST_CHAR_ID)


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: SECRET)
    from admin.admin_server import app

    return TestClient(app, raise_server_exceptions=False)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {SECRET}"}


def _legacy_log(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "2026-08-01.md").write_text(
        """## 12:34
**用户**：第一行
第二行
> speaker:user turn_id:old-a
---
## 12:34
**用户**：第一行
第二行
> speaker:user turn_id:old-a
---
## 12:35
**任意显示名**：不可信角色
> speaker:user
---
""",
        encoding="utf-8",
    )
    return root


def test_dry_run_is_read_only_and_duplicate_blocks_are_deterministic(sandbox, tmp_path):
    from core.memory.event_migration import scan_legacy
    from core.memory.path_resolver import resolve_path

    scope = _scope("migration-dry-run")
    plan = scan_legacy(scope, source_dir=_legacy_log(tmp_path / "legacy"))
    assert plan["total"] == 2
    assert plan["parsed"] == 1
    assert plan["malformed"] == plan["legacy_unknown"] == 1
    assert plan["duplicate"] == 1
    assert plan["sources"] == {"current": 0, "legacy": 0, "override": 3}
    assert not resolve_path(scope, "event_store").exists()
    assert not resolve_path(scope, "event_migration_state").exists()


def test_migration_is_batched_idempotent_and_recovers_after_failure(sandbox, tmp_path, monkeypatch):
    from core.memory import event_migration, event_query, event_store

    scope = _scope("migration-recovery")
    plan = event_migration.scan_legacy(scope, source_dir=_legacy_log(tmp_path / "legacy"))
    failed = event_store.AppendResult(False, False, plan["entries"][0].event_id, "database_error")
    original = event_migration.event_store.append_event
    monkeypatch.setattr(event_migration.event_store, "append_event", lambda *_args: failed)
    paused = event_migration.apply_batch(scope, plan, batch_size=1, backup={"verified": True})
    assert paused["status"] == "paused"
    assert paused["next_offset"] == 0
    assert paused["failed"] == 1

    monkeypatch.setattr(event_migration.event_store, "append_event", original)
    first = event_migration.apply_batch(scope, plan, batch_size=1, backup={"verified": True})
    assert first["next_offset"] == 1
    completed = event_migration.apply_batch(scope, plan, batch_size=10, backup={"verified": True})
    assert completed["status"] == "completed"
    assert completed["written"] == 2
    assert completed["duplicate"] == 1
    unknown = next(entry for entry in plan["entries"] if entry.kind == "legacy_unknown")
    event = event_query.get_event(scope, unknown.event_id, source="legacy_unknown")
    assert event is not None
    assert event["raw_text"] == ""
    assert event["visible_text"].startswith("[legacy_unknown:")


def test_migration_conflict_is_counted_without_overwrite(sandbox, tmp_path):
    from core.memory import event_migration, event_store

    scope = _scope("migration-conflict")
    plan = event_migration.scan_legacy(scope, source_dir=_legacy_log(tmp_path / "legacy"))
    entry = plan["entries"][0]
    assert event_store.append_event(scope, {**entry.event(), "memory_text": "different evidence"}).inserted
    status = event_migration.apply_batch(scope, plan, batch_size=10, backup={"verified": True})
    # The deterministic plan has collapsed the repeated legacy block.  It may
    # not overwrite an occupied ID with different provenance.
    assert status["conflict"] == 1
    assert status["duplicate"] == 1


def test_conflicts_can_be_preserved_as_isolated_evidence(sandbox, tmp_path):
    from core.memory import event_migration, event_store

    scope = _scope("migration-conflict-preserve")
    plan = event_migration.scan_legacy(scope, source_dir=_legacy_log(tmp_path / "legacy-preserve"))
    entry = plan["entries"][0]
    assert event_store.append_event(scope, {**entry.event(), "memory_text": "different evidence"}).inserted
    plan = event_migration.scan_legacy(scope, source_dir=_legacy_log(tmp_path / "legacy-preserve"))
    result = event_migration.preserve_conflicts(scope, plan, backup={"verified": True})
    assert result["status"] == "completed"
    assert result["conflicts"] == 1
    assert result["preserved"] == 1


def test_migration_counts_existing_live_canonical_event_without_duplicate(sandbox, tmp_path):
    from core.memory import event_migration, event_store

    root = tmp_path / "already-live"
    root.mkdir()
    (root / "2026-08-05.md").write_text(
        "## 09:30\n**用户**：已经在线双写\n> speaker:user turn_id:live-turn\n---\n",
        encoding="utf-8",
    )
    scope = _scope("migration-already-live")
    plan = event_migration.scan_legacy(scope, source_dir=root)
    assert event_store.append_event(scope, {
        **plan["entries"][0].event(), "source": "user_chat",
    }).inserted
    status = event_migration.apply_batch(scope, plan, batch_size=10, backup={"verified": True})
    assert status["already_live"] == 1 and status["written"] == 0


def test_migration_preserves_isolated_source_and_unknown_trigger_time(sandbox, tmp_path):
    from core.memory import event_migration

    root = tmp_path / "legacy-sources"
    root.mkdir()
    (root / "2026-08-04.md").write_text(
        "**角色**：定时问候\n> speaker:assistant trigger:daily_check\n---\n"
        "## 11:00\n**用户**：外部资料\n> speaker:user turn_id:web-turn source:web\n---\n",
        encoding="utf-8",
    )
    plan = event_migration.scan_legacy(_scope("migration-source-policy"), source_dir=root)
    web = next(entry for entry in plan["entries"] if entry.turn_id == "web-turn")
    trigger = next(entry for entry in plan["entries"] if entry.trigger)
    assert web.event_id == "web-turn:user" and web.source == "web"
    assert trigger.kind == "legacy_unknown" and trigger.occurred_at == 0.0 and trigger.unknown_time
    assert plan["source_isolated"] == 1
    assert plan["assistant_trigger_unknown_time"] == 1


def test_migration_parses_each_message_actor_and_turn_id(sandbox, tmp_path):
    from core.memory.event_migration import scan_legacy

    root = tmp_path / "legacy"
    root.mkdir()
    (root / "2026-08-02.md").write_text(
        "## 09:30\n**用户**：早上好\n> speaker:user turn_id:turn-9\n"
        "**角色**：早，今天想做什么？\n> speaker:assistant turn_id:turn-9\n---\n",
        encoding="utf-8",
    )
    plan = scan_legacy(_scope("migration-actors"), source_dir=root)
    assert plan["parsed"] == 2
    user, assistant = plan["entries"]
    assert (user.actor, user.turn_id, user.seq) == ("user", "turn-9", 0)
    assert (assistant.actor, assistant.turn_id, assistant.seq) == ("assistant", "turn-9", 1)
    assert user.text == "早上好"
    assert assistant.text == "早，今天想做什么？"


def test_migration_default_scans_current_and_legacy_with_inventory_only_assets(sandbox):
    from core.memory.event_migration import current_event_log_dir, legacy_event_log_dir, scan_legacy
    from core.memory.path_resolver import resolve_path

    scope = _scope("migration-union")
    current = current_event_log_dir(scope)
    legacy = legacy_event_log_dir(scope)
    current.mkdir(parents=True)
    legacy.mkdir(parents=True)
    body = "## 10:00\n**用户**：同一条\n> speaker:user turn_id:turn-union\n---\n"
    (current / "2026-08-03.md").write_text(body, encoding="utf-8")
    (legacy / "2026-08-03.md").write_text(body, encoding="utf-8")
    resolve_path(scope, "episodic").write_text("[]", encoding="utf-8")

    plan = scan_legacy(scope)
    assert plan["parsed"] == 1
    assert plan["duplicate"] == 1
    assert plan["sources"]["current"] == plan["sources"]["legacy"] == 1
    assert plan["artifacts"]["episodic"]["mode"] == "inventory_only"
    assert plan["artifacts"]["episodic"]["current_items"] == 0


def test_migration_same_identity_different_source_is_conflict_and_order_stable(sandbox):
    from core.memory import event_migration

    current = _scope("migration-source-conflict")
    current_dir = event_migration.current_event_log_dir(current)
    legacy_dir = event_migration.legacy_event_log_dir(current)
    current_dir.mkdir(parents=True, exist_ok=True)
    legacy_dir.mkdir(parents=True, exist_ok=True)
    body = "## 10:00\n**用户**：same event\n> speaker:user turn_id:same-turn\n---\n"
    (current_dir / "2026-08-06.md").write_text(body, encoding="utf-8")
    (legacy_dir / "2026-08-06.md").write_text(
        body.replace("turn_id:same-turn", "turn_id:same-turn source:web"), encoding="utf-8",
    )
    first = event_migration.scan_legacy(current)
    second = event_migration.scan_legacy(current)
    assert first["plan_conflict"] == second["plan_conflict"] == 1
    assert first["conflict"] == second["conflict"] == 1
    assert first["would_write"] == second["would_write"] == 0


def test_migration_dry_run_reports_schema_mismatch_without_would_write(sandbox, tmp_path, monkeypatch):
    from core.memory import event_migration

    root = _legacy_log(tmp_path / "migration-schema-root")
    scope = _scope("migration-schema-mismatch")
    monkeypatch.setattr(
        event_migration.event_store, "migration_evidence_status",
        lambda *_args, **_kwargs: ("schema_mismatch", None),
    )
    plan = event_migration.scan_legacy(scope, source_dir=root)
    assert plan["comparison_status"] == "schema_mismatch"
    assert plan["would_write"] == 0
    assert plan["ledger_classifications"][plan["entries"][0].event_id] == "schema_mismatch"


def test_migration_dry_run_report_is_json_serializable_and_conflicts_are_stable(sandbox, tmp_path):
    from core.memory import event_migration

    scope = _scope("migration-json-report")
    plan = event_migration.scan_legacy(scope, source_dir=_legacy_log(tmp_path / "json-report"))
    report = {key: value for key, value in plan.items() if key != "entries"}
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert json.loads(encoded)["conflicted_event_ids"] == sorted(plan["conflicted_event_ids"])


def test_migration_mixed_comparison_keeps_indeterminate_status(sandbox, tmp_path, monkeypatch):
    from core.memory import event_migration

    statuses = iter((("not_found", None), ("schema_mismatch", None)))
    monkeypatch.setattr(
        event_migration.event_store, "migration_evidence_status",
        lambda *_args, **_kwargs: next(statuses),
    )
    plan = event_migration.scan_legacy(
        _scope("migration-mixed-status"), source_dir=_legacy_log(tmp_path / "mixed-status"),
    )
    assert plan["comparison_status"] == "schema_mismatch"
    assert plan["indeterminate"] is True
    assert plan["indeterminate_statuses"] == ["schema_mismatch"]
    assert plan["would_write"] == 0


def test_migration_locked_status_is_explicit_and_does_not_advance(sandbox, tmp_path, monkeypatch):
    from core.memory import event_migration

    scope = _scope("migration-locked")
    plan = event_migration.scan_legacy(scope, source_dir=_legacy_log(tmp_path / "locked"))
    monkeypatch.setattr(
        event_migration.event_store, "migration_evidence_status",
        lambda *_args, **_kwargs: ("locked", None),
    )
    result = event_migration.apply_batch(scope, plan, batch_size=10, backup={"verified": True})
    assert result["status"] == "paused"
    assert result["next_offset"] == 0
    assert result["last_error"] == "locked"


def test_migration_status_retains_content_free_sources_and_inventory(sandbox, tmp_path):
    from core.memory import event_migration

    scope = _scope("migration-observe")
    plan = event_migration.scan_legacy(scope, source_dir=_legacy_log(tmp_path / "legacy"))
    state = event_migration.apply_batch(scope, plan, batch_size=100, backup={"verified": True})
    assert state["sources"]["override"] == 3
    assert state["artifacts"]["short_term"]["mode"] == "inventory_only"


def test_migration_backup_helper_requires_verification(monkeypatch, tmp_path):
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_memory_events.py"
    spec = importlib.util.spec_from_file_location("migration_script_fixture", module_path)
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(script, "create_snapshot", lambda *_args, **_kwargs: {
        "backup_id": "backup-fixture", "file_count": 3, "protection_mode": "protected_volume",
    })
    monkeypatch.setattr(script, "verify_snapshot", lambda _path: {"ok": True, "file_count": 3})
    checksum = "a" * 64
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "manifest.sha256").write_text(checksum, encoding="ascii")
    assert script._backup(snapshot) == {
        "backup_id": "backup-fixture", "file_count": 3, "verified": True, "manifest_sha256": checksum,
        "protection_mode": "protected_volume",
    }


def test_tombstone_retains_edges_and_hides_payload_in_admin_views(sandbox, monkeypatch):
    from core.memory import event_query, event_store, lineage
    from core.memory.path_resolver import resolve_path
    from core.safe_write import safe_write_json

    scope = _scope("migration-tombstone")
    assert event_store.append_event(scope, {
        "event_id": "retain-edge-a", "occurred_at": 1, "realm": "reality", "kind": "owner_chat",
        "actor": "user", "raw_text": "forget me", "visible_text": "forget me", "memory_text": "forget me",
        "media_refs_json": [{"filename": "private.png"}],
    }).inserted
    assert event_store.append_event(scope, {
        "event_id": "retain-edge-b", "occurred_at": 2, "realm": "reality", "kind": "owner_chat",
        "actor": "assistant", "raw_text": "keep me", "visible_text": "keep me", "memory_text": "keep me",
        "reply_to_event_id": "retain-edge-a",
    }).inserted

    client = _client(monkeypatch)
    response = client.delete("/memory-events/retain-edge-a", params={"uid": scope.uid, "char_id": TEST_CHAR_ID, "realm": "reality"}, headers=_headers())
    assert response.status_code == 200
    assert response.json()["edges"] == "retained"
    again = client.delete("/memory-events/retain-edge-a", params={"uid": scope.uid, "char_id": TEST_CHAR_ID, "realm": "reality"}, headers=_headers())
    assert again.status_code == 200 and again.json()["changed"] is False

    tombstoned = event_query.get_event(scope, "retain-edge-a")
    assert tombstoned and tombstoned["tombstoned"] is True
    assert tombstoned["raw_text"] == tombstoned["visible_text"] == tombstoned["memory_text"] == ""
    assert tombstoned["media_refs"] == []
    related = event_query.related(scope, "retain-edge-b", cursor="", limit=10)
    assert related and related["items"][0]["event"]["tombstoned"] is True
    assert safe_write_json(resolve_path(scope, "episodic"), [{
        "id": "derived-retains-lineage", "source_event_ids": ["retain-edge-a"],
    }])
    derived = lineage.resolve_episode(scope.uid, "derived-retains-lineage", char_id=TEST_CHAR_ID)
    assert derived and derived["lineage_status"] == "resolved"
    assert derived["events"][0]["tombstoned"] is True

    migration_status = client.get("/observability/memory-event-migration", params={"uid": scope.uid, "char_id": TEST_CHAR_ID}, headers=_headers())
    assert migration_status.status_code == 200
    assert "raw_text" not in migration_status.text
    old_delete = client.delete(f"/memory/{scope.uid}/event-log/2026-08-01", params={"char_id": TEST_CHAR_ID}, headers=_headers())
    assert old_delete.status_code == 409
    assert old_delete.json()["detail"]["code"] == "physical_delete_disabled_pending_owner_policy"
