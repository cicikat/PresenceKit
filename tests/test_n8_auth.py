"""
N8 鉴权补洞测试
验证 /upload/ingest、/desktop/wake、/desktop/activate
在未持 token 时返回 401/403，持正确 token 时通过鉴权层。
"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.owner_chat import service as owner_chat_service
from admin.routers.chat import router
from admin.auth import verify_token

VALID_TOKEN = "test-secret-n8"

# ── Build test app ─────────────────────────────────────────────────────────────
app = FastAPI()
app.include_router(router)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _patch_secret(monkeypatch):
    """Make get_admin_secret return a known value for every test."""
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)


@pytest.fixture(autouse=True)
def _patch_internals(monkeypatch):
    """Block real LLM / channel calls so tests are unit-scoped."""
    import admin.routers.chat as chat_mod
    monkeypatch.setattr('core.owner_chat.service.run_owner_chat_turn', AsyncMock(return_value={"reply": "ok"}))

    try:
        import channels.registry as reg
        monkeypatch.setattr(reg, "get", lambda _: None)
    except Exception:
        pass


@pytest.fixture()
def client_no_token():
    app.dependency_overrides.clear()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def client_authed():
    app.dependency_overrides.clear()
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )


# ── /upload/ingest ────────────────────────────────────────────────────────────

def test_upload_ingest_no_token_rejected(client_no_token):
    resp = client_no_token.post("/upload/ingest", data={"message": "hi"})
    assert resp.status_code in (401, 403)


def test_upload_ingest_valid_token_passes_auth(client_authed):
    # File is empty → 422 from business logic, NOT 401/403
    resp = client_authed.post("/upload/ingest", data={"message": "hi"})
    assert resp.status_code not in (401, 403)


# ── /desktop/wake ─────────────────────────────────────────────────────────────

def test_desktop_wake_no_token_rejected(client_no_token):
    resp = client_no_token.post("/desktop/wake", json={})
    assert resp.status_code in (401, 403)


def test_desktop_wake_no_token_does_not_trigger_llm(client_no_token):
    import admin.routers.chat as chat_mod
    owner_chat_service.run_owner_chat_turn.reset_mock()
    client_no_token.post("/desktop/wake", json={})
    owner_chat_service.run_owner_chat_turn.assert_not_called()


def test_desktop_wake_valid_token_passes_auth(client_authed):
    resp = client_authed.post("/desktop/wake", json={})
    assert resp.status_code not in (401, 403)


# ── /desktop/activate ─────────────────────────────────────────────────────────

def test_desktop_activate_no_token_rejected(client_no_token):
    resp = client_no_token.post("/desktop/activate")
    assert resp.status_code in (401, 403)


def test_desktop_activate_valid_token_passes(client_authed):
    resp = client_authed.post("/desktop/activate")
    assert resp.status_code == 200
