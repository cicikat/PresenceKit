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
    assert client.put("/", json={"data": "heartRate:74"}).json() == {"accepted": True, "value": 74}
    assert client.put("/", json={"data": "motion:[0.1,0.2,0.3]"}).json() == {"accepted": False, "ignored": True}
    assert client.put("/", json={"data": "calories:10"}).json() == {"accepted": False, "ignored": True}
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
    state = health_state.load(uid)
    assert state["hds_last_analysis"]["outcome"] == "candidate"
    assert state["hds_last_signal"]["value"] == 133
    assert hds_local.latest_change(uid, now=1261) is None
    assert health_state.load(uid)["hds_last_analysis"]["outcome"] == "cooldown"
    assert hds_local.allowed_source("192.168.1.50")
    assert not hds_local.allowed_source("192.168.2.50")


@pytest.mark.asyncio
async def test_hds_read_tool_reports_live_and_stale_without_consuming_signal(sandbox, monkeypatch):
    from core.tool_dispatcher import _TOOL_REGISTRY, _read_hds_heart_rate_wrapper, get_tools_schema

    monkeypatch.setattr(hds_local, "config", lambda: {"enabled": True})
    uid = "hds-read"
    for offset, value in [(0, 70), (20, 72), (40, 71)]:
        hds_local.ingest(uid, {"data": f"heartRate:{value}"}, now=1000 + offset)
    assert hds_local.latest_change(uid, now=1040) is None
    before = health_state.load(uid)
    live = hds_local.read_status(uid, now=1050)
    assert live["live"] is True
    assert live["recent_3m"]["median_bpm"] == 71
    assert live["last_automation_analysis"]["outcome"] == "ordinary"
    assert hds_local.read_status(uid, now=1400)["live"] is False
    assert health_state.load(uid) == before
    assert "read_hds_heart_rate" in {item["function"]["name"] for item in get_tools_schema(["memory"])}
    assert _TOOL_REGISTRY["read_hds_heart_rate"]["examples"]
    assert _TOOL_REGISTRY["read_hds_heart_rate"]["keywords"]
    monkeypatch.setattr(hds_local.time, "time", lambda: 1050)
    assert json.loads(await _read_hds_heart_rate_wrapper(uid))["latest"]["value"] == 71


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


def test_character_read_is_a_second_consent_layer(monkeypatch):
    """接收样本和「允许角色读取」是两层同意；缺字段时保持既有安装的行为不变。"""
    from core.tool_dispatcher import _is_tool_enabled

    monkeypatch.setattr(hds_local, "config", lambda: {"enabled": True})
    assert hds_local.character_read_enabled() is True
    monkeypatch.setattr(hds_local, "config", lambda: {"enabled": True, "character_read_enabled": False})
    assert hds_local.character_read_enabled() is False
    assert _is_tool_enabled("read_hds_heart_rate") is False
    # 接收本身关掉时，角色自然也读不到。
    monkeypatch.setattr(hds_local, "config", lambda: {"enabled": False, "character_read_enabled": True})
    assert hds_local.character_read_enabled() is False


@pytest.mark.asyncio
async def test_hds_admin_surfaces_character_read_toggle_and_hit_counts(sandbox, monkeypatch):
    """暴露着但零调用是本次的实际故障模式，观测必须能区分这两种情况。"""
    from admin.routers import watch

    monkeypatch.setattr(hds_local, "config", lambda: {
        "enabled": True, "port": 3476, "source_mode": "auto", "character_read_enabled": False,
    })
    monkeypatch.setattr(hds_local, "interfaces", lambda: [])
    monkeypatch.setattr("admin.hds_server.bound_port", lambda: 3476)
    settings = await watch.get_hds_local_settings(auth={})
    assert settings["character_read_enabled"] is False
    assert settings["character_read_effective"] is False

    monkeypatch.setattr("admin.hds_server.request_stats", lambda: {})
    monkeypatch.setattr(watch, "get_config", lambda: {"scheduler": {"owner_id": "hds-reads"}})
    monkeypatch.setattr("core.tool_audit.query", lambda *a, **kw: [
        {"tool": "read_hds_heart_rate", "timestamp": 1e12, "time": "2026-09-28T10:00:00+00:00"},
    ])
    status = await watch.get_hds_local_status(auth={})
    assert status["character_read_effective"] is False
    assert status["character_reads"]["last_7d"] == 1
