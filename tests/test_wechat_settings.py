from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from admin.auth import require_scopes
from admin.routers import wechat
from core.wechat_service import WechatSettings


def test_settings_validate_private_binding_and_url():
    for body in ({"enabled": "false"}, {"transport": "unknown"},
                 {"base_url": "http://user:secret@bridge.invalid"},
                 {"base_url": "http://bridge.invalid?key=secret"},
                 {"owner_sender_id": "group@chatroom"}, {"token": "secret"}):
        with pytest.raises(ValidationError):
            WechatSettings(**body)


def client():
    app = FastAPI()
    app.include_router(wechat.router)
    return app, TestClient(app)


def test_settings_and_observer_require_scopes():
    app, api = client()
    assert api.get("/settings/wechat").status_code in (401, 403)
    assert api.get("/observability/wechat").status_code in (401, 403)
    assert api.put("/settings/wechat", json={"enabled": True}).status_code in (401, 403)


def test_state_read_projection_has_no_binding_or_secret(monkeypatch):
    monkeypatch.setattr(wechat, "load_settings", lambda: WechatSettings(account_id="private_account",
                                                                       owner_sender_id="private_owner"))
    monkeypatch.setenv("WECHAT_TRANSPORT_TOKEN", "private-key")
    app, api = client()
    for route in app.routes:
        if hasattr(route, "dependant"):
            for dependency in route.dependant.dependencies:
                app.dependency_overrides[dependency.call] = lambda: object()
    response = api.get("/observability/wechat")
    assert response.status_code == 200
    for secret in ("private-key", "private_account", "private_owner"):
        assert secret not in response.text
    settings = api.get("/settings/wechat")
    assert settings.json()["credential_configured"]
    assert "private-key" not in settings.text


@pytest.mark.asyncio
async def test_config_save_preserves_other_settings_and_applies_runtime(tmp_path, monkeypatch):
    from core import config_loader
    path = tmp_path / "preview-config.yaml"
    path.write_bytes(b"qq:\n  enabled: true\n")
    monkeypatch.setattr(wechat, "CONFIG_FILE", path)
    monkeypatch.setattr(config_loader, "_CONFIG_PATH", path)
    monkeypatch.setattr(config_loader, "_config", None)
    monkeypatch.setattr(config_loader, "_base_config", None)
    monkeypatch.setattr(config_loader, "_config_mtime", None)
    monkeypatch.setattr(config_loader, "_base_config_mtime", None)
    runtime = type("Runtime", (), {"apply": AsyncMock(), "snapshot": lambda self: {
        "effective_state": "missing_connection_or_owner_binding", "connected": False,
        "proactive_effective": False, "counters": {}, "last_error": "", "queue_depth": 0,
    }})()
    monkeypatch.setattr(wechat, "get_service", lambda: runtime)
    settings = WechatSettings(enabled=True, account_id="fixture_account", owner_sender_id="fixture_owner")
    result = await wechat.put_settings(settings, auth=object())
    assert result["enabled"] is True and result["reload_status"] == "reloaded"
    assert result["effective_state"] == "missing_connection_or_owner_binding"
    runtime.apply.assert_awaited_once()
    assert config_loader.get_config()["qq"]["enabled"] is True


def test_state_read_token_cannot_read_bindings_or_change_settings(monkeypatch):
    import admin.auth as auth
    monkeypatch.setattr(auth, "resolve_token", lambda raw: auth.TokenInfo("fixture", frozenset({"state.read"})))
    app, api = client()
    headers = {"Authorization": "Bearer fixture-state-only"}
    assert api.get("/observability/wechat", headers=headers).status_code == 200
    assert api.get("/settings/wechat", headers=headers).status_code == 403
    assert api.put("/settings/wechat", headers=headers, json={"enabled": True}).status_code == 403
