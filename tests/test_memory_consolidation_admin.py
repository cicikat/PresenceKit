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
