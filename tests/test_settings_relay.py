"""
tests/test_settings_relay.py — W5: relay 中继唤醒设置接口

GET /settings/relay  — 读取 relay_base_url / relay_topic / relay_token（token 打码）
PUT /settings/relay  — 修改并热重载
"""

from unittest.mock import patch

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

VALID_TOKEN = "relay-test-secret"


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import admin.routers.settings_relay as sr

    temp_cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(sr, "CONFIG_FILE", temp_cfg)
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)

    with patch("core.config_loader.reload_config", return_value=None):
        from admin.routers.settings_relay import router as sr_router
        app = FastAPI()
        app.include_router(sr_router)
        yield TestClient(app), temp_cfg


def _auth():
    return {"Authorization": f"Bearer {VALID_TOKEN}"}


def test_get_relay_settings_masks_token(monkeypatch):
    import admin.routers.settings_relay as sr
    monkeypatch.setattr(
        sr, "get_config",
        lambda: {
            "relay_base_url": "https://relay.example",
            "relay_topic": "yexuan-wake-a1b2c3",
            "relay_token": "supersecrettoken123",
        },
    )
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)

    app = FastAPI()
    app.include_router(sr.router)
    client = TestClient(app)
    resp = client.get("/settings/relay", headers=_auth())

    assert resp.status_code == 200
    data = resp.json()
    assert data["relay_base_url"] == "https://relay.example"
    assert data["relay_topic"] == "yexuan-wake-a1b2c3"
    assert data["relay_token"] != "supersecrettoken123"
    assert data["relay_token"].startswith("supe")
    assert "supersecrettoken123" not in data["relay_token"]


def test_get_relay_settings_empty_when_unset(monkeypatch):
    import admin.routers.settings_relay as sr
    monkeypatch.setattr(sr, "get_config", lambda: {})
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)

    app = FastAPI()
    app.include_router(sr.router)
    client = TestClient(app)
    resp = client.get("/settings/relay", headers=_auth())

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"relay_base_url": "", "relay_topic": "", "relay_token": ""}


def test_put_relay_settings_writes_top_level_keys(admin_client):
    client, temp_cfg = admin_client
    temp_cfg.write_text("admin:\n  port: 8080\n", encoding="utf-8")

    resp = client.put(
        "/settings/relay",
        json={
            "relay_base_url": "https://ntfy.example.com",
            "relay_topic": "yexuan-wake-xyz",
            "relay_token": "tok12345678",
        },
        headers=_auth(),
    )
    assert resp.status_code == 200

    saved = yaml.safe_load(temp_cfg.read_text(encoding="utf-8"))
    assert saved["relay_base_url"] == "https://ntfy.example.com"
    assert saved["relay_topic"] == "yexuan-wake-xyz"
    assert saved["relay_token"] == "tok12345678"

    body = resp.json()
    assert body["relay_token"] != "tok12345678"


def test_put_relay_settings_partial_update_preserves_other_fields(admin_client):
    client, temp_cfg = admin_client
    temp_cfg.write_text(
        "relay_base_url: https://old.example\nrelay_topic: old-topic\nrelay_token: old-token\n",
        encoding="utf-8",
    )

    resp = client.put(
        "/settings/relay",
        json={"relay_topic": "new-topic"},
        headers=_auth(),
    )
    assert resp.status_code == 200

    saved = yaml.safe_load(temp_cfg.read_text(encoding="utf-8"))
    assert saved["relay_base_url"] == "https://old.example"
    assert saved["relay_topic"] == "new-topic"
    assert saved["relay_token"] == "old-token"


def test_no_token_rejected(admin_client):
    client, _ = admin_client
    resp = client.get("/settings/relay")
    assert resp.status_code in (401, 403)

    resp = client.put("/settings/relay", json={"relay_topic": "x"})
    assert resp.status_code in (401, 403)
