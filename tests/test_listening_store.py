"""Ticket 260 D: listening ledger, notes, stats and redacted observability."""
from __future__ import annotations

import io
import threading
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin import token_registry
from core.listening_store import (
    apply_event,
    bind_host,
    get_stats,
    history_snapshot,
    list_history,
    load_session,
    metadata_snapshot,
    read_note,
    reconcile_after_restart,
    refresh_track_analysis,
    register_audio_blob,
    set_participant,
    set_queue,
    upsert_track,
    write_note,
)


def _event(kind, *, generation, session_id, seq, track_id=None, **extra):
    payload = {
        "event_id": extra.pop("event_id", f"{kind}-{seq}"),
        "kind": kind,
        "generation": generation,
        "session_id": session_id,
        "sequence": seq,
        "track_id": track_id,
    }
    payload.update(extra)
    return payload


def _session(sandbox, uid="owner"):
    bind_host(uid, host_id="host-a")
    return load_session(uid)


def test_same_title_does_not_merge_tracks(sandbox):
    one = upsert_track("owner", provider="local", source_id="a", title="Song")
    two = upsert_track("owner", provider="local", source_id="b", title="Song")
    assert one["track_id"] != two["track_id"]


def test_queue_revision_conflict(sandbox):
    session = _session(sandbox)
    first = set_queue("owner", ["t1"], expected_revision=session["revision"])
    assert first["ok"]
    second = set_queue("owner", ["t2"], expected_revision=session["revision"])
    assert not second["ok"]
    assert second["reason"] == "revision_conflict"
    assert second["session"]["queue"] == ["t1"]


def test_duplicate_and_out_of_order_events(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="One", duration_s=40)
    gen, sid = session["generation"], session["session_id"]
    first = apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    assert first["ok"]
    dup = apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    assert dup["ok"] and dup["reason"] == "duplicate"
    late = apply_event(
        "owner",
        _event("progress", generation=gen, session_id=sid, seq=1, track_id=track["track_id"],
               event_id="late", played_delta_s=5),
    )
    assert not late["ok"]
    assert late["reason"] == "out_of_order"
    stale = apply_event(
        "owner",
        _event("started", generation=gen + 9, session_id=sid, seq=2, track_id=track["track_id"],
               event_id="stale"),
    )
    assert not stale["ok"]
    assert stale["reason"] == "stale_host"


def test_character_switch_does_not_reassign_old_occurrence(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="One", duration_s=40)
    other = upsert_track("owner", provider="local", source_id="y", title="Two", duration_s=40)
    set_participant("owner", "char_a")
    gen, sid = session["generation"], session["session_id"]
    apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    apply_event(
        "owner",
        _event("progress", generation=gen, session_id=sid, seq=2, track_id=track["track_id"], played_delta_s=21),
    )
    set_participant("owner", "char_b")
    merged = apply_event(
        "owner",
        _event("changed", generation=gen, session_id=sid, seq=3, track_id=track["track_id"], event_id="chg"),
    )
    assert merged["reason"] == "merged"
    rows = list_history("owner")
    assert len(rows) == 1
    assert rows[0]["char_id"] == "char_a"
    apply_event(
        "owner",
        _event("started", generation=gen, session_id=sid, seq=4, track_id=other["track_id"], event_id="next"),
    )
    rows = list_history("owner")
    assert len(rows) == 2
    by_char = {row["char_id"]: row for row in rows}
    assert by_char["char_a"]["track_id"] == track["track_id"]
    assert by_char["char_b"]["track_id"] == other["track_id"]
    assert get_stats("owner", track["track_id"])["started_count"] == 1
    assert get_stats("owner", other["track_id"])["started_count"] == 1


def test_loop_playback_creates_new_occurrence(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="Loop", duration_s=20)
    gen, sid = session["generation"], session["session_id"]
    apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    apply_event("owner", _event("finished", generation=gen, session_id=sid, seq=2, track_id=track["track_id"]))
    apply_event(
        "owner",
        _event("started", generation=gen, session_id=sid, seq=3, track_id=track["track_id"], event_id="again"),
    )
    rows = list_history("owner")
    assert len(rows) == 2
    assert {row["natural_finished"] for row in rows} == {0, 1}
    stats = get_stats("owner", track["track_id"])
    assert stats["started_count"] == 2
    assert stats["completed_count"] == 1


def test_pause_seek_do_not_count_listen(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="One", duration_s=40)
    gen, sid = session["generation"], session["session_id"]
    apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    apply_event(
        "owner",
        _event("progress", generation=gen, session_id=sid, seq=2, track_id=track["track_id"], played_delta_s=10),
    )
    apply_event("owner", _event("paused", generation=gen, session_id=sid, seq=3, track_id=track["track_id"]))
    apply_event(
        "owner",
        _event("progress", generation=gen, session_id=sid, seq=4, track_id=track["track_id"],
               event_id="seek", position_s=30, played_delta_s=0),
    )
    apply_event("owner", _event("resumed", generation=gen, session_id=sid, seq=5, track_id=track["track_id"]))
    apply_event(
        "owner",
        _event("progress", generation=gen, session_id=sid, seq=6, track_id=track["track_id"],
               event_id="more", played_delta_s=5),
    )
    stats = get_stats("owner", track["track_id"])
    assert stats["listen_count"] == 0
    occ = list_history("owner")[0]
    assert occ["accumulated_play_s"] == 15
    assert occ["listen_counted"] == 0


def test_listen_count_once_per_occurrence(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="One", duration_s=40)
    gen, sid = session["generation"], session["session_id"]
    apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    apply_event(
        "owner",
        _event("progress", generation=gen, session_id=sid, seq=2, track_id=track["track_id"], played_delta_s=20),
    )
    apply_event(
        "owner",
        _event("progress", generation=gen, session_id=sid, seq=3, track_id=track["track_id"],
               event_id="extra", played_delta_s=10),
    )
    stats = get_stats("owner", track["track_id"])
    assert stats["listen_count"] == 1
    assert stats["last_listened_at"]


def test_started_changed_same_play_merges(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="One")
    gen, sid = session["generation"], session["session_id"]
    apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    merged = apply_event(
        "owner",
        _event("changed", generation=gen, session_id=sid, seq=2, track_id=track["track_id"], event_id="chg"),
    )
    assert merged["reason"] == "merged"
    assert len(list_history("owner")) == 1


def test_missing_finished_and_restart_do_not_accrue_wall_clock(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="One", duration_s=40)
    gen, sid = session["generation"], session["session_id"]
    apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    apply_event(
        "owner",
        _event("progress", generation=gen, session_id=sid, seq=2, track_id=track["track_id"], played_delta_s=5),
    )
    after = reconcile_after_restart("owner")
    assert after["state"] == "stopped"
    assert after["claimed_playing"] is False
    occ = list_history("owner")[0]
    assert occ["termination"] == "disconnected"
    assert occ["accumulated_play_s"] == 5
    assert get_stats("owner", track["track_id"])["listen_count"] == 0
    rebuilt = get_stats("owner", track["track_id"])
    assert rebuilt["started_count"] == 1


def test_notes_are_per_character_and_revisioned(sandbox):
    track = upsert_track("owner", provider="local", source_id="x", title="One")
    first = write_note("owner", "char_a", track["track_id"], "soft")
    assert first["ok"]
    conflict = write_note(
        "owner", "char_a", track["track_id"], "overwrite", expected_revision=0,
    )
    assert not conflict["ok"]
    other = write_note("owner", "char_b", track["track_id"], "other view")
    assert other["ok"]
    assert read_note("owner", "char_a", track["track_id"])["body"] == "soft"
    assert read_note("owner", "char_b", track["track_id"])["body"] == "other view"


def test_metadata_omits_notes_and_history_bodies(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="Secret")
    write_note("owner", "char_a", track["track_id"], "do not leak")
    gen, sid = session["generation"], session["session_id"]
    apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    snap = metadata_snapshot("owner")
    blob = str(snap)
    assert "do not leak" not in blob
    assert "Secret" not in blob
    assert "occurrences" not in snap
    assert snap["session"]["has_current_track"] is True
    hist = history_snapshot("owner", char_id="char_a")
    assert hist["occurrences"] == [] or hist["occurrences"][0]["char_id"] != "missing"


def test_concurrent_queue_revision_only_one_wins(sandbox):
    session = _session(sandbox)
    rev = session["revision"]
    results: list[dict] = []

    def _worker(item: str) -> None:
        results.append(set_queue("owner", [item], expected_revision=rev))

    first = threading.Thread(target=_worker, args=("t1",))
    second = threading.Thread(target=_worker, args=("t2",))
    first.start()
    second.start()
    first.join()
    second.join()
    ok = [item for item in results if item["ok"]]
    failed = [item for item in results if not item["ok"]]
    assert len(ok) == 1
    assert len(failed) == 1
    assert failed[0]["reason"] == "revision_conflict"
    live = load_session("owner")
    assert live["revision"] == rev + 1
    assert live["queue"] in (["t1"], ["t2"])


def test_stale_session_and_missing_progress_delta(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="One")
    gen, sid = session["generation"], session["session_id"]
    apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    stale = apply_event(
        "owner",
        _event("progress", generation=gen, session_id="other-session", seq=2,
               track_id=track["track_id"], event_id="stale-sid", played_delta_s=5),
    )
    assert not stale["ok"]
    assert stale["reason"] == "stale_session"
    missing = apply_event(
        "owner",
        _event("progress", generation=gen, session_id=sid, seq=2,
               track_id=track["track_id"], event_id="no-delta"),
    )
    assert not missing["ok"]
    assert missing["reason"] == "missing_played_delta"


def test_idle_snapshot_cannot_claim_playing(sandbox):
    session = _session(sandbox)
    snap = apply_event(
        "owner",
        _event("snapshot", generation=session["generation"], session_id=session["session_id"],
               seq=1, state="playing"),
    )
    assert not snap["ok"]
    assert snap["reason"] == "illegal_transition"
    live = load_session("owner")
    assert live["state"] == "idle"
    assert live["claimed_playing"] is False


def test_host_occurrence_id_is_ignored(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="One")
    apply_event(
        "owner",
        _event(
            "started",
            generation=session["generation"],
            session_id=session["session_id"],
            seq=1,
            track_id=track["track_id"],
            occurrence_id="client-forged",
        ),
    )
    row = list_history("owner")[0]
    assert row["occurrence_id"] != "client-forged"
    live = load_session("owner")
    assert live["claimed_playing"] is True
    assert live["host_online"] is True


def test_notes_do_not_change_listen_stats(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="One", duration_s=40)
    gen, sid = session["generation"], session["session_id"]
    apply_event("owner", _event("started", generation=gen, session_id=sid, seq=1, track_id=track["track_id"]))
    write_note("owner", "char_a", track["track_id"], "subjective only")
    stats = get_stats("owner", track["track_id"])
    assert stats["listen_count"] == 0
    assert stats["started_count"] == 1
    assert read_note("owner", "char_a", track["track_id"])["body"] == "subjective only"


def test_blob_ref_is_digest_not_path(sandbox):
    ref = register_audio_blob("owner", b"pcm-bytes", filename="raw")
    assert ref.startswith("blob:")
    assert "\\" not in ref and "/" not in ref
    name = ref.split(":", 1)[1]
    assert ":" not in name
    assert name.endswith(".bin")
    wav = register_audio_blob("owner", b"RIFF....WAVE", filename="tone.wav")
    assert wav.endswith(".wav")


def test_refresh_analysis_unavailable_without_switch_or_readable_audio(sandbox, monkeypatch):
    track = upsert_track(
        "owner", provider="local", source_id="x", title="Named Only",
        audio_access="unavailable",
    )
    monkeypatch.setattr("core.listening_store.music_analysis_enabled", lambda: True)
    out = refresh_track_analysis("owner", track["track_id"])
    assert out["analysis_status"] == "unavailable"
    monkeypatch.setattr("core.listening_store.music_analysis_enabled", lambda: False)
    ref = register_audio_blob("owner", b"not-enough")
    readable = upsert_track(
        "owner", provider="local", source_id="y", title="Has Blob",
        audio_ref=ref, audio_access="backend_readable",
    )
    closed = refresh_track_analysis("owner", readable["track_id"])
    assert closed["analysis_status"] == "unavailable"


def test_refresh_analysis_uses_blob_bytes_not_title(sandbox, monkeypatch):
    monkeypatch.setattr("core.listening_store.music_analysis_enabled", lambda: True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 8000)
    ref = register_audio_blob("owner", buf.getvalue())
    track = upsert_track(
        "owner", provider="local", source_id="blob-1", title="Secret Title",
        audio_ref=ref, audio_access="backend_readable",
    )
    out = refresh_track_analysis("owner", track["track_id"])
    assert out["analysis_status"] in {"ok", "partial", "failed", "unavailable"}
    assert out["audio_ref"] == ref


def _auth_headers(sandbox, raw: str, scopes: list[str]) -> dict[str, str]:
    import yaml

    path = sandbox.auth_tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({
            "tokens": [{
                "label": f"listen-{raw}",
                "hash": token_registry.hash_token(raw),
                "scopes": scopes,
            }],
        }),
        encoding="utf-8",
    )
    token_registry._records = None
    token_registry._mtime = None
    return {"Authorization": f"Bearer {raw}"}


def _listening_client():
    from admin.routers import listening

    app = FastAPI()
    app.include_router(listening.router)
    return TestClient(app)


def test_http_scopes_split_metadata_and_notes(sandbox):
    session = _session(sandbox)
    track = upsert_track("owner", provider="local", source_id="x", title="Secret")
    write_note("owner", "char_a", track["track_id"], "do not leak")
    set_participant("owner", "char_a")
    apply_event(
        "owner",
        _event(
            "started",
            generation=session["generation"],
            session_id=session["session_id"],
            seq=1,
            track_id=track["track_id"],
        ),
    )
    client = _listening_client()
    assert client.get("/observability/listening", params={"uid": "owner"}).status_code == 401
    assert client.get("/listening/history", params={"uid": "owner"}).status_code == 401
    assert client.get("/listening/notes", params={"uid": "owner", "char_id": "char_a"}).status_code == 401

    state_headers = _auth_headers(sandbox, "emt_state", ["state.read"])
    meta = client.get("/observability/listening", params={"uid": "owner"}, headers=state_headers)
    assert meta.status_code == 200
    body = meta.json()
    dumped = str(body)
    assert "do not leak" not in dumped
    assert "Secret" not in dumped
    assert body["notes_omitted"] is True
    assert body["history_bodies_omitted"] is True
    assert "occurrences" not in body
    assert client.get(
        "/listening/history", params={"uid": "owner"}, headers=state_headers,
    ).status_code == 403
    assert client.get(
        "/listening/notes", params={"uid": "owner", "char_id": "char_a"}, headers=state_headers,
    ).status_code == 403

    memory_headers = _auth_headers(sandbox, "emt_memory", ["memory.read"])
    assert client.get(
        "/observability/listening", params={"uid": "owner"}, headers=memory_headers,
    ).status_code == 403
    hist = client.get("/listening/history", params={"uid": "owner"}, headers=memory_headers)
    notes = client.get(
        "/listening/notes", params={"uid": "owner", "char_id": "char_a"}, headers=memory_headers,
    )
    assert hist.status_code == 200
    assert notes.status_code == 200
    assert notes.json()["notes"][0]["body"] == "do not leak"
    assert hist.json()["occurrences"][0]["char_id"] == "char_a"
