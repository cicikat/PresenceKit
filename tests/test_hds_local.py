import json

import pytest

from core import hds_local
from core.memory import health_state


def test_hds_http_receiver_enforces_lan_and_size(sandbox, monkeypatch):
    from fastapi.testclient import TestClient
    from admin.hds_server import app

    monkeypatch.setattr(hds_local, "config", lambda: {"enabled": True, "allowed_subnets": ["127.0.0.1/32"]})
    monkeypatch.setattr("admin.hds_server.get_config", lambda: {"scheduler": {"owner_id": "hds-http"}})
    client = TestClient(app)
    assert client.put("/", json={"heartRate": 91}).status_code == 403
    monkeypatch.setattr(hds_local, "allowed_source", lambda _: True)
    assert client.put("/", json={"data": json.dumps({"heartRate": 91})}).status_code == 200
    assert client.put("/", json={"data": "bad"}).status_code == 422
    assert client.put("/", content=b"x" * 8193).status_code == 413


def test_hds_envelope_persistence_and_change(sandbox, monkeypatch):
    monkeypatch.setattr(hds_local, "config", lambda: {"enabled": True, "allowed_subnets": ["192.168.1.0/24"]})
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


def test_hds_rejects_invalid_and_duplicate(sandbox, monkeypatch):
    monkeypatch.setattr(hds_local, "config", lambda: {"enabled": True})
    uid = "hds-invalid"
    for bad in ({"data": "not-json"}, {"heartRate": 12}, {"bpm": True}):
        with pytest.raises(ValueError):
            hds_local.ingest(uid, bad, now=1000)
    assert hds_local.ingest(uid, {"heartRate": 80}, now=1000)["accepted"]
    assert not hds_local.ingest(uid, {"heartRate": 80}, now=1002)["accepted"]
