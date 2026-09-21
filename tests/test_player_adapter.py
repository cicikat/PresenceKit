"""Ticket 260 E: Player Adapter command ledger and fake-host regression.

The fake host is allowed here. It does not close the construction ticket;
the first-party admin HTMLAudioElement is the real host.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin import token_registry
from admin.auth import reset_rate_limit_state_for_test
from core.listening_store import get_stats, list_history, load_session, upsert_track
from core.player_adapter import (
    FakePlayerHost,
    dispatch_command,
    mark_outcome_unknown,
    music_control_enabled,
    register_host,
)


def test_music_control_defaults_off():
    assert music_control_enabled() is False


def test_fake_play_pause_resume_stop_and_finish(sandbox, monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    track = upsert_track("owner", provider="local", source_id="a", title="One", duration_s=40)
    host = FakePlayerHost("owner")
    played = host.play(track["track_id"])
    assert played["accepted"] is True
    assert played["event"]["ok"] is True
    assert load_session("owner")["state"] == "playing"
    paused = host.pause()
    assert paused["ok"] is True
    assert load_session("owner")["state"] == "paused"
    host.resume()
    assert load_session("owner")["state"] == "playing"
    host.progress(5, position_s=5)
    host.stop()
    assert load_session("owner")["state"] == "stopped"
    assert list_history("owner")[0]["termination"] == "stopped"


def test_duplicate_command_does_not_replay_next(sandbox, monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    one = upsert_track("owner", provider="local", source_id="a", title="One")
    two = upsert_track("owner", provider="local", source_id="b", title="Two")
    host = FakePlayerHost("owner")
    session = load_session("owner")
    queued = dispatch_command("owner", {
        "command_id": "q1",
        "action": "set_queue",
        "generation": session["generation"],
        "session_id": session["session_id"],
        "expected_revision": session["revision"],
        "args": {"queue": [one["track_id"], two["track_id"]]},
    })
    assert queued["outcome"] == "confirmed"
    host.play(one["track_id"])
    session = load_session("owner")
    first = dispatch_command("owner", {
        "command_id": "n1",
        "action": "next",
        "generation": session["generation"],
        "session_id": session["session_id"],
        "expected_revision": session["revision"],
    })
    assert first["accepted"] is True
    replay = dispatch_command("owner", {
        "command_id": "n1",
        "action": "next",
        "generation": session["generation"],
        "session_id": session["session_id"],
        "expected_revision": session["revision"],
    })
    assert replay["reason"] == "duplicate"
    assert load_session("owner")["track_id"] == two["track_id"]
    assert load_session("owner")["revision"] == first["session"]["revision"]


def test_stale_host_and_revision_conflict(sandbox, monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    track = upsert_track("owner", provider="local", source_id="a", title="One")
    first = register_host("owner", host_id="host-a")
    dispatch_command("owner", {
        "command_id": "p1",
        "action": "play",
        "generation": first["session"]["generation"],
        "session_id": first["session"]["session_id"],
        "args": {"track_id": track["track_id"]},
    })
    second = register_host("owner", host_id="host-b")
    stale = dispatch_command("owner", {
        "command_id": "p2",
        "action": "play",
        "generation": first["session"]["generation"],
        "session_id": first["session"]["session_id"],
        "args": {"track_id": track["track_id"]},
    })
    assert stale["reason"] == "stale_host"
    conflict = dispatch_command("owner", {
        "command_id": "q2",
        "action": "set_queue",
        "generation": second["session"]["generation"],
        "session_id": second["session"]["session_id"],
        "expected_revision": 99,
        "args": {"queue": [track["track_id"]]},
    })
    assert conflict["reason"] == "revision_conflict"


def test_unknown_outcome_next_is_not_auto_replayed(sandbox, monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    one = upsert_track("owner", provider="local", source_id="a", title="One")
    two = upsert_track("owner", provider="local", source_id="b", title="Two")
    host = FakePlayerHost("owner")
    session = load_session("owner")
    dispatch_command("owner", {
        "command_id": "q1",
        "action": "set_queue",
        "generation": session["generation"],
        "session_id": session["session_id"],
        "expected_revision": session["revision"],
        "args": {"queue": [one["track_id"], two["track_id"]]},
    })
    host.play(one["track_id"])
    session = load_session("owner")
    nxt = dispatch_command("owner", {
        "command_id": "n-unknown",
        "action": "next",
        "generation": session["generation"],
        "session_id": session["session_id"],
        "expected_revision": session["revision"],
    })
    assert nxt["outcome"] == "accepted"
    marked = mark_outcome_unknown("owner", "n-unknown")
    assert marked["record"]["outcome"] == "outcome_unknown"
    replay = dispatch_command("owner", {
        "command_id": "n-unknown",
        "action": "next",
        "generation": session["generation"],
        "session_id": session["session_id"],
        "expected_revision": session["revision"],
    })
    assert replay["reason"] == "duplicate"
    assert replay["outcome"] == "outcome_unknown"


def test_progress_uses_played_delta_not_position(sandbox, monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    track = upsert_track("owner", provider="local", source_id="a", title="One", duration_s=40)
    host = FakePlayerHost("owner")
    host.play(track["track_id"])
    host.progress(4, position_s=20)
    occ = list_history("owner")[0]
    assert occ["accumulated_play_s"] == 4
    assert get_stats("owner", track["track_id"])["listen_count"] == 0


def test_music_control_off_rejects_play(sandbox, monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: False)
    track = upsert_track("owner", provider="local", source_id="a", title="One")
    session = register_host("owner", host_id="host-a")["session"]
    result = dispatch_command("owner", {
        "command_id": "off",
        "action": "play",
        "generation": session["generation"],
        "session_id": session["session_id"],
        "args": {"track_id": track["track_id"]},
    })
    assert result["reason"] == "music_control_disabled"


def _auth_headers(sandbox, entries: list[tuple[str, list[str]]]) -> dict[str, dict[str, str]]:
    import yaml

    path = sandbox.auth_tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({
            "tokens": [{
                "label": f"player-{raw}",
                "hash": token_registry.hash_token(raw),
                "scopes": scopes,
            } for raw, scopes in entries],
        }),
        encoding="utf-8",
    )
    token_registry._records = None
    token_registry._mtime = None
    reset_rate_limit_state_for_test()
    return {raw: {"Authorization": f"Bearer {raw}"} for raw, _scopes in entries}


def test_player_http_scopes(sandbox, monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    from admin.routers import listening

    app = FastAPI()
    app.include_router(listening.router)
    client = TestClient(app)
    tokens = _auth_headers(sandbox, [
        ("emt_state", ["state.read"]),
        ("emt_admin", ["admin"]),
    ])
    state = tokens["emt_state"]
    admin = tokens["emt_admin"]
    missing = client.get("/player/state", params={"uid": "owner"})
    assert missing.status_code in {401, 429}
    reset_rate_limit_state_for_test()
    assert client.get("/player/state", params={"uid": "owner"}, headers=state).status_code == 200
    denied = client.post(
        "/player/host/bind", json={"uid": "owner", "host_id": "h1"}, headers=state,
    )
    assert denied.status_code == 403
    bound = client.post(
        "/player/host/bind", json={"uid": "owner", "host_id": "h1"}, headers=admin,
    )
    assert bound.status_code == 200
    assert bound.json()["session"]["host_id"] == "h1"


def test_causation_confirms_accepted_command(sandbox, monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    track = upsert_track("owner", provider="local", source_id="a", title="One")
    host = FakePlayerHost("owner")
    played = host.play(track["track_id"])
    assert played["event"]["ok"] is True
    cached = next(
        item for item in load_session("owner")["command_cache"]
        if item["command_id"] == played["command_id"]
    )
    assert cached["outcome"] == "confirmed"


def test_offline_host_does_not_accrue_listen_time(sandbox, monkeypatch):
    from core.player_adapter import ingest_host_event, mark_host_offline

    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    track = upsert_track("owner", provider="local", source_id="a", title="One", duration_s=40)
    host = FakePlayerHost("owner")
    host.play(track["track_id"])
    host.progress(4, position_s=4)
    session = mark_host_offline("owner")
    assert session["host_online"] is False
    rejected = ingest_host_event("owner", {
        "event_id": "after-offline",
        "kind": "progress",
        "generation": session["generation"],
        "session_id": session["session_id"],
        "sequence": 99,
        "track_id": track["track_id"],
        "played_delta_s": 20,
        "position_s": 24,
    })
    assert rejected["ok"] is False
    assert rejected["reason"] == "host_offline"
    occ = list_history("owner")[0]
    assert occ["accumulated_play_s"] == 4
    assert get_stats("owner", track["track_id"])["listen_count"] == 0


def test_fake_capabilities_cannot_close_e(sandbox, monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    host = FakePlayerHost("owner")
    caps = host.capabilities()
    assert caps["adapter"] == "fake"
    assert caps["mock_closes_e"] is False
    assert caps["disconnect_policy"] == "local_may_continue_unsynced"
    assert caps["tts_queue_is_music_host"] is False
    assert caps["netease_or_media_key"] is False


def test_player_http_upload_and_audio(sandbox, monkeypatch):
    import io
    import wave

    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    from admin.routers import listening

    app = FastAPI()
    app.include_router(listening.router)
    client = TestClient(app)
    admin = _auth_headers(sandbox, [("emt_admin", ["admin"])])["emt_admin"]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 800)
    uploaded = client.post(
        "/player/tracks",
        data={"uid": "owner", "title": "Tone"},
        files={"file": ("tone.wav", buf.getvalue(), "audio/wav")},
        headers=admin,
    )
    assert uploaded.status_code == 200
    track = uploaded.json()["track"]
    assert track["audio_access"] == "backend_readable"
    assert str(track["audio_ref"]).startswith("blob:")
    library = client.get("/player/library", params={"uid": "owner"}, headers=admin)
    assert library.status_code == 200
    assert library.json()["tracks"][0]["has_blob"] is True
    audio = client.get(
        f"/player/audio/{track['track_id']}",
        params={"uid": "owner"},
        headers=admin,
    )
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert audio.content[:4] == b"RIFF"


def test_observability_listening_exposes_switch_effective_state(sandbox, monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    from admin.routers import listening

    app = FastAPI()
    app.include_router(listening.router)
    client = TestClient(app)
    state = _auth_headers(sandbox, [("emt_state", ["state.read"])])["emt_state"]
    body = client.get("/observability/listening", params={"uid": "owner"}, headers=state).json()
    assert body["notes_omitted"] is True
    assert body["history_bodies_omitted"] is True
    assert body["music_control_enabled"] is True
    assert body["audio_music"]["music_control"]["effective_state"] == "enabled"
    assert body["audio_music"]["speech_analysis"]["desired_enabled"] is False
    assert body["capabilities"]["adapter"] == "first_party_admin"
    assert body["capabilities"]["mock_closes_e"] is False


def test_observe_listening_page_is_registered_and_cache_busted():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    index = (root / "admin/static/index.html").read_text(encoding="utf-8")
    core = (root / "admin/static/js/core.js").read_text(encoding="utf-8")
    script = (root / "admin/static/js/observability.js").read_text(encoding="utf-8")
    fragment = (root / "admin/static/pages/observe-listening.html").read_text(encoding="utf-8")
    i18n = (root / "admin/static/i18n.js").read_text(encoding="utf-8")

    assert 'data-page="observe-listening"' in index
    assert 'id="page-observe-listening"' in index
    assert "ADMIN_UI_FRAGMENT_VERSION = 'v1-260-admin-surface-1'" in core
    assert '<script src="/static/js/core.js?v=v1-260-admin-surface-1"></script>' in index
    assert '<script src="/static/js/observability.js?v=v1-260-admin-surface-1"></script>' in index
    assert '<script src="/static/js/listening-player.js?v=v1-260-admin-surface-1"></script>' in index
    assert "loadObserveListening" in script
    assert "loadObserveListening" in core
    assert 'id="obs-listening-flags"' in fragment
    assert 'id="obs-listening-player"' in fragment
    assert "/observability/listening" in script
    assert "'page_context.observe-listening.purpose'" in i18n
    assert "'flag.speech_analysis'" in i18n
    assert "'flag.music_control'" in i18n
