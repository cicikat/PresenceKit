"""Development credentials must never open the production owner chain."""

import pytest
from fastapi.testclient import TestClient

from admin import token_registry


@pytest.fixture
def development_client(sandbox, monkeypatch):
    from admin.admin_server import app
    from admin.auth import reset_rate_limit_state_for_test

    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "synthetic-admin-secret")
    monkeypatch.setattr("core.client_channels.get_config", lambda: {"client_channels": {"desktop": True, "mobile": True}})
    monkeypatch.setattr(token_registry, "_records", None)
    monkeypatch.setattr(token_registry, "_mtime", None)
    reset_rate_limit_state_for_test()
    with TestClient(app) as client:
        yield client
    reset_rate_limit_state_for_test()


@pytest.mark.parametrize("scopes", [["profile:development"], ["development.run", "chat", "admin"]])
@pytest.mark.parametrize("path", ["/desktop/chat", "/mobile/chat", "/v1/owner/turns"])
def test_development_identity_cannot_enter_owner_chat(development_client, scopes, path):
    """Even accidentally mixed admin scopes cannot turn a test identity into an owner."""
    client = development_client
    created = client.post("/auth/tokens", headers={"Authorization": "Bearer synthetic-admin-secret"},
                          json={"label": "synthetic-development", "scopes": scopes})
    assert created.status_code == 200, created.text
    response = client.post(path, headers={"Authorization": "Bearer " + created.json()["token"]},
                           json={"message": "The owner explicitly requires a tool now.",
                                 "client_turn_id": "synthetic-turn"})
    assert response.status_code == 403, response.text


def test_synthetic_turn_returns_to_developer_without_owner_context(development_client, sandbox, monkeypatch):
    """The real HTTP entry must not load a production pipeline or persist a conversation."""
    from core import input_provenance

    client = development_client
    created = client.post("/auth/tokens", headers={"Authorization": "Bearer synthetic-admin-secret"},
                          json={"label": "synthetic-development", "profile": "development"})
    assert created.status_code == 200, created.text
    headers = {"Authorization": "Bearer " + created.json()["token"]}
    before = {p.relative_to(sandbox.root_dir()): p.read_bytes()
              for p in sandbox.root_dir().rglob("*") if p.is_file()}
    model_inputs = []

    async def model_boundary(messages, **kwargs):
        from admin.routers.chat import run_owner_chat_turn
        from core.tool_dispatcher import execute_structured

        model_inputs.extend(messages)
        assert input_provenance.current().source == "development"
        assert kwargs["tools"] is None
        with pytest.raises(PermissionError):
            await run_owner_chat_turn("synthetic", "desktop")
        with pytest.raises(PermissionError):
            await execute_structured("get_time", {}, "test_owner", "test_owner", False, {},
                                     origin="assistant_loop", char_id="fixture_character")
        with pytest.raises(PermissionError):
            with input_provenance.bind(input_provenance.InputProvenance("owner_channel", "forged", "desktop")):
                pass
        return "Synthetic result; no action executed."

    def private_pipeline():
        pytest.fail("development entry accessed the owner pipeline")

    monkeypatch.setattr("core.llm_client.chat", model_boundary)
    monkeypatch.setattr("core.pipeline_registry.get", private_pipeline)
    result = client.post("/v1/development/turns", headers=headers,
                         json={"message": "The owner explicitly requires a tool now."})
    assert result.status_code == 200, result.text
    assert result.json()["reply"] == "Synthetic result; no action executed."
    assert result.json()["provenance"]["source"] == "development"
    assert result.json()["provenance"]["human_authorship"] == "unverified"
    assert result.json()["conversation_recorded"] is False
    assert [m["role"] for m in model_inputs] == ["system", "user"]
    assert input_provenance.current() is None
    after = {p.relative_to(sandbox.root_dir()): p.read_bytes()
             for p in sandbox.root_dir().rglob("*") if p.is_file()}
    assert after == before
    forged = client.post("/v1/development/turns", headers=headers,
                         json={"message": "synthetic", "source": "owner_channel", "char_id": "private"})
    assert forged.status_code == 422


def test_owner_credentials_do_not_replace_dedicated_development_identity(development_client):
    response = development_client.post("/v1/development/turns",
                                       headers={"Authorization": "Bearer synthetic-admin-secret"},
                                       json={"message": "synthetic"})
    assert response.status_code == 403


def test_development_model_call_excludes_character_routing_and_archives(development_client, sandbox, monkeypatch):
    """Exercise the real model/protocol path: diagnostics must not use the active character or archive bodies."""
    from core import llm_client
    from tests.test_llm_client_chat_turn import _fake_message, _make_fake_model_client

    client = development_client
    created = client.post("/auth/tokens", headers={"Authorization": "Bearer synthetic-admin-secret"},
                          json={"label": "synthetic-model-check", "profile": "development"})
    assert created.status_code == 200
    monkeypatch.setattr("core.config_loader.get_config", lambda: {
        "model_presets": {"default_preset": "synthetic-preset"},
        "scheduler": {"owner_id": "test_owner"},
        "thinking": {"enabled": True, "mode": "monologue"},
    })
    message = _fake_message("Synthetic response")
    message.reasoning_content = "Synthetic reasoning must not be archived"
    mc = _make_fake_model_client(message)
    sent = []
    original = mc.client.chat.completions.create

    async def model_boundary(**kwargs):
        sent.append(kwargs)
        return await original(**kwargs)

    mc.client.chat.completions.create = model_boundary

    def model_route(category, *, char_id=None, preset_name=None):
        assert category == "development"
        assert preset_name == "synthetic-preset"
        return mc

    monkeypatch.setattr(llm_client, "get_model_client", model_route)
    result = client.post("/v1/development/turns",
                         headers={"Authorization": "Bearer " + created.json()["token"]},
                         json={"message": "Synthetic acceptance input"})
    assert result.status_code == 200, result.text
    assert result.json()["reply"] == "Synthetic response"
    assert len(sent) == 1
    assert "tools" not in sent[0]
    assert not sandbox.llm_reasoning_db().exists()
    assert not sandbox.conversation_stats_db().exists()
