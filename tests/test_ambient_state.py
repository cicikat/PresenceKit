"""Exercise ambient input and readback through the authenticated HTTP routes."""

from collections import OrderedDict
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin import auth, token_registry
from admin.routers import chat, sensor
from core import session_scope
from core.perception import ambient_state


def test_ambient_readback_rejects_other_credentials_and_expires(monkeypatch):
    credentials = {"test-desktop": "desktop", "other-desktop": "desktop", "test-watch": "watch"}
    active_hashes = {token_registry.hash_token(token) for token in credentials}

    def resolve(raw):
        profile = credentials.get(raw)
        if profile and token_registry.hash_token(raw) in active_hashes:
            return auth.TokenInfo(raw, frozenset({"sensor.write", "state.read", "chat"}), profile)
        return None

    monkeypatch.setattr(auth, "resolve_token", resolve)
    by_hash = {token_registry.hash_token(raw): SimpleNamespace(profiles={profile}) for raw, profile in credentials.items()}
    monkeypatch.setattr(
        ambient_state, "find_by_hash", lambda digest: by_hash.get(digest) if digest in active_hashes else None,
    )
    monkeypatch.setattr(ambient_state, "_devices", OrderedDict())
    monkeypatch.setattr(session_scope, "_SESSIONS", {})
    monkeypatch.setattr(session_scope, "_owner_id", lambda: "fixture-owner")
    monkeypatch.setattr(session_scope, "_validate_character", lambda value: value)
    monkeypatch.setattr(chat, "_owner_media_scope", lambda: ("fixture-owner", "fixture-character"))
    grant = session_scope.create_session(token_label="test-desktop", char_id="fixture-character")
    app = FastAPI()
    app.include_router(sensor.router)
    now = time.time()
    body = {"light": {"status": "available", "observed_at": now, "relative_brightness": 30}}
    headers = {"Authorization": "Bearer test-desktop", "X-Presence-Session": grant.session_id}

    with TestClient(app) as client:
        for other in ("other-desktop", "test-watch"):
            denied = client.post(
                "/sensor/ambient", json=body, headers={**headers, "Authorization": f"Bearer {other}"},
            )
            assert denied.status_code == 403
        raw = client.post("/sensor/ambient", json={"light": {**body["light"], "jpeg": "raw"}}, headers=headers)
        assert raw.status_code == 422
        assert client.post("/sensor/ambient", json=body, headers=headers).status_code == 200
        light = client.get("/sensor/ambient", headers=headers).json()["light"]
        assert light["relative_brightness"] == 30
        assert light["source"] == "camera_relative_brightness"
        assert light["expires_at"] <= now + ambient_state.TTL_SECONDS
        assert client.get("/sensor/ambient?device=phone", headers=headers).json()["light"] == {
            "status": "unavailable",
        }
        monkeypatch.setattr(chat, "_owner_media_scope", lambda: ("fixture-owner", "another-character"))
        assert client.get("/sensor/ambient", headers=headers).json()["light"] == {"status": "unavailable"}
        monkeypatch.setattr(chat, "_owner_media_scope", lambda: ("fixture-owner", "fixture-character"))

        with monkeypatch.context() as expired:
            expired.setattr(ambient_state.time, "time", lambda: now + 121)
            stale = client.get("/sensor/ambient", headers=headers).json()["light"]
            assert stale["status"] == "stale"
            assert "relative_brightness" not in stale

        active_hashes.remove(token_registry.hash_token("test-desktop"))
        inspector = {"Authorization": "Bearer other-desktop"}
        assert client.get("/sensor/ambient", headers=inspector).json()["light"] == {"status": "unavailable"}
