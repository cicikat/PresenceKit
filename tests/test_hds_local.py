import json

import pytest

from core import hds_local
from core.memory import health_state


def test_hds_http_receiver_enforces_lan_and_size(sandbox, monkeypatch):
    from fastapi.testclient import TestClient
    from admin.hds_server import app
    from admin.hds_server import request_stats

    monkeypatch.setattr(hds_local, "config", lambda: {"enabled": True, "allowed_subnets": ["127.0.0.1/32"]})
    monkeypatch.setattr("admin.hds_server.get_config", lambda: {"scheduler": {"owner_id": "hds-http"}})
    client = TestClient(app)
    assert client.put("/", json={"heartRate": 91}).status_code == 403
    monkeypatch.setattr(hds_local, "allowed_source", lambda _: True)
    assert client.put("/", json={"data": json.dumps({"heartRate": 91})}).status_code == 200
    assert client.put("/", json={"data": "bad"}).status_code == 422
    assert client.put("/", content=b"x" * 8193).status_code == 413
    stats = request_stats()
    assert stats["methods"]["PUT"] >= 4
    assert all(stats["statuses"].get(code, 0) >= 1 for code in ("200", "403", "413", "422"))


def test_hds_envelope_persistence_and_change(sandbox, monkeypatch):
    monkeypatch.setattr(hds_local, "config", lambda: {"enabled": True, "source_mode": "manual", "allowed_subnets": ["192.168.1.0/24"]})
    uid = "hds-test"
    for offset, value in [(0, 75), (20, 76), (40, 74), (220, 132), (240, 134), (260, 133)]:
        hds_local.ingest(uid, {"data": json.dumps({"heartRate": value})}, now=1000 + offset)
    assert len(health_state.load(uid)["hds_samples"]) == 6
    change = hds_local.latest_change(uid, now=1260)
    assert change["value"] == 133
    assert change["previous_value"] == 75
    assert hds_local.latest_change(uid, now=1261) is None
    assert hds_local.allowed_source("192.168.1.50")
    assert not hds_local.allowed_source("192.168.2.50")


def test_auto_source_follows_interface_address(monkeypatch):
    monkeypatch.setattr(hds_local, "config", lambda: {"source_mode": "auto", "interface": "WiFi"})
    monkeypatch.setattr(hds_local, "interfaces", lambda: [
        {"name": "WiFi", "address": "192.168.5.10", "network": "192.168.5.0/24"},
        {"name": "Other", "address": "10.2.0.3", "network": "10.2.0.0/16"},
    ])
    assert hds_local.allowed_source("192.168.5.20")
    assert not hds_local.allowed_source("10.2.0.20")


def test_hds_rejects_invalid_and_duplicate(sandbox, monkeypatch):
    monkeypatch.setattr(hds_local, "config", lambda: {"enabled": True})
    uid = "hds-invalid"
    for bad in ({"data": "not-json"}, {"heartRate": 12}, {"bpm": True}):
        with pytest.raises(ValueError):
            hds_local.ingest(uid, bad, now=1000)
    assert hds_local.ingest(uid, {"heartRate": 80}, now=1000)["accepted"]
    assert not hds_local.ingest(uid, {"heartRate": 80}, now=1002)["accepted"]


@pytest.mark.asyncio
async def test_hds_admin_settings_save_interface_without_ip(sandbox, monkeypatch):
    from admin.routers import watch
    saved = {}
    monkeypatch.setattr(watch, "read_config_file", lambda _: {})
    monkeypatch.setattr(watch, "write_config_file", lambda _, value: saved.update(value))
    monkeypatch.setattr(hds_local, "interfaces", lambda: [
        {"name": "WiFi", "address": "192.168.5.10", "network": "192.168.5.0/24"}
    ])
    body = watch.HdsLocalSettings(enabled=True, port=3476, source_mode="auto", interface="WiFi")
    await watch.update_hds_local_settings(body, auth={})
    assert saved["hds_local"]["interface"] == "WiFi"
    assert saved["hds_local"]["host"] == "0.0.0.0"
    assert "192.168.5.10" not in str(saved)


@pytest.mark.asyncio
async def test_hds_admin_rejects_public_and_broad_manual_sources(sandbox):
    from fastapi import HTTPException
    from admin.routers import watch

    for subnet in ("0.0.0.0/0", "8.8.8.0/24", "192.168.0.0/8"):
        body = watch.HdsLocalSettings(
            enabled=True, port=3476, source_mode="manual", allowed_subnets=[subnet],
        )
        with pytest.raises(HTTPException) as exc:
            await watch.update_hds_local_settings(body, auth={})
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_hds_admin_reports_current_address_and_restart_need(monkeypatch):
    from admin.routers import watch
    monkeypatch.setattr(hds_local, "config", lambda: {
        "enabled": True, "port": 3476, "source_mode": "auto", "interface": "WiFi",
    })
    monkeypatch.setattr(hds_local, "interfaces", lambda: [
        {"name": "WiFi", "address": "192.168.7.42", "network": "192.168.7.0/24"},
    ])
    monkeypatch.setattr("admin.hds_server.bound_port", lambda: None)
    result = await watch.get_hds_local_settings(auth={})
    assert result["urls"] == ["http://192.168.7.42:3476/"]
    assert result["restart_required"] is True
