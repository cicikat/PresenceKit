"""
tests/test_sec_auth1.py — SEC-AUTH-1: HTTP endpoint Bearer-token auth

Endpoints protected:
  POST /desktop/wake       (was already protected, tests verify contract)
  POST /desktop/activate
  POST /desktop/deactivate
  POST /upload/ingest
  POST /dream/enter        (SEC-AUTH-1 fix)
  POST /dream/chat         (SEC-AUTH-1 fix)
  POST /dream/exit         (SEC-AUTH-1 fix)
  GET  /dream/state        (SEC-AUTH-1 fix)
  GET  /dream/settings     (SEC-AUTH-1 fix)
  PATCH /dream/settings    (SEC-AUTH-1 fix)
  POST /agent/think        (SEC-AUTH-1 fix)

Coverage matrix per endpoint:
  - no token           → 401/403, no side-effects
  - wrong token        → 401/403, no side-effects
  - correct token      → passes auth layer (may 422/503 from business logic)

Additional contracts:
  - token value absent from error response body
  - token value absent from log records on rejection
  - upload/ingest: file not written to disk on auth failure
  - desktop/wake: LLM not called on auth failure
  - dream/enter: dream pipeline not called on auth failure
  - dream/chat:  dream turn not called on auth failure
"""

import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.routers.chat import router as chat_router
from admin.routers.dream import router as dream_router
from admin.routers.agent import router as agent_router
from admin.auth import verify_token

VALID_TOKEN = "test-secret-sec-auth1"
WRONG_TOKEN = "definitely-wrong-token"

# ── Build test app ─────────────────────────────────────────────────────────────

_app = FastAPI()
_app.include_router(chat_router)
_app.include_router(dream_router)
_app.include_router(agent_router)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _patch_secret(monkeypatch):
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)


@pytest.fixture(autouse=True)
def _patch_internals(monkeypatch):
    """Stub out all side-effectful internals so tests are unit-scoped."""
    import admin.routers.chat as chat_mod
    monkeypatch.setattr(chat_mod, "run_owner_chat_turn", AsyncMock(return_value={"reply": "ok"}))

    # channel registry
    try:
        import channels.registry as reg
        monkeypatch.setattr(reg, "get", lambda _: None)
    except Exception:
        pass

    # pipeline (used by dream enter)
    try:
        import core.pipeline_registry as pr
        monkeypatch.setattr(pr, "get", lambda: None)
    except Exception:
        pass


@pytest.fixture()
def no_token():
    _app.dependency_overrides.clear()
    return TestClient(_app, raise_server_exceptions=False)


@pytest.fixture()
def wrong_token():
    _app.dependency_overrides.clear()
    return TestClient(
        _app,
        raise_server_exceptions=False,
        headers={"Authorization": f"Bearer {WRONG_TOKEN}"},
    )


@pytest.fixture()
def authed():
    _app.dependency_overrides.clear()
    return TestClient(
        _app,
        raise_server_exceptions=False,
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )


# ════════════════════════════════════════════════════════════════════════════════
# /desktop/wake
# ════════════════════════════════════════════════════════════════════════════════

class TestDesktopWake:
    def test_no_token_rejected(self, no_token):
        resp = no_token.post("/desktop/wake", json={})
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.post("/desktop/wake", json={})
        assert resp.status_code in (401, 403)

    def test_correct_token_passes_auth(self, authed):
        resp = authed.post("/desktop/wake", json={})
        assert resp.status_code not in (401, 403)

    def test_no_token_does_not_call_llm(self, no_token):
        import admin.routers.chat as chat_mod
        chat_mod.run_owner_chat_turn.reset_mock()
        no_token.post("/desktop/wake", json={})
        chat_mod.run_owner_chat_turn.assert_not_called()

    def test_wrong_token_does_not_call_llm(self, wrong_token):
        import admin.routers.chat as chat_mod
        chat_mod.run_owner_chat_turn.reset_mock()
        wrong_token.post("/desktop/wake", json={})
        chat_mod.run_owner_chat_turn.assert_not_called()

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.post("/desktop/wake", json={})
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# /desktop/activate
# ════════════════════════════════════════════════════════════════════════════════

class TestDesktopActivate:
    def test_no_token_rejected(self, no_token):
        resp = no_token.post("/desktop/activate")
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.post("/desktop/activate")
        assert resp.status_code in (401, 403)

    def test_correct_token_passes(self, authed):
        resp = authed.post("/desktop/activate")
        assert resp.status_code == 200

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.post("/desktop/activate")
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# /desktop/deactivate
# ════════════════════════════════════════════════════════════════════════════════

class TestDesktopDeactivate:
    def test_no_token_rejected(self, no_token):
        resp = no_token.post("/desktop/deactivate")
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.post("/desktop/deactivate")
        assert resp.status_code in (401, 403)

    def test_correct_token_passes(self, authed):
        resp = authed.post("/desktop/deactivate")
        assert resp.status_code == 200

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.post("/desktop/deactivate")
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# /upload/ingest
# ════════════════════════════════════════════════════════════════════════════════

class TestUploadIngest:
    def test_no_token_rejected(self, no_token):
        resp = no_token.post("/upload/ingest", data={"message": "hi"})
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.post("/upload/ingest", data={"message": "hi"})
        assert resp.status_code in (401, 403)

    def test_correct_token_passes_auth(self, authed):
        # No file → 422 from business logic, NOT auth rejection
        resp = authed.post("/upload/ingest", data={"message": "hi"})
        assert resp.status_code not in (401, 403)

    def test_no_token_does_not_write_disk(self, no_token, tmp_path):
        """Auth rejection must happen before any disk write."""
        import io
        with patch("core.media_processor.ingest_file_bytes", new_callable=AsyncMock) as mock_ingest:
            fake_file = io.BytesIO(b"hello world")
            no_token.post(
                "/upload/ingest",
                files={"file": ("test.txt", fake_file, "text/plain")},
                data={"message": ""},
            )
            mock_ingest.assert_not_called()

    def test_wrong_token_does_not_write_disk(self, wrong_token):
        import io
        with patch("core.media_processor.ingest_file_bytes", new_callable=AsyncMock) as mock_ingest:
            fake_file = io.BytesIO(b"hello world")
            wrong_token.post(
                "/upload/ingest",
                files={"file": ("test.txt", fake_file, "text/plain")},
                data={"message": ""},
            )
            mock_ingest.assert_not_called()

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.post("/upload/ingest", data={"message": "hi"})
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# /dream/enter
# ════════════════════════════════════════════════════════════════════════════════

class TestDreamEnter:
    def test_no_token_rejected(self, no_token):
        resp = no_token.post("/dream/enter", json={})
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.post("/dream/enter", json={})
        assert resp.status_code in (401, 403)

    def test_correct_token_passes_auth(self, authed):
        # Pipeline not initialized → 503/409/422, not 401/403
        resp = authed.post("/dream/enter", json={"dream_mode": "sandbox"})
        assert resp.status_code not in (401, 403)

    def test_no_token_does_not_call_pipeline(self, no_token):
        with patch("core.dream.dream_pipeline.enter_dream", new_callable=AsyncMock) as mock_enter:
            no_token.post("/dream/enter", json={"dream_mode": "sandbox"})
            mock_enter.assert_not_called()

    def test_wrong_token_does_not_call_pipeline(self, wrong_token):
        with patch("core.dream.dream_pipeline.enter_dream", new_callable=AsyncMock) as mock_enter:
            wrong_token.post("/dream/enter", json={"dream_mode": "sandbox"})
            mock_enter.assert_not_called()

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.post("/dream/enter", json={})
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# /dream/chat
# ════════════════════════════════════════════════════════════════════════════════

class TestDreamChat:
    def test_no_token_rejected(self, no_token):
        resp = no_token.post("/dream/chat", json={"message": "hello"})
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.post("/dream/chat", json={"message": "hello"})
        assert resp.status_code in (401, 403)

    def test_correct_token_passes_auth(self, authed):
        with patch("core.dream.dream_pipeline.dream_turn", new_callable=AsyncMock) as mock_turn:
            mock_turn.return_value = {"reply": "ok"}
            resp = authed.post("/dream/chat", json={"message": "hello"})
            assert resp.status_code not in (401, 403)

    def test_no_token_does_not_call_dream_turn(self, no_token):
        with patch("core.dream.dream_pipeline.dream_turn", new_callable=AsyncMock) as mock_turn:
            no_token.post("/dream/chat", json={"message": "hello"})
            mock_turn.assert_not_called()

    def test_wrong_token_does_not_call_dream_turn(self, wrong_token):
        with patch("core.dream.dream_pipeline.dream_turn", new_callable=AsyncMock) as mock_turn:
            wrong_token.post("/dream/chat", json={"message": "hello"})
            mock_turn.assert_not_called()

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.post("/dream/chat", json={"message": "hello"})
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# /dream/exit
# ════════════════════════════════════════════════════════════════════════════════

class TestDreamExit:
    def test_no_token_rejected(self, no_token):
        resp = no_token.post("/dream/exit")
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.post("/dream/exit")
        assert resp.status_code in (401, 403)

    def test_correct_token_passes_auth(self, authed):
        with patch("core.dream.dream_pipeline.force_exit_dream", new_callable=AsyncMock):
            resp = authed.post("/dream/exit")
            assert resp.status_code not in (401, 403)

    def test_no_token_does_not_call_exit(self, no_token):
        with patch("core.dream.dream_pipeline.force_exit_dream", new_callable=AsyncMock) as mock_exit:
            no_token.post("/dream/exit")
            mock_exit.assert_not_called()

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.post("/dream/exit")
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# GET /dream/state
# ════════════════════════════════════════════════════════════════════════════════

class TestDreamState:
    def test_no_token_rejected(self, no_token):
        resp = no_token.get("/dream/state")
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.get("/dream/state")
        assert resp.status_code in (401, 403)

    def test_correct_token_passes_auth(self, authed):
        with patch("core.dream.dream_state.read_state", return_value={}), \
             patch("core.dream.dream_settings.load", return_value={}), \
             patch("core.dream.dream_hud.derive_hud_v1", return_value=({}, {})), \
             patch("core.dream.dream_hud.load_hud_state", return_value={}), \
             patch("core.dream.dream_hud.save_hud_state"):
            resp = authed.get("/dream/state")
            assert resp.status_code not in (401, 403)

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.get("/dream/state")
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# GET /dream/settings
# ════════════════════════════════════════════════════════════════════════════════

class TestDreamSettings:
    def test_no_token_rejected(self, no_token):
        resp = no_token.get("/dream/settings")
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.get("/dream/settings")
        assert resp.status_code in (401, 403)

    def test_correct_token_passes_auth(self, authed):
        with patch("core.dream.dream_settings.load", return_value={}):
            resp = authed.get("/dream/settings")
            assert resp.status_code not in (401, 403)

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.get("/dream/settings")
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# PATCH /dream/settings
# ════════════════════════════════════════════════════════════════════════════════

class TestDreamSettingsPatch:
    def test_no_token_rejected(self, no_token):
        resp = no_token.patch("/dream/settings", json={"lucid_mode": "lucid_shared"})
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.patch("/dream/settings", json={"lucid_mode": "lucid_shared"})
        assert resp.status_code in (401, 403)

    def test_correct_token_passes_auth(self, authed):
        with patch("core.dream.dream_settings.load", return_value={}), \
             patch("core.dream.dream_settings.save"):
            resp = authed.patch("/dream/settings", json={"lucid_mode": "lucid_shared"})
            assert resp.status_code not in (401, 403)

    def test_no_token_does_not_write_settings(self, no_token):
        with patch("core.dream.dream_settings.save") as mock_save:
            no_token.patch("/dream/settings", json={"lucid_mode": "lucid_shared"})
            mock_save.assert_not_called()

    def test_wrong_token_does_not_write_settings(self, wrong_token):
        with patch("core.dream.dream_settings.save") as mock_save:
            wrong_token.patch("/dream/settings", json={"lucid_mode": "lucid_shared"})
            mock_save.assert_not_called()

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.patch("/dream/settings", json={"lucid_mode": "lucid_shared"})
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# POST /agent/think
# ════════════════════════════════════════════════════════════════════════════════

class TestAgentThink:
    def test_no_token_rejected(self, no_token):
        resp = no_token.post("/agent/think", json={"messages": []})
        assert resp.status_code in (401, 403)

    def test_wrong_token_rejected(self, wrong_token):
        resp = wrong_token.post("/agent/think", json={"messages": []})
        assert resp.status_code in (401, 403)

    def test_correct_token_passes_auth(self, authed):
        with patch("core.llm_client.chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = "response"
            resp = authed.post("/agent/think", json={"messages": []})
            assert resp.status_code not in (401, 403)

    def test_no_token_does_not_call_llm(self, no_token):
        with patch("core.llm_client.chat", new_callable=AsyncMock) as mock_chat:
            no_token.post("/agent/think", json={"messages": []})
            mock_chat.assert_not_called()

    def test_wrong_token_does_not_call_llm(self, wrong_token):
        with patch("core.llm_client.chat", new_callable=AsyncMock) as mock_chat:
            wrong_token.post("/agent/think", json={"messages": []})
            mock_chat.assert_not_called()

    def test_token_not_in_error_response(self, wrong_token):
        resp = wrong_token.post("/agent/think", json={"messages": []})
        assert WRONG_TOKEN not in resp.text


# ════════════════════════════════════════════════════════════════════════════════
# Cross-cutting: token not in logs on rejection
# ════════════════════════════════════════════════════════════════════════════════

class TestTokenNotInLogs:
    """verify_token never logs the rejected credential."""

    def test_http_rejection_no_token_in_log(self, caplog):
        client = TestClient(_app, raise_server_exceptions=False,
                            headers={"Authorization": f"Bearer {WRONG_TOKEN}"})
        with caplog.at_level(logging.DEBUG):
            client.post("/desktop/wake", json={})
        for record in caplog.records:
            assert WRONG_TOKEN not in record.getMessage(), (
                f"Token leaked in log record: {record.getMessage()!r}"
            )

    def test_empty_secret_rejects_all(self, monkeypatch):
        monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "")
        client = TestClient(_app, raise_server_exceptions=False,
                            headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        resp = client.post("/desktop/activate")
        assert resp.status_code in (401, 403)
