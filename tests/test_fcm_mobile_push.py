"""FCM registration and wake tests against the existing mobile queue."""

import asyncio
import json
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi import FastAPI
import httpx
import pytest

from admin import auth
from admin.routers import mobile
from admin.token_registry import hash_token
from channels import fcm_publisher, mobile as queue
from core import mobile_push_devices as devices, session_scope

INSTALLATION = "d88ab94f-952d-4aa9-a18e-8e262c299925"
CREDENTIAL = "test-mobile"
REGISTRATION = "fake_registration_abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def push_app(sandbox, monkeypatch, enabled_client_channels):
    channel = queue.MobileChannel()
    active = {hash_token(CREDENTIAL), hash_token("other-mobile")}
    monkeypatch.setenv("PRESENCEKIT_FCM_PROJECT", "fixture-project")
    monkeypatch.setenv("PRESENCEKIT_FCM_REGISTRY_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(devices, "owner_id", lambda: "fixture-owner")
    monkeypatch.setattr(session_scope, "_owner_id", lambda: "fixture-owner")
    monkeypatch.setattr(session_scope, "_validate_character", lambda value: value)
    monkeypatch.setattr(session_scope, "_SESSIONS", {})
    monkeypatch.setattr(devices, "find_by_hash", lambda digest: (
        SimpleNamespace(profiles={"mobile"}) if digest in active else None
    ))
    monkeypatch.setattr(auth, "resolve_token", lambda raw: auth.TokenInfo(
        raw, frozenset({"chat"}), "desktop" if raw == "test-desktop" else "mobile",
    ) if raw in {"test-desktop", CREDENTIAL, "other-mobile"} else None)
    monkeypatch.setattr(mobile, "_get_mobile_channel", lambda: channel)
    monkeypatch.setattr(queue, "schedule_signal_publish", lambda item: None)
    app = FastAPI()
    app.include_router(mobile.router)
    grant = session_scope.create_session(token_label=CREDENTIAL, char_id="fixture-character")
    headers = {"Authorization": f"Bearer {CREDENTIAL}", "X-Presence-Session": grant.session_id}
    return app, channel, headers, active


@pytest.mark.asyncio
async def test_registration_and_readback_are_bound_and_revoke_old_generation(push_app, sandbox):
    app, channel, headers, active = push_app
    path = "/mobile/push/devices/self"
    body = {"installation_id": INSTALLATION, "registration": REGISTRATION, "consent": True}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        for credential in ("test-desktop", "other-mobile"):
            rejected = await client.put(path, json=body, headers={**headers, "Authorization": f"Bearer {credential}"})
            assert rejected.status_code == 403
        invalid = await client.put(path, headers=headers, json={**body, "registration": "secret.invalid"})
        assert invalid.status_code == 422
        assert "secret.invalid" not in invalid.text
        first = await client.put(path, headers=headers, json=body)
        assert first.status_code == 200
        generation = first.json()["generation"]
        assert REGISTRATION not in sandbox.mobile_push_devices().read_text()
        await channel.send("private message", "fixture-owner", msg_id="wake-1", char_id="fixture-character")
        read_path = f"/mobile/push/message?installation_id={INSTALLATION}&seq=1&message_id=wake-1"
        correct = await client.get(f"{read_path}&generation={generation}", headers=headers)
        assert correct.json()["content"] == "private message"
        wrong = await client.get(f"{read_path}&generation={generation}", headers={"Authorization": "Bearer other-mobile"})
        assert wrong.status_code == 404
        assert len(await channel.poll()) == 1

        second = await client.put(path, headers=headers, json={**body, "registration": REGISTRATION + "rotated"})
        updated = second.json()["generation"]
        assert updated != generation
        assert (await client.get(f"{read_path}&generation={generation}", headers=headers)).status_code == 404
        assert devices.targets("fixture-owner", "wrong-character") == []
        active.remove(hash_token(CREDENTIAL))
        assert devices.targets("fixture-owner", "fixture-character") == []
        active.add(hash_token(CREDENTIAL))
        assert (await client.delete(f"{path}?installation_id={INSTALLATION}", headers=headers)).status_code == 200
        assert (await client.get(f"{read_path}&generation={updated}", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_fcm_queue_is_durable_and_fcm_payload_contains_only_wake(push_app, monkeypatch):
    _, channel, _, _ = push_app
    devices.register(INSTALLATION, "fixture-owner", "fixture-character", hash_token(CREDENTIAL), REGISTRATION)
    original_client = httpx.AsyncClient
    attempts = []

    def send(request):
        message = json.loads(request.content)["message"]
        attempts.append(message)
        assert "private body" not in request.content.decode()
        if len(attempts) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"name": "provider-id"})

    monkeypatch.setattr(fcm_publisher, "_access_token", lambda: "fake-authorization")
    monkeypatch.setattr(fcm_publisher.httpx, "AsyncClient", lambda **options: original_client(
        transport=httpx.MockTransport(send), **options,
    ))
    relay = []
    monkeypatch.setattr(queue, "schedule_signal_publish", relay.append)
    await channel.send("private body", "fixture-owner", msg_id="wake-1",
                       char_id="fixture-character", proactive_push=True)
    rows = await channel.poll()
    assert rows[0]["content"] == "private body"
    await asyncio.gather(*fcm_publisher._jobs)
    assert len(attempts) == 2
    assert attempts[-1]["data"]["seq"] == str(rows[0]["seq"])
    assert attempts[-1]["data"]["message_id"] == rows[0]["id"]
    assert relay == []
    assert await channel.ack(rows[0]["seq"]) == 0
