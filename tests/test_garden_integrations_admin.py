"""Admin control-surface contracts for the optional Galatea Garden bridge."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from admin.token_registry import TokenRecord


def _record(*, disabled: bool = False) -> TokenRecord:
    return TokenRecord(
        label="garden-wake",
        hash="sha256:not-a-secret",
        scopes=frozenset({"integration.write"}),
        expires_at=None,
        disabled=disabled,
    )


def test_unconfigured_garden_status_is_safe_and_side_effect_free(monkeypatch):
    from admin.routers import integrations

    monkeypatch.setattr(integrations, "_active_scope", lambda: ("", ""))
    monkeypatch.setattr(integrations.token_registry, "list_records", lambda: [])
    status = integrations.garden_status()["garden"]

    assert status["enabled"] is False
    assert status["integration_token"] == "missing"
    assert status["bridge_status"] == "unknown"
    assert status["pending_count"] == 0


def test_status_redacts_machine_secret_and_event_identity(monkeypatch):
    from admin.routers import integrations

    secret = "machine-token-must-not-leak"
    monkeypatch.setenv("GARDEN_MACHINE_TOKEN", secret)
    monkeypatch.setattr(integrations, "_active_scope", lambda: ("owner-a", "char-a"))
    monkeypatch.setattr(integrations.token_registry, "list_records", lambda: [_record()])
    monkeypatch.setattr(integrations, "_garden_entry", lambda *_: {
        "last_received_at": 100.0,
        "next_attempt_at": 0.0,
        "pending_count": 2,
        "processing_count": 1,
        "expired_count": 3,
        "consecutive_failures": 4,
        "last_success_at": 99.0,
        "last_reason": "game_turn_required",
        "last_disposition": "global_gap_filtered",
        "last_attempt_at": 98.0,
        "last_next_attempt_at": 101.0,
        "last_time_sensitive_lane": True,
        "external_id": "forbidden-id",
        "message": "forbidden message",
        "raw_hash": "forbidden-hash",
        "last_cursor": "forbidden-cursor",
    })
    monkeypatch.setattr(integrations, "_scheduler_running", lambda: True)

    serialized = repr(integrations.garden_status())
    assert "configured" in serialized
    assert "game_turn_required" in serialized
    assert "global_gap_filtered" in serialized
    for forbidden in (secret, "forbidden-id", "forbidden message", "forbidden-hash", "forbidden-cursor"):
        assert forbidden not in serialized


def test_manual_test_wake_uses_formal_garden_ingress_without_inline_drain(sandbox, monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin import auth
    from admin.routers import integrations
    from core.wake_bridge import WakeBridge

    monkeypatch.setattr(integrations, "_integration_token_configured", lambda: True)
    monkeypatch.setattr(integrations, "_active_scope", lambda: ("owner-a", "char-a"))
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"owner_id": "owner-a"}})
    monkeypatch.setattr("core.character_context.selected_character_id_or_none", lambda: "char-a")
    monkeypatch.setattr(auth, "resolve_token", lambda value: auth.TokenInfo(
        "fixture-admin", frozenset({"admin"})) if value == "fixture-t1" else None)
    drain, generate, sink = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(WakeBridge, "drain_due", drain)
    monkeypatch.setattr("core.scheduler.execution.execute_prompt", generate)
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", sink)
    app = FastAPI()
    app.include_router(integrations.router)
    with TestClient(app) as client:
        assert client.post("/integrations/garden/test-wake").status_code == 401
        response = client.post("/integrations/garden/test-wake", headers={"Authorization": "Bearer fixture-t1"})
    assert response.status_code == 200
    assert response.json()["status"] == "pending" and response.json()["sent"] is False
    state = json.loads(sandbox.wake_bridge_state("owner-a", char_id="char-a", provider="galatea_garden").read_text(encoding="utf-8"))
    record, = state["events"].values()
    assert record["provider"] == "galatea_garden" and record["reason"] == "manual_test"
    assert record["status"] == "pending" and record["attempts"] == 0
    drain.assert_not_called()
    generate.assert_not_called()
    sink.assert_not_called()


def test_missing_integration_token_rejects_test_without_ingress(monkeypatch):
    from admin.routers import integrations

    monkeypatch.setattr(integrations, "_integration_token_configured", lambda: False)
    result = asyncio.run(integrations.send_garden_test_wake(_auth=object()))
    assert result == {"status": "rejected", "reason": "integration_token_missing"}


def test_status_route_requires_auth_and_returns_only_redacted_fields(sandbox, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from admin import token_registry
    from admin.routers import integrations

    app = FastAPI()
    app.include_router(integrations.router)
    client = TestClient(app)
    assert client.get("/integrations/garden/status").status_code == 401

    token = token_registry.create_token("garden-panel", scopes=["admin"])
    monkeypatch.setattr(integrations, "_active_scope", lambda: ("owner-a", "char-a"))
    monkeypatch.setattr(integrations, "_garden_entry", lambda *_: {"pending_count": 1})
    response = client.get("/integrations/garden/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    garden = response.json()["garden"]
    assert garden["pending_count"] == 1
    assert "token" not in garden and "cursor" not in garden and "message" not in garden


def test_admin_page_is_a_frozen_compatibility_deep_link():
    root = Path(__file__).parent.parent
    page = (root / "admin/static/pages/integrations.html").read_text(encoding="utf-8")
    script = (root / "admin/static/js/integrations.js").read_text(encoding="utf-8")
    index = (root / "admin/static/index.html").read_text(encoding="utf-8")
    core = (root / "admin/static/js/core.js").read_text(encoding="utf-8")

    assert 'id="page-integrations" data-page-fragment="integrations"' in index
    nav = index.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert 'data-page="integrations"' not in nav
    assert 'data-action-args=\'["integrations"]\'' not in index
    assert "integrations:" not in core
    assert page.count('class="card"') == 1
    assert 'data-i18n="integrations.freeze.badge"' in page
    assert 'data-i18n="integrations.freeze.reason"' in page
    assert 'data-i18n="integrations.freeze.warning"' in page
    assert 'data-i18n="integrations.freeze.compatibility"' in page
    for forbidden in (
        "/integrations/garden/status",
        "/integrations/garden/test-wake",
        "manual_test",
        "GARDEN_MACHINE_TOKEN",
        "PRESENCE_INTEGRATION_TOKEN",
        "data-action=",
    ):
        assert forbidden not in page + script


def test_frozen_garden_page_has_complete_bilingual_copy():
    root = Path(__file__).parent.parent
    runtime = (root / "admin/static/i18n.js").read_text(encoding="utf-8")
    for key in (
        "integrations.title",
        "integrations.freeze.badge",
        "integrations.freeze.reason",
        "integrations.freeze.warning",
        "integrations.freeze.compatibility",
    ):
        assert runtime.count(f"'{key}'") == 2
