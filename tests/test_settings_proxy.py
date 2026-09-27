"""GET/PUT /proxy keeps the model connection mode next to the existing proxy URLs."""

from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

VALID_TOKEN = "proxy-test-secret"


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import admin.routers.settings_proxy as sp

    temp_cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(sp, "CONFIG_FILE", temp_cfg)
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)

    with patch("core.config_loader.reload_config", return_value=None):
        from admin.routers.settings_proxy import router as proxy_router
        app = FastAPI()
        app.include_router(proxy_router)
        yield TestClient(app), temp_cfg


def _auth():
    return {"Authorization": f"Bearer {VALID_TOKEN}"}


def test_get_proxy_defaults_to_follow_global(monkeypatch):
    import admin.routers.settings_proxy as sp

    monkeypatch.setattr(sp, "get_config", lambda: {"proxy": {"enabled": False, "http": "", "https": ""}})
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    resp = client.get("/proxy", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["model_connection_mode"] == "follow_global"


def test_get_proxy_normalizes_unknown_mode(monkeypatch):
    import admin.routers.settings_proxy as sp

    monkeypatch.setattr(
        sp,
        "get_config",
        lambda: {"proxy": {"enabled": True, "http": "http://127.0.0.1:7897", "model_connection_mode": "weird"}},
    )
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    assert client.get("/proxy", headers=_auth()).json()["model_connection_mode"] == "follow_global"


def test_put_auto_mode_requires_http_proxy_and_does_not_write(admin_client, monkeypatch):
    client, temp_cfg = admin_client
    temp_cfg.write_text("proxy:\n  enabled: false\n  http: ''\n", encoding="utf-8")
    monkeypatch.setattr("core.llm_client.reload_client", AsyncMock())
    resp = client.put(
        "/proxy",
        json={"model_connection_mode": "auto", "http": ""},
        headers=_auth(),
    )
    assert resp.status_code == 422
    saved = yaml.safe_load(temp_cfg.read_text(encoding="utf-8"))
    assert saved["proxy"].get("model_connection_mode") is None


def test_put_auto_mode_writes_and_reloads_clients(admin_client, monkeypatch):
    client, temp_cfg = admin_client
    temp_cfg.write_text("proxy:\n  enabled: false\n  http: http://127.0.0.1:7897\n", encoding="utf-8")
    reload_client = AsyncMock()
    monkeypatch.setattr("core.llm_client.reload_client", reload_client)
    resp = client.put(
        "/proxy",
        json={"model_connection_mode": "auto", "http": "http://127.0.0.1:7897"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    saved = yaml.safe_load(temp_cfg.read_text(encoding="utf-8"))
    assert saved["proxy"]["model_connection_mode"] == "auto"
    assert saved["proxy"]["http"] == "http://127.0.0.1:7897"
    assert reload_client.await_count == 1


def test_put_auto_mode_reuses_existing_http_url(admin_client, monkeypatch):
    client, temp_cfg = admin_client
    temp_cfg.write_text("proxy:\n  enabled: false\n  http: http://127.0.0.1:7897\n", encoding="utf-8")
    monkeypatch.setattr("core.llm_client.reload_client", AsyncMock())
    resp = client.put("/proxy", json={"model_connection_mode": "auto"}, headers=_auth())
    assert resp.status_code == 200
    saved = yaml.safe_load(temp_cfg.read_text(encoding="utf-8"))
    assert saved["proxy"]["model_connection_mode"] == "auto"
    assert saved["proxy"]["http"] == "http://127.0.0.1:7897"


def test_put_direct_mode_keeps_proxy_url(admin_client, monkeypatch):
    client, temp_cfg = admin_client
    temp_cfg.write_text(
        "proxy:\n  enabled: true\n  http: http://127.0.0.1:7897\n  https: http://127.0.0.1:7897\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("core.llm_client.reload_client", AsyncMock())
    resp = client.put("/proxy", json={"model_connection_mode": "direct"}, headers=_auth())
    assert resp.status_code == 200
    saved = yaml.safe_load(temp_cfg.read_text(encoding="utf-8"))
    assert saved["proxy"]["model_connection_mode"] == "direct"
    assert saved["proxy"]["http"] == "http://127.0.0.1:7897"
