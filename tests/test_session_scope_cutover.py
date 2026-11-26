"""T4 mounted HTTP contracts: admission, frozen ownership and no fallback."""
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fixtures.session_grants import issue


@pytest.fixture
def client(sandbox, monkeypatch):
    from admin import auth
    from admin.routers import chat, chat_log, mobile, observability, transcribe
    from core import session_scope
    session_scope.reset_for_tests()
    monkeypatch.setattr(auth, "resolve_token", lambda token: auth.TokenInfo(
        token, frozenset({"chat", "memory.read", "state.read"})))
    app = FastAPI()
    for router in (chat.router, mobile.router, observability.router, transcribe.router):
        app.include_router(router)
    app.include_router(chat_log.router, prefix="/chat-log")
    with TestClient(app) as client:
        yield client
    session_scope.reset_for_tests()


CASES = [
    ("POST", "/desktop/chat", {"json": {"message": "hello"}}),
    ("POST", "/mobile/chat", {"json": {"message": "hello"}}),
    ("POST", "/desktop/wake", {"json": {}}),
    ("POST", "/upload/ingest", {"files": {"file": ("note.txt", b"hello")}}),
    ("POST", "/transcribe", {"files": {"file": ("voice.wav", b"audio")}}),
    ("GET", "/chat/media/" + "a" * 64, {}),
    ("GET", "/chat/artifacts/" + "a" * 32, {}),
    ("GET", "/chat/artifacts/" + "a" * 32 + "/preview", {}),
    ("GET", "/chat-log/dates", {}),
    ("GET", "/chat-log/2026-11-24", {}),
    ("GET", "/chat-log/stats/calendar", {}),
    ("GET", "/chat/turns/turn/reasoning", {}),
    ("GET", "/desktop/chat/requests/dc-1795478400000-" + "a" * 32, {}),
]


@pytest.mark.parametrize("method,path,arguments", CASES)
@pytest.mark.parametrize("case,expected", [("missing", (422, "session_scope_required")),
    ("unknown", (404, "session_not_found")), ("expired", (404, "session_not_found")),
    ("other_token", (403, "character_not_authorized"))])
def test_scoped_routes_reject_before_business(client, monkeypatch, method, path, arguments, case, expected):
    """No rejected request may fall through to live-active business execution."""
    from core import session_scope
    grant = issue(monkeypatch, label="fixture")
    headers = {"Authorization": "Bearer fixture"}
    if case == "expired":
        monkeypatch.setattr(session_scope.time, "time", lambda: grant.expires_at + 1)
    if case != "missing":
        headers["X-Presence-Session"] = "unknown" if case == "unknown" else grant.session_id
    if case == "other_token":
        headers["Authorization"] = "Bearer other"
    response = client.request(method, path, headers=headers, **arguments)
    assert (response.status_code, response.json()["detail"]) == expected
    assert session_scope.observability_snapshot()["retained_requests"] == 0


def test_artifact_id_never_authorizes_another_character(client, monkeypatch):
    from core.tools import chat_artifacts
    first = issue(monkeypatch, owner="owner", char="first", label="fixture")
    other = issue(monkeypatch, owner="owner", char="second", label="fixture")
    artifact = json.loads(chat_artifacts.write_artifact("note.md", "private", user_id="owner", char_id="first"))
    path = "/chat/artifacts/" + artifact["id"]
    for suffix in ("", "/preview"):
        denied = client.get(path + suffix, headers={"Authorization": "Bearer fixture", "X-Presence-Session": other.session_id})
        assert denied.status_code == 404
        allowed = client.get(path + suffix, headers={"Authorization": "Bearer fixture", "X-Presence-Session": first.session_id})
        assert allowed.status_code == 200
        assert "private" in allowed.text


def test_media_digest_never_authorizes_another_character(client, monkeypatch, sandbox):
    """A global inbox blob is readable only through its retained owner/role evidence."""
    import hashlib
    from core.memory.event_store import append_event, tombstone_event
    from core.memory.scope import MemoryScope
    from tests.test_chat_media import PNG
    first = issue(monkeypatch, owner="owner", char="first", label="fixture")
    other = issue(monkeypatch, owner="owner", char="second", label="fixture")
    digest = hashlib.sha256(PNG).hexdigest()
    (sandbox.inbox_dir() / "sample.png").write_bytes(PNG)
    scope = MemoryScope.reality_scope("owner", "first")
    append_event(scope, {
        "event_id": "media-event", "turn_id": "media-turn", "kind": "user_message",
        "actor": "user", "visible_text": "sample", "memory_text": "sample",
        "media_refs_json": [{"kind": "image", "filename": "sample.png", "sha256": digest}],
    })
    first_headers = {"Authorization": "Bearer fixture", "X-Presence-Session": first.session_id}
    other_headers = {"Authorization": "Bearer fixture", "X-Presence-Session": other.session_id}
    allowed = client.get("/chat/media/" + digest, headers=first_headers)
    assert allowed.status_code == 200
    assert allowed.content == PNG
    denied = client.get("/chat/media/" + digest, headers=other_headers)
    missing = client.get("/chat/media/" + "f" * 64, headers=other_headers)
    assert denied.status_code == missing.status_code == 410
    assert denied.json() == missing.json()
    assert denied.content != PNG
    tombstone_event(scope, "media-event")
    assert client.get("/chat/media/" + digest, headers=first_headers).status_code == 410


def test_scoped_upload_index_authorizes_inbox_before_turn_capture(client, monkeypatch, sandbox):
    """A newly ingested own-role upload remains usable before its turn is captured."""
    import hashlib
    from core.character_document_library import store_upload
    from tests.test_chat_media import PNG
    grant = issue(monkeypatch, owner="owner", char="first", label="fixture")
    digest = hashlib.sha256(PNG).hexdigest()
    (sandbox.inbox_dir() / "fresh.png").write_bytes(PNG)
    store_upload(uid="owner", char_id="first", filename="fresh.png", media_type="image/png",
                 sha256=digest, searchable_text="newly received image", source="upload_image")
    response = client.get("/chat/media/" + digest, headers={
        "Authorization": "Bearer fixture", "X-Presence-Session": grant.session_id,
    })
    assert response.status_code == 200
    assert response.content == PNG


def test_desktop_receipt_and_generation_are_character_scoped(client, monkeypatch):
    from core.owner_chat import service
    first = issue(monkeypatch, owner="owner", char="first", label="fixture")
    second = issue(monkeypatch, owner="owner", char="second", label="fixture")
    calls = []
    async def execute(message, channel, **kwargs):
        from core.memory import short_term
        scope = kwargs["frozen_scope"]
        calls.append((scope.uid, scope.character_id))
        short_term.append(scope.uid, "assistant", scope.character_id, "turn", char_id=scope.character_id)
        return {"reply": scope.character_id, "char_id": scope.character_id, "msg_id": "wire", "turn_id": "turn"}
    monkeypatch.setattr(service, "run_owner_chat_turn", execute)
    monkeypatch.setattr("admin.reality_admission.require_reality_turn", lambda owner: None)
    from core.config_loader import get_config
    config = get_config()
    monkeypatch.setattr("core.config_loader.get_config", lambda: {**config, "scheduler": {"owner_id": "owner"}})
    rid = f"dc-{int(time.time()*1000)}-" + "b" * 32
    def headers(grant):
        return {"Authorization": "Bearer fixture", "X-Presence-Session": grant.session_id, "X-Presence-Chat-Request": rid}
    one = client.post("/desktop/chat", json={"message": "hello"}, headers=headers(first))
    assert one.status_code == 200, one.text
    path = "/desktop/chat/requests/" + rid
    assert client.get(path, headers=headers(second)).json()["status"] == "unknown"
    assert client.post("/desktop/chat", json={"message": "hello"}, headers=headers(first)).json()["reply"] == "first"
    assert client.post("/desktop/chat", json={"message": "hello"}, headers=headers(second)).json()["reply"] == "second"
    assert calls == [("owner", "first"), ("owner", "second")]


def test_transcription_receipt_uses_grant_instead_of_live_active(client, monkeypatch):
    from core import audio_perception as audio
    from core.memory.scope import MemoryScope
    grant = issue(monkeypatch, owner="owner", char="first", label="fixture")
    monkeypatch.setattr("core.character_context.selected_character_id_or_none", lambda: "second")
    monkeypatch.setattr(audio, "uses_local_transcribe", lambda config: False)
    async def transcribe(*args):
        return {"text": "hello", "tone": "unclear", "voice_only": True}
    monkeypatch.setattr(audio, "ingest_audio_bytes", transcribe)
    response = client.post("/transcribe", files={"file": ("voice.wav", b"audio")}, data={"channel": "desktop"},
        headers={"Authorization": "Bearer fixture", "X-Presence-Session": grant.session_id})
    assert response.status_code == 200
    receipt = response.json()["audio_perception_id"]
    value = audio.consume_receipt(receipt, "hello", "desktop", scope=grant.memory_scope)
    assert value["voice_only"] is True
    receipt = audio.issue_receipt({"text": "hello", "voice_only": True}, "desktop", scope=grant.memory_scope)
    assert audio.consume_receipt(receipt, "hello", "desktop", scope=MemoryScope.reality_scope("owner", "second")) is None
