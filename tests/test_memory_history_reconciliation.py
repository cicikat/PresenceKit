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
