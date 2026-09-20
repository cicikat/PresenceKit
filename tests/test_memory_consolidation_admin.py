from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fixtures.public_assets import TEST_CHAR_ID


def _event(scope, event_id: str = "admin-dossier-event"):
    from core.memory.event_store import append_event

    assert append_event(scope, {
        "event_id": event_id, "turn_id": event_id, "seq": 1,
        "occurred_at": 100.0, "ingested_at": 101.0,
        "uid": scope.uid, "char_id": scope.character_id, "realm": "reality",
        "kind": "owner_chat", "actor": "user", "channel": "desktop",
        "source": "fixture", "visible_text": "private dossier evidence",
        "memory_text": "private dossier evidence", "redaction_state": "scrubbed",
    }).ok


def _seed(scope):
    from core.memory.dossiers import apply_operations

    _event(scope)
    dossier_id = uuid.uuid4().hex
    apply_operations(scope, [{"action": "create_dossier", "dossier_id": dossier_id,
                              "title": "Admin fixture", "description": "private summary"}],
                     operation_id=uuid.uuid4().hex, actor="character", chain="owner_chat")
    return dossier_id


def _client_with_tokens(sandbox):
    import yaml

    from admin import token_registry
    from admin.routers.memory_consolidation import router

    values = {
        "state-token": ["state.read"],
        "memory-token": ["memory.read"],
        "admin-token": ["admin"],
    }
    path = sandbox.auth_tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"tokens": [
        {"label": name, "hash": token_registry.hash_token(name), "scopes": scopes}
        for name, scopes in values.items()
    ]}), encoding="utf-8")
    token_registry._records = None
    token_registry._mtime = None
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_history_source_items_require_memory_read(sandbox):
    from core.memory import history_reconciliation
    from core.memory.event_store import append_event
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("admin-source-item", TEST_CHAR_ID)
    assert append_event(scope, {
        "event_id": "admin-source-event", "turn_id": "admin-source-turn", "seq": 1,
        "occurred_at": 1.0, "ingested_at": 2.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "owner_chat",
        "actor": "user", "channel": "desktop", "source": "fixture",
        "visible_text": "private dossier evidence", "memory_text": "private dossier evidence",
        "redaction_state": "scrubbed",
    }).ok
    history_reconciliation.create_manifest(scope, now=1)
    client = _client_with_tokens(sandbox)
    path = f"/memory/history-source-items?uid={scope.uid}&char_id={scope.character_id}&store_kind=event"
    denied = client.get(path, headers=_headers("state-token"))
    allowed = client.get(path, headers=_headers("memory-token"))
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["items"][0]["source_id"] == "admin-source-event"
    assert "private dossier evidence" not in allowed.text


def test_calibrate_control_is_isolated_and_redacted(sandbox, monkeypatch):
    from core.memory.event_store import append_event
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("admin-calibrate", TEST_CHAR_ID)
    secret = "private dossier evidence"
    assert append_event(scope, {
        "event_id": "admin-calibrate-event", "turn_id": "admin-calibrate-event", "seq": 1,
        "occurred_at": 1.0, "ingested_at": 2.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "owner_chat",
        "actor": "user", "channel": "desktop", "source": "fixture",
        "visible_text": secret, "memory_text": secret, "redaction_state": "scrubbed",
    }).ok

    async def fake_calibrate(scope_arg, **kwargs):
        assert kwargs.get("sample_size") == 2
        return {
            "isolated": True,
            "production_first_night": False,
            "sample_size": 1,
            "model_calls": 1,
            "samples": [{"applied": False, "input_tokens": 12, "output_tokens": 1, "retries": 0}],
            "quality": {"hits": {"classification_fragmentation": False}},
            "rate_band": {"unlimited_run_allowed": False, "budget_unset": False},
            "frozen": True,
            "first_night_range": {"priority_order": ["correction", "active_theme", "recent", "remaining"]},
        }

    monkeypatch.setattr("core.memory.history_reconciliation.calibrate_side_chain", fake_calibrate)
    client = _client_with_tokens(sandbox)
    denied = client.post(
        "/memory-history-reconciliation/control",
        json={"action": "calibrate", "uid": scope.uid, "char_id": scope.character_id, "sample_size": 2},
        headers=_headers("state-token"),
    )
    allowed = client.post(
        "/memory-history-reconciliation/control",
        json={"action": "calibrate", "uid": scope.uid, "char_id": scope.character_id, "sample_size": 2},
        headers=_headers("admin-token"),
    )
    assert denied.status_code == 403
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["isolated"] is True
    assert payload["production_first_night"] is False
    assert payload["samples"][0]["applied"] is False
    assert secret not in allowed.text


def test_admit_control_requires_admin_and_does_not_run(sandbox, monkeypatch):
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("admin-admit", TEST_CHAR_ID)

    def fake_admit(scope_arg, **kwargs):
        assert kwargs["go_live_date"] == "2099-01-02"
        assert kwargs["timezone_name"] == "Asia/Shanghai"
        assert kwargs["preconditions"]["spot_check"] is True
        return {
            "admitted": True,
            "production_first_night": False,
            "go_live_date": "2099-01-02",
            "timezone": "Asia/Shanghai",
            "restore_strategy": "verified_snapshot_rollback",
        }

    monkeypatch.setattr("core.memory.history_reconciliation.admit_first_night", fake_admit)
    client = _client_with_tokens(sandbox)
    denied = client.post(
        "/memory-history-reconciliation/control",
        json={"action": "admit", "uid": scope.uid, "char_id": scope.character_id,
              "go_live_date": "2099-01-02", "timezone": "Asia/Shanghai",
              "grant_revision": 1, "daily_call_budget": 8, "daily_token_budget": 9600,
              "daily_cost_budget": 1, "preconditions": {
                  "brief_258_b_e": True, "recovery_drill": True, "spot_check": True,
              }},
        headers=_headers("state-token"),
    )
    allowed = client.post(
        "/memory-history-reconciliation/control",
        json={"action": "admit", "uid": scope.uid, "char_id": scope.character_id,
              "go_live_date": "2099-01-02", "timezone": "Asia/Shanghai",
              "grant_revision": 1, "daily_call_budget": 8, "daily_token_budget": 9600,
              "daily_cost_budget": 1, "preconditions": {
                  "brief_258_b_e": True, "recovery_drill": True, "spot_check": True,
              }},
        headers=_headers("admin-token"),
    )
    assert denied.status_code == 403
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["admitted"] is True
    assert payload["production_first_night"] is False


def test_observability_is_metadata_only_and_scope_protected(sandbox):
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("admin-owner", TEST_CHAR_ID)
    _seed(scope)
    client = _client_with_tokens(sandbox)
    response = client.get(
        f"/observability/memory-consolidation?uid={scope.uid}&char_id={scope.character_id}",
        headers=_headers("state-token"),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["scope_status"]["backlog"] == 1
    assert "private dossier evidence" not in response.text
    assert "private summary" not in response.text
    assert client.get(
        f"/memory/dossiers?uid={scope.uid}&char_id={scope.character_id}",
        headers=_headers("state-token"),
    ).status_code == 403
    assert client.get(
        f"/observability/memory-consolidation?uid={scope.uid}&char_id={scope.character_id}",
        headers=_headers("memory-token"),
    ).status_code == 403


def test_observability_returns_empty_status_before_scope_database_exists(sandbox):
    client = _client_with_tokens(sandbox)
    response = client.get(
        f"/observability/memory-consolidation?uid=new-owner&char_id={TEST_CHAR_ID}",
        headers=_headers("state-token"),
    )
    assert response.status_code == 200
    status = response.json()["scope_status"]
    assert status["coverage_ingest_sequence"] == 0
    assert status["backlog"] == 0
    assert status["current_batch"] is None
    assert status["recent_runs"] == []


def test_memory_read_can_search_and_read_but_cannot_control(sandbox):
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("memory-reader", TEST_CHAR_ID)
    dossier_id = _seed(scope)
    client = _client_with_tokens(sandbox)
    search = client.get(
        f"/memory/dossiers?uid={scope.uid}&char_id={scope.character_id}&q=Admin",
        headers=_headers("memory-token"),
    )
    assert search.status_code == 200
    assert search.json()["items"][0]["dossier_id"] == dossier_id
    detail = client.get(
        f"/memory/dossiers/{dossier_id}?uid={scope.uid}&char_id={scope.character_id}",
        headers=_headers("memory-token"),
    )
    assert detail.status_code == 200
    assert detail.json()["dossier"]["description"] == "private summary"
    assert client.patch(
        "/settings/memory-consolidation", json={"enabled": True},
        headers=_headers("memory-token"),
    ).status_code == 403


def test_admin_updates_config_and_controls_scope(sandbox, monkeypatch, tmp_path):
    import shutil

    from core import config_loader

    config_path = tmp_path / "config.yaml"
    shutil.copyfile("config.example.yaml", config_path)
    monkeypatch.setattr(config_loader, "_CONFIG_PATH", config_path)
    config_loader._config = None
    config_loader._base_config = None
    client = _client_with_tokens(sandbox)

    updated = client.patch(
        "/settings/memory-consolidation",
        json={"enabled": True, "daily_call_budget": 3, "background_preset": "deepseek-default"},
        headers=_headers("admin-token"),
    )
    assert updated.status_code == 200
    assert updated.json()["settings"]["daily_call_budget"] == 3
    paused = client.post(
        "/memory-consolidation/control",
        json={"action": "pause", "reason": "maintenance window"},
        headers=_headers("admin-token"),
    )
    assert paused.status_code == 200
    assert paused.json()["settings"]["paused"] is True
    revoked = client.post(
        "/memory-consolidation/control",
        json={"action": "revoke"}, headers=_headers("admin-token"),
    )
    assert revoked.status_code == 200
    assert revoked.json()["settings"]["enabled"] is False
    assert revoked.json()["settings"]["grant_revision"] == 2
