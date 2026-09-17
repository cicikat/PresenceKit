"""Canonical chat media identity, live-ref GC, and authenticated download."""

from pathlib import Path

from tests.fixtures.public_assets import TEST_CHAR_ID

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _digest(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _append_ref(uid: str, digest: str, filename: str = "photo.png"):
    from core.memory.event_store import append_event
    from core.memory.scope import MemoryScope

    append_event(MemoryScope.reality_scope(uid, TEST_CHAR_ID), {
        "event_id": f"{digest[:12]}:user",
        "turn_id": digest[:12],
        "kind": "user_message",
        "actor": "user",
        "visible_text": "hi",
        "memory_text": "hi",
        "media_refs_json": [{
            "kind": "image",
            "filename": filename,
            "sha256": digest,
            "availability": "available",
        }],
    })


def _client(monkeypatch):
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "media-admin-secret")
    from admin.admin_server import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


def _scoped(sandbox, raw, scopes):
    from admin import token_registry
    import yaml

    token_registry._records = None
    token_registry._mtime = None
    path = sandbox.auth_tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({
        "tokens": [{
            "label": "t-media",
            "hash": token_registry.hash_token(raw),
            "scopes": scopes,
        }],
    }), encoding="utf-8")


def test_gc_skips_live_event_refs_and_deletes_unreferenced(sandbox, monkeypatch):
    from core import media_processor
    from core.chat_media import invalidate_live_media_cache
    from core.sandbox import get_paths

    live = PNG
    stale = PNG + b"stale"
    live_digest = _digest(live)
    inbox = get_paths().inbox_dir()
    live_path = inbox / "live.png"
    stale_path = inbox / "stale.png"
    live_path.write_bytes(live)
    stale_path.write_bytes(stale)
    cache = get_paths().image_cache_dir()
    (cache / f"{live_digest}.json").write_text(
        '{"description":"x","created_at":0,"source_filename":"live.png","image_path":"%s"}'
        % live_path.as_posix().replace("\\", "\\\\"),
        encoding="utf-8",
    )
    stale_cache = cache / ("b" * 64 + ".json")
    stale_cache.write_text('{"description":"y","created_at":0}', encoding="utf-8")
    monkeypatch.setattr(Path, "stat", Path.stat)
    import os
    old_mtime = 1.0
    os.utime(live_path, (old_mtime, old_mtime))
    os.utime(stale_path, (old_mtime, old_mtime))
    os.utime(cache / f"{live_digest}.json", (old_mtime, old_mtime))
    os.utime(stale_cache, (old_mtime, old_mtime))
    _append_ref("media-owner", live_digest)
    invalidate_live_media_cache()

    assert media_processor.gc_inbox(max_age_days=0) == 1
    assert live_path.exists()
    assert not stale_path.exists()
    assert media_processor.gc_image_cache(max_age_days=0, max_files=500) == 1
    assert (cache / f"{live_digest}.json").exists()
    assert not stale_cache.exists()


def test_gc_protects_retained_library_blob(sandbox, monkeypatch):
    from core import media_processor
    from core.character_document_library import store_upload
    from core.chat_media import invalidate_live_media_cache
    from core.sandbox import get_paths

    monkeypatch.setattr(
        "core.character_document_library._config",
        lambda: {"retain_raw_uploads": True},
    )
    digest = _digest(PNG)
    store_upload(
        uid="media-owner", char_id=TEST_CHAR_ID, filename="kept.png",
        media_type="image/png", sha256=digest, searchable_text="a picture",
        source="upload_image", raw_bytes=PNG,
    )
    inbox = get_paths().inbox_dir() / "kept.png"
    inbox.write_bytes(PNG)
    import os
    os.utime(inbox, (1.0, 1.0))
    invalidate_live_media_cache()
    assert media_processor.gc_inbox(max_age_days=0) == 0
    assert inbox.exists()


def test_download_and_observability_scopes(sandbox, monkeypatch):
    from core.config_loader import get_config
    from core.sandbox import get_paths
    import core.pipeline_registry as pipeline_registry

    digest = _digest(PNG)
    path = get_paths().inbox_dir() / "photo.png"
    path.write_bytes(PNG)
    _append_ref("test_owner", digest)
    monkeypatch.setattr("core.config_loader.get_config", lambda: {
        **get_config(),
        "scheduler": {**get_config().get("scheduler", {}), "owner_id": "test_owner"},
    })

    class _Pipe:
        _active_character_id = TEST_CHAR_ID

    monkeypatch.setattr(pipeline_registry, "get", lambda: _Pipe())
    client = _client(monkeypatch)

    assert client.get(f"/chat/media/{digest}").status_code == 401
    assert client.get("/observability/chat-media").status_code == 401

    _scoped(sandbox, "emt_chat", ["chat"])
    chat_headers = {"Authorization": "Bearer emt_chat"}
    download = client.get(f"/chat/media/{digest}", headers=chat_headers)
    assert download.status_code == 200
    assert download.content == PNG
    assert download.headers.get("x-content-type-options") == "nosniff"
    assert "no-store" in download.headers.get("cache-control", "")
    assert client.get(
        "/observability/chat-media",
        headers=chat_headers,
    ).status_code == 403

    missing = client.get("/chat/media/" + ("c" * 64), headers=chat_headers)
    assert missing.status_code == 410
    invalid = client.get("/chat/media/not-a-hash", headers=chat_headers)
    assert invalid.status_code == 422

    _scoped(sandbox, "emt_state", ["state.read"])
    state_headers = {"Authorization": "Bearer emt_state"}
    obs = client.get("/observability/chat-media?uid=test_owner&char_id=" + TEST_CHAR_ID, headers=state_headers)
    assert obs.status_code == 200
    body = obs.json()
    assert body["identity"] == "sha256"
    assert body["retention"]["live_ref_guard"] is True
    assert "photo.png" not in str(body)
    assert str(path) not in str(body)
    assert client.get(f"/chat/media/{digest}", headers=state_headers).status_code == 403


def test_upload_ingest_omits_stored_paths(sandbox, monkeypatch):
    from admin.routers import chat as chat_router

    async def _fake_ingest(items, *, uid="", char_id=""):
        return ["scene"]

    async def _fake_turn(*_args, **kwargs):
        return {"reply": "ok", "turn_id": "t1", "msg_id": "t1"}

    monkeypatch.setattr("core.media_processor.ingest_image_bytes", _fake_ingest)
    monkeypatch.setattr(chat_router, "run_owner_chat_turn", _fake_turn)
    client = _client(monkeypatch)
    _scoped(sandbox, "emt_chat", ["chat"])
    response = client.post(
        "/upload/ingest",
        headers={"Authorization": "Bearer emt_chat"},
        files=[("files", ("photo.png", PNG, "image/png"))],
        data={"channel": "mobile"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "stored_path" not in body
    assert "stored_paths" not in body
    assert body["media_refs"][0]["sha256"] == _digest(PNG)
    assert "kind" in body["media_refs"][0]


def test_tombstone_releases_live_ref_for_gc(sandbox):
    from core import media_processor
    from core.chat_media import invalidate_live_media_cache
    from core.memory.event_store import tombstone_event
    from core.memory.scope import MemoryScope
    from core.sandbox import get_paths

    digest = _digest(PNG)
    inbox = get_paths().inbox_dir() / "live.png"
    inbox.write_bytes(PNG)
    import os
    os.utime(inbox, (1.0, 1.0))
    _append_ref("media-owner", digest)
    invalidate_live_media_cache()
    assert media_processor.gc_inbox(max_age_days=0) == 0
    assert inbox.exists()
    result = tombstone_event(
        MemoryScope.reality_scope("media-owner", TEST_CHAR_ID),
        f"{digest[:12]}:user",
    )
    assert result.ok and result.changed
    assert media_processor.gc_inbox(max_age_days=0) == 1
    assert not inbox.exists()


def test_admin_observe_page_uses_authenticated_media_fetch():
    from pathlib import Path as _Path

    source = (_Path(__file__).parents[1] / "admin" / "static" / "js" / "observability.js").read_text(encoding="utf-8")
    page = (_Path(__file__).parents[1] / "admin" / "static" / "pages" / "observe-chat-media.html").read_text(encoding="utf-8")
    assert "loadObserveChatMedia" in source
    assert "await api('GET', `/observability/chat-media" in source
    assert "window.loadObserveChatMedia = loadObserveChatMedia" in source
    assert 'id="obs-chat-media-body"' in page
    assert "Authorization: `Bearer ${TOKEN}`" not in page
