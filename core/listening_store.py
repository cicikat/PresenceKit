"""Owner listening ledger, character notes and derived stats (ticket 260 D).

Playback events are committed here before any autonomy candidate. Counts rebuild
from history; pause/seek/wall-clock never become listen time. Character notes
are isolated from listen counts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any

from core.audio_music_contract import (
    ADAPTER_EVENTS,
    ANALYSIS_STATUSES,
    ANALYSIS_VERSION,
    AUDIO_ACCESS,
    AUDIO_BLOB_BUDGET_BYTES,
    COMMAND_RESULT_CACHE_CAP,
    CONFIG_ROOT,
    COUNT_FIELDS,
    HISTORY_OCCURRENCE_CAP,
    HOST_EVENT_RING_CAP,
    LIBRARY_TRACK_CAP,
    LISTEN_THRESHOLD_VERSION,
    LOCK_NAMES,
    NOTE_MAX_CHARS,
    PLAYBACK_STATES,
    TERMINATION_REASONS,
    is_legal_transition,
    listen_threshold_seconds,
    should_count_listen,
)
from core.sandbox import get_paths
from core.safe_write import safe_write_json

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    track_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT,
    duration_s REAL,
    audio_ref TEXT,
    audio_access TEXT NOT NULL,
    analysis_status TEXT NOT NULL,
    analysis_version TEXT,
    created_at REAL NOT NULL,
    UNIQUE(provider, source_id)
);
CREATE TABLE IF NOT EXISTS occurrences (
    occurrence_id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL,
    char_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    accumulated_play_s REAL NOT NULL DEFAULT 0,
    termination TEXT,
    natural_finished INTEGER NOT NULL DEFAULT 0,
    listen_counted INTEGER NOT NULL DEFAULT 0,
    last_listened_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL,
    kind TEXT NOT NULL,
    occurrence_id TEXT,
    track_id TEXT,
    accepted INTEGER NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stats (
    track_id TEXT PRIMARY KEY,
    started_count INTEGER NOT NULL DEFAULT 0,
    listen_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    last_listened_at REAL
);
CREATE TABLE IF NOT EXISTS notes (
    track_id TEXT NOT NULL,
    body TEXT NOT NULL,
    revision INTEGER NOT NULL,
    occurrence_id TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY(track_id)
);
"""

_locks: dict[str, threading.RLock] = {}
_lock_guard = threading.Lock()


def _thread_lock(name: str) -> threading.RLock:
    with _lock_guard:
        lock = _locks.get(name)
        if lock is None:
            lock = threading.RLock()
            _locks[name] = lock
        return lock


def listening_lock(uid: str) -> threading.RLock:
    return _thread_lock(LOCK_NAMES["listening"].format(uid=str(uid)))


def notes_lock(uid: str, char_id: str) -> threading.RLock:
    return _thread_lock(LOCK_NAMES["track_notes"].format(char_id=str(char_id), uid=str(uid)))


def _owner_uid(uid: str) -> str:
    value = str(uid or "").strip()
    if not value:
        raise ValueError("uid required")
    return value


def _connect(path, *, write: bool) -> sqlite3.Connection:
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=2.0)
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(_SCHEMA)
        db.execute("BEGIN IMMEDIATE")
    elif not path.exists():
        db = sqlite3.connect(":memory:")
        db.executescript(_SCHEMA)
    else:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)
    db.row_factory = sqlite3.Row
    return db


@contextmanager
def _history(uid: str, *, write: bool = False):
    path = get_paths().listening_history_db(uid)
    db = _connect(path, write=write)
    try:
        yield db
        if write:
            db.commit()
    except BaseException:
        if write:
            db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def _library(uid: str, *, write: bool = False):
    path = get_paths().music_library_db(uid)
    db = _connect(path, write=write)
    try:
        yield db
        if write:
            db.commit()
    except BaseException:
        if write:
            db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def _stats_db(uid: str, *, write: bool = False):
    path = get_paths().listening_stats_db(uid)
    db = _connect(path, write=write)
    try:
        yield db
        if write:
            db.commit()
    except BaseException:
        if write:
            db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def _notes_db(uid: str, char_id: str, *, write: bool = False):
    path = get_paths().character_track_notes(uid, char_id=char_id)
    db = _connect(path, write=write)
    try:
        yield db
        if write:
            db.commit()
    except BaseException:
        if write:
            db.rollback()
        raise
    finally:
        db.close()


def _empty_session(uid: str) -> dict[str, Any]:
    return {
        "uid": uid,
        "session_id": "",
        "generation": 0,
        "host_id": "",
        "adapter": "",
        "device": "",
        "participant_char_id": "",
        "state": "idle",
        "track_id": None,
        "occurrence_id": None,
        "position_s": 0.0,
        "queue": [],
        "revision": 0,
        "last_sequence": 0,
        "host_online": False,
        "claimed_playing": False,
        "command_cache": [],
        "updated_at": 0.0,
    }


def load_session(uid: str) -> dict[str, Any]:
    uid = _owner_uid(uid)
    path = get_paths().listening_session(uid)
    if not path.is_file():
        return _empty_session(uid)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_session(uid)
    session = _empty_session(uid)
    if isinstance(payload, dict):
        session.update({k: payload.get(k, session[k]) for k in session})
    if session["state"] not in PLAYBACK_STATES:
        session["state"] = "idle"
    session["claimed_playing"] = bool(
        session.get("claimed_playing")
        and session.get("host_online")
        and session.get("state") == "playing"
    )
    return session


def persist_session(session: dict[str, Any]) -> None:
    uid = session["uid"]
    path = get_paths().listening_session(uid)
    cache = list(session.get("command_cache") or [])[-COMMAND_RESULT_CACHE_CAP:]
    session["command_cache"] = cache
    session["updated_at"] = time.time()
    safe_write_json(path, session, keep_bak=False)


def _save_session(session: dict[str, Any]) -> None:
    persist_session(session)


def bind_host(
    uid: str,
    *,
    host_id: str,
    adapter: str = "first_party_desktop",
    device: str = "desktop",
    generation: int | None = None,
) -> dict[str, Any]:
    uid = _owner_uid(uid)
    with listening_lock(uid):
        session = load_session(uid)
        nxt = int(generation) if generation is not None else int(session["generation"] or 0) + 1
        if session["host_id"] and session["host_id"] != host_id and session["generation"] >= nxt:
            session["host_online"] = False
            return session
        if session["occurrence_id"] and session["state"] in {"playing", "paused", "loading"}:
            _terminate_open(uid, session, reason="host_expired")
        session["host_id"] = str(host_id)
        session["adapter"] = str(adapter)
        session["device"] = str(device)
        session["generation"] = nxt
        session["session_id"] = session["session_id"] or uuid.uuid4().hex
        session["host_online"] = True
        session["claimed_playing"] = False
        if session["state"] in {"playing", "paused", "loading"}:
            session["state"] = "stopped"
            session["occurrence_id"] = None
        _save_session(session)
        return session


def reconcile_after_restart(uid: str) -> dict[str, Any]:
    """Crash/restart: never claim still playing; do not accrue wall-clock."""
    uid = _owner_uid(uid)
    with listening_lock(uid):
        session = load_session(uid)
        if session["occurrence_id"] and session["state"] in {"playing", "paused", "loading"}:
            _terminate_open(uid, session, reason="disconnected")
        session["host_online"] = False
        session["claimed_playing"] = False
        if session["state"] in {"playing", "paused", "loading"}:
            session["state"] = "stopped"
            session["occurrence_id"] = None
        _save_session(session)
        rebuild_stats(uid)
        return session


def upsert_track(
    uid: str,
    *,
    provider: str,
    source_id: str,
    title: str,
    author: str = "",
    duration_s: float | None = None,
    audio_ref: str | None = None,
    audio_access: str = "unavailable",
    analysis_status: str = "unavailable",
) -> dict[str, Any]:
    uid = _owner_uid(uid)
    provider = str(provider or "").strip() or "local"
    source_id = str(source_id or "").strip()
    if not source_id:
        raise ValueError("source_id required")
    title = str(title or "").strip() or "untitled"
    if audio_access not in AUDIO_ACCESS:
        audio_access = "unavailable"
    if analysis_status not in ANALYSIS_STATUSES:
        analysis_status = "unavailable"
    with listening_lock(uid):
        with _library(uid, write=True) as db:
            row = db.execute(
                "SELECT * FROM tracks WHERE provider=? AND source_id=?",
                (provider, source_id),
            ).fetchone()
            if row is None:
                count = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
                if count >= LIBRARY_TRACK_CAP:
                    raise ValueError("library_cap")
                track_id = hashlib.sha256(f"{provider}:{source_id}".encode()).hexdigest()[:24]
                db.execute(
                    "INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        track_id, provider, source_id, title, author, duration_s,
                        audio_ref, audio_access, analysis_status, ANALYSIS_VERSION,
                        time.time(),
                    ),
                )
            else:
                track_id = row["track_id"]
                db.execute(
                    """UPDATE tracks SET title=?, author=?, duration_s=?, audio_ref=?,
                       audio_access=?, analysis_status=?, analysis_version=?
                       WHERE track_id=?""",
                    (
                        title, author, duration_s, audio_ref, audio_access,
                        analysis_status, ANALYSIS_VERSION, track_id,
                    ),
                )
            stored = db.execute("SELECT * FROM tracks WHERE track_id=?", (track_id,)).fetchone()
            return dict(stored)


def get_track(uid: str, track_id: str) -> dict[str, Any] | None:
    uid = _owner_uid(uid)
    with _library(uid, write=False) as db:
        row = db.execute("SELECT * FROM tracks WHERE track_id=?", (track_id,)).fetchone()
        return dict(row) if row else None


def list_tracks(uid: str, *, limit: int = 200) -> list[dict[str, Any]]:
    uid = _owner_uid(uid)
    limit = max(1, min(int(limit), LIBRARY_TRACK_CAP))
    with _library(uid, write=False) as db:
        rows = db.execute(
            "SELECT * FROM tracks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


_BLOB_SUFFIXES = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm", ".opus", ".aac"}


def register_audio_blob(uid: str, data: bytes, *, filename: str = "track.wav") -> str:
    """Store a controlled blob. Arbitrary host paths/URLs are not accepted."""
    uid = _owner_uid(uid)
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("empty_blob")
    if len(data) > AUDIO_BLOB_BUDGET_BYTES:
        raise ValueError("blob_oversize")
    digest = hashlib.sha256(bytes(data)).hexdigest()
    suffix = str(filename or "").rsplit(".", 1)
    ext = f".{suffix[-1].lower()}" if len(suffix) == 2 else ""
    if ext not in _BLOB_SUFFIXES:
        ext = ".wav" if bytes(data[:4]) == b"RIFF" and b"WAVE" in bytes(data[:12]) else ".bin"
    folder = get_paths().music_audio_blob_dir(uid)
    folder.mkdir(parents=True, exist_ok=True)
    used = sum(path.stat().st_size for path in folder.glob("*") if path.is_file())
    target = folder / f"{digest}{ext}"
    if not target.exists() and used + len(data) > AUDIO_BLOB_BUDGET_BYTES:
        raise ValueError("blob_budget")
    if not target.exists():
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(bytes(data))
        tmp.replace(target)
    return f"blob:{digest}{ext}"


def resolve_audio_blob(uid: str, audio_ref: str):
    """Resolve a blob: digest reference inside the sandbox. No host paths."""
    uid = _owner_uid(uid)
    ref = str(audio_ref or "")
    if not ref.startswith("blob:"):
        return None
    name = ref.split(":", 1)[1]
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    path = get_paths().music_audio_blob_dir(uid) / name
    if path.is_file() and path.resolve().is_relative_to(
        get_paths().music_audio_blob_dir(uid).resolve()
    ):
        return path
    return None


def set_participant(uid: str, char_id: str) -> dict[str, Any]:
    uid = _owner_uid(uid)
    with listening_lock(uid):
        session = load_session(uid)
        session["participant_char_id"] = str(char_id or "")
        _save_session(session)
        return session


def set_queue(uid: str, queue: list[str], *, expected_revision: int) -> dict[str, Any]:
    uid = _owner_uid(uid)
    with listening_lock(uid):
        session = load_session(uid)
        if int(session["revision"]) != int(expected_revision):
            return {"ok": False, "reason": "revision_conflict", "session": session}
        cleaned = [str(item) for item in queue if str(item)]
        session["queue"] = cleaned
        session["revision"] = int(session["revision"]) + 1
        _save_session(session)
        return {"ok": True, "reason": "", "session": session}


def _open_occurrence(uid: str, session: dict[str, Any], track_id: str) -> str:
    occurrence_id = uuid.uuid4().hex
    char_id = session.get("participant_char_id") or ""
    with _history(uid, write=True) as db:
        db.execute(
            """INSERT INTO occurrences(occurrence_id, track_id, char_id, session_id,
               generation, started_at, accumulated_play_s)
               VALUES (?,?,?,?,?,?,0)""",
            (
                occurrence_id, track_id, char_id, session["session_id"],
                int(session["generation"]), time.time(),
            ),
        )
        db.execute(
            "DELETE FROM occurrences WHERE occurrence_id NOT IN "
            "(SELECT occurrence_id FROM ("
            "SELECT occurrence_id FROM occurrences ORDER BY started_at DESC LIMIT ?))",
            (HISTORY_OCCURRENCE_CAP,),
        )
    session["occurrence_id"] = occurrence_id
    session["track_id"] = track_id
    return occurrence_id


def _terminate_open(uid: str, session: dict[str, Any], *, reason: str) -> None:
    occurrence_id = session.get("occurrence_id")
    if not occurrence_id:
        return
    if reason not in TERMINATION_REASONS:
        reason = "stopped"
    with _history(uid, write=True) as db:
        row = db.execute(
            "SELECT * FROM occurrences WHERE occurrence_id=?", (occurrence_id,),
        ).fetchone()
        if row and row["ended_at"] is None:
            finished = 1 if reason == "finished" else 0
            db.execute(
                """UPDATE occurrences SET ended_at=?, termination=?, natural_finished=?
                   WHERE occurrence_id=?""",
                (time.time(), reason, finished, occurrence_id),
            )
    session["occurrence_id"] = None
    rebuild_stats(uid)


def _accumulate(uid: str, occurrence_id: str, delta_s: float) -> None:
    if delta_s <= 0:
        return
    with _history(uid, write=True) as db:
        row = db.execute(
            "SELECT * FROM occurrences WHERE occurrence_id=?", (occurrence_id,),
        ).fetchone()
        if not row or row["ended_at"] is not None:
            return
        accumulated = float(row["accumulated_play_s"]) + float(delta_s)
        track = get_track(uid, row["track_id"])
        duration = None if not track else track.get("duration_s")
        counted = bool(row["listen_counted"])
        last_listened = row["last_listened_at"]
        if should_count_listen(
            accumulated_play_s=accumulated, duration_s=duration, already_counted=counted,
        ):
            counted = True
            last_listened = time.time()
        db.execute(
            """UPDATE occurrences SET accumulated_play_s=?, listen_counted=?, last_listened_at=?
               WHERE occurrence_id=?""",
            (accumulated, int(counted), last_listened, occurrence_id),
        )
    rebuild_stats(uid)


def rebuild_stats(uid: str) -> dict[str, Any]:
    uid = _owner_uid(uid)
    with _history(uid, write=False) as history, _stats_db(uid, write=True) as stats:
        stats.execute("DELETE FROM stats")
        rows = history.execute("SELECT * FROM occurrences").fetchall()
        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = buckets.setdefault(
                row["track_id"],
                {"started_count": 0, "listen_count": 0, "completed_count": 0, "last_listened_at": None},
            )
            item["started_count"] += 1
            if row["listen_counted"]:
                item["listen_count"] += 1
                stamp = row["last_listened_at"]
                if stamp and (item["last_listened_at"] is None or stamp > item["last_listened_at"]):
                    item["last_listened_at"] = stamp
            if row["natural_finished"]:
                item["completed_count"] += 1
        for track_id, item in buckets.items():
            stats.execute(
                "INSERT INTO stats VALUES (?,?,?,?,?)",
                (
                    track_id, item["started_count"], item["listen_count"],
                    item["completed_count"], item["last_listened_at"],
                ),
            )
        return {track_id: dict(item) for track_id, item in buckets.items()}


def get_stats(uid: str, track_id: str | None = None) -> dict[str, Any]:
    uid = _owner_uid(uid)
    with _stats_db(uid, write=False) as db:
        if track_id:
            row = db.execute("SELECT * FROM stats WHERE track_id=?", (track_id,)).fetchone()
            if not row:
                return {field: 0 for field in COUNT_FIELDS} | {"track_id": track_id, "last_listened_at": None}
            return dict(row)
        rows = db.execute("SELECT * FROM stats").fetchall()
        return {row["track_id"]: dict(row) for row in rows}


def _seen_event(uid: str, event_id: str) -> bool:
    with _history(uid, write=False) as db:
        row = db.execute("SELECT event_id FROM events WHERE event_id=?", (event_id,)).fetchone()
        return row is not None


def _record_event(uid: str, event: dict[str, Any], *, accepted: bool) -> None:
    with _history(uid, write=True) as db:
        db.execute(
            "INSERT OR IGNORE INTO events(event_id, sequence, kind, occurrence_id, track_id, accepted, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                event["event_id"], int(event.get("sequence") or 0), event.get("kind"),
                event.get("occurrence_id"), event.get("track_id"), int(accepted), time.time(),
            ),
        )
        db.execute(
            "DELETE FROM events WHERE event_id NOT IN "
            "(SELECT event_id FROM ("
            "SELECT event_id FROM events ORDER BY ts DESC LIMIT ?))",
            (HOST_EVENT_RING_CAP,),
        )


def apply_event(uid: str, event: dict[str, Any]) -> dict[str, Any]:
    """Idempotent host event commit. Identity comes from the bound session."""
    uid = _owner_uid(uid)
    if not isinstance(event, dict):
        return {"ok": False, "reason": "invalid_event"}
    event_id = str(event.get("event_id") or "")
    kind = str(event.get("kind") or "")
    if not event_id or (kind not in ADAPTER_EVENTS and kind != "snapshot"):
        return {"ok": False, "reason": "invalid_event"}
    with listening_lock(uid):
        session = load_session(uid)
        if _seen_event(uid, event_id):
            return {"ok": True, "reason": "duplicate", "session": session}
        if not session.get("host_online"):
            _record_event(uid, event, accepted=False)
            return {"ok": False, "reason": "host_offline", "session": session}
        if int(event.get("generation") or 0) != int(session["generation"]):
            _record_event(uid, event, accepted=False)
            return {"ok": False, "reason": "stale_host", "session": session}
        if event.get("session_id") and event.get("session_id") != session["session_id"]:
            _record_event(uid, event, accepted=False)
            return {"ok": False, "reason": "stale_session", "session": session}
        sequence = int(event.get("sequence") or 0)
        if sequence and sequence <= int(session["last_sequence"] or 0) and kind != "snapshot":
            _record_event(uid, event, accepted=False)
            return {"ok": False, "reason": "out_of_order", "session": session}

        result = _apply_kind(uid, session, event, kind)
        if result.get("ok"):
            if sequence:
                session["last_sequence"] = sequence
            session["claimed_playing"] = session["state"] == "playing"
            _save_session(session)
        _record_event(
            uid,
            {**event, "occurrence_id": session.get("occurrence_id"), "track_id": session.get("track_id")},
            accepted=bool(result.get("ok")),
        )
        result["session"] = session
        return result


def _move_state(session: dict[str, Any], nxt: str) -> bool:
    previous = session["state"]
    if not is_legal_transition(previous, nxt):
        return False
    session["state"] = nxt
    return True


def _enter_playing(session: dict[str, Any]) -> bool:
    if session["state"] == "playing":
        return True
    if session["state"] == "idle" and not _move_state(session, "loading"):
        return False
    if session["state"] == "paused":
        return _move_state(session, "playing")
    if session["state"] != "loading" and not _move_state(session, "loading"):
        return False
    return _move_state(session, "playing")


def _apply_kind(uid: str, session: dict[str, Any], event: dict[str, Any], kind: str) -> dict[str, Any]:
    track_id = event.get("track_id") or session.get("track_id")
    if kind == "snapshot":
        reported = event.get("state")
        if reported in PLAYBACK_STATES:
            if reported == "playing" and session["state"] == "idle":
                return {"ok": False, "reason": "illegal_transition"}
            if reported == session["state"] or is_legal_transition(session["state"], reported):
                session["state"] = reported
            if reported != "playing":
                session["claimed_playing"] = False
        if event.get("position_s") is not None:
            session["position_s"] = float(event["position_s"])
        return {"ok": True, "reason": "snapshot"}

    if kind in {"started", "changed"}:
        if not track_id:
            return {"ok": False, "reason": "missing_track"}
        same_play = (
            session.get("track_id") == track_id
            and session["state"] == "playing"
            and session.get("occurrence_id")
        )
        if same_play:
            return {"ok": True, "reason": "merged"}
        if session.get("occurrence_id"):
            _terminate_open(uid, session, reason="changed")
        if not _enter_playing(session):
            return {"ok": False, "reason": "illegal_transition"}
        _open_occurrence(uid, session, str(track_id))
        session["position_s"] = float(event.get("position_s") or 0.0)
        rebuild_stats(uid)
        return {"ok": True, "reason": "started"}

    if kind == "progress":
        if session["state"] != "playing" or not session.get("occurrence_id"):
            if event.get("position_s") is not None:
                session["position_s"] = float(event["position_s"])
            return {"ok": True, "reason": "progress_ignored"}
        delta = event.get("played_delta_s")
        if delta is None:
            return {"ok": False, "reason": "missing_played_delta"}
        _accumulate(uid, session["occurrence_id"], float(delta))
        if event.get("position_s") is not None:
            session["position_s"] = float(event["position_s"])
        return {"ok": True, "reason": "progress"}

    if kind == "paused":
        if not _move_state(session, "paused"):
            return {"ok": False, "reason": "illegal_transition"}
        return {"ok": True, "reason": "paused"}

    if kind == "resumed":
        if session["state"] != "paused":
            return {"ok": False, "reason": "illegal_transition"}
        if not _move_state(session, "playing"):
            return {"ok": False, "reason": "illegal_transition"}
        return {"ok": True, "reason": "resumed"}

    if kind == "finished":
        if not _move_state(session, "ended"):
            return {"ok": False, "reason": "illegal_transition"}
        _terminate_open(uid, session, reason="finished")
        return {"ok": True, "reason": "finished"}

    if kind == "stopped":
        if session["state"] != "stopped" and not _move_state(session, "stopped"):
            return {"ok": False, "reason": "illegal_transition"}
        _terminate_open(uid, session, reason="stopped")
        return {"ok": True, "reason": "stopped"}

    if kind == "error":
        if session["state"] != "error" and not _move_state(session, "error"):
            return {"ok": False, "reason": "illegal_transition"}
        _terminate_open(uid, session, reason="error")
        return {"ok": True, "reason": "error"}

    return {"ok": False, "reason": "unsupported_kind"}


def list_history(uid: str, *, char_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    uid = _owner_uid(uid)
    limit = max(1, min(int(limit), 200))
    with _history(uid, write=False) as db:
        if char_id:
            rows = db.execute(
                "SELECT * FROM occurrences WHERE char_id=? ORDER BY started_at DESC LIMIT ?",
                (char_id, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM occurrences ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def read_note(uid: str, char_id: str, track_id: str) -> dict[str, Any] | None:
    uid = _owner_uid(uid)
    with notes_lock(uid, char_id):
        with _notes_db(uid, char_id, write=False) as db:
            row = db.execute("SELECT * FROM notes WHERE track_id=?", (track_id,)).fetchone()
            return dict(row) if row else None


def write_note(
    uid: str,
    char_id: str,
    track_id: str,
    body: str,
    *,
    expected_revision: int | None = None,
    occurrence_id: str | None = None,
) -> dict[str, Any]:
    uid = _owner_uid(uid)
    text = str(body or "")
    if len(text) > NOTE_MAX_CHARS:
        raise ValueError("note_too_long")
    with notes_lock(uid, char_id):
        with _notes_db(uid, char_id, write=True) as db:
            row = db.execute("SELECT * FROM notes WHERE track_id=?", (track_id,)).fetchone()
            current = int(row["revision"]) if row else 0
            if expected_revision is not None and int(expected_revision) != current:
                return {"ok": False, "reason": "revision_conflict", "note": dict(row) if row else None}
            nxt = current + 1
            db.execute(
                """INSERT INTO notes(track_id, body, revision, occurrence_id, updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(track_id) DO UPDATE SET
                   body=excluded.body, revision=excluded.revision,
                   occurrence_id=excluded.occurrence_id, updated_at=excluded.updated_at""",
                (track_id, text, nxt, occurrence_id, time.time()),
            )
            stored = db.execute("SELECT * FROM notes WHERE track_id=?", (track_id,)).fetchone()
            return {"ok": True, "reason": "", "note": dict(stored)}


def list_notes(uid: str, char_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    uid = _owner_uid(uid)
    with notes_lock(uid, char_id):
        with _notes_db(uid, char_id, write=False) as db:
            rows = db.execute(
                "SELECT * FROM notes ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
            return [dict(row) for row in rows]


def music_analysis_enabled() -> bool:
    from core.config_loader import get_config
    block = get_config().get(CONFIG_ROOT) or {}
    return block.get("music_analysis") is True


def refresh_track_analysis(uid: str, track_id: str) -> dict[str, Any]:
    """Analyze a backend-readable blob. Missing audio stays unavailable."""
    uid = _owner_uid(uid)
    track = get_track(uid, track_id)
    if not track:
        raise ValueError("unknown_track")
    if not music_analysis_enabled() or track.get("audio_access") != "backend_readable":
        upsert_track(
            uid, provider=track["provider"], source_id=track["source_id"],
            title=track["title"], author=track.get("author") or "",
            duration_s=track.get("duration_s"), audio_ref=track.get("audio_ref"),
            audio_access=track.get("audio_access") or "unavailable",
            analysis_status="unavailable",
        )
        return get_track(uid, track_id) or track
    ref = str(track.get("audio_ref") or "")
    path = resolve_audio_blob(uid, ref)
    if path is None:
        status = "unavailable"
    else:
        from core.audio_analysis import analyze_audio_bytes
        result = analyze_audio_bytes(path.read_bytes(), mode="music", use_cache=True)
        status = result.get("analysis_status") or "failed"
    upsert_track(
        uid, provider=track["provider"], source_id=track["source_id"],
        title=track["title"], author=track.get("author") or "",
        duration_s=track.get("duration_s"), audio_ref=track.get("audio_ref"),
        audio_access=track.get("audio_access") or "unavailable",
        analysis_status=status if status in ANALYSIS_STATUSES else "failed",
    )
    return get_track(uid, track_id) or track


def metadata_snapshot(uid: str) -> dict[str, Any]:
    """state.read projection: no notes, no history bodies, no audio bytes."""
    uid = _owner_uid(uid)
    session = load_session(uid)
    with _library(uid, write=False) as lib:
        tracks = lib.execute("SELECT analysis_status FROM tracks").fetchall()
    analysis = {status: 0 for status in ANALYSIS_STATUSES}
    for row in tracks:
        analysis[row["analysis_status"]] = analysis.get(row["analysis_status"], 0) + 1
    with _history(uid, write=False) as history:
        occurrence_count = history.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0]
        last_events = [
            {"kind": row["kind"], "accepted": bool(row["accepted"]), "sequence": row["sequence"]}
            for row in history.execute(
                "SELECT kind, accepted, sequence FROM events ORDER BY ts DESC LIMIT 8"
            ).fetchall()
        ]
    return {
        "uid_present": True,
        "listen_threshold_version": LISTEN_THRESHOLD_VERSION,
        "count_fields": COUNT_FIELDS,
        "listen_threshold_rule": "min(30s, 50% duration); unknown duration = 30s",
        "analysis_version": ANALYSIS_VERSION,
        "library_track_count": len(tracks),
        "analysis_status_counts": analysis,
        "occurrence_count": occurrence_count,
        "session": {
            "state": session["state"],
            "revision": session["revision"],
            "generation": session["generation"],
            "host_online": session["host_online"],
            "claimed_playing": False if not session["host_online"] else session["state"] == "playing",
            "queue_len": len(session.get("queue") or []),
            "has_current_track": bool(session.get("track_id")),
            "open_occurrence": bool(session.get("occurrence_id")),
            "participant_bound": bool(session.get("participant_char_id")),
        },
        "last_events": last_events,
    }


def history_snapshot(uid: str, *, char_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {
        "threshold_version": LISTEN_THRESHOLD_VERSION,
        "stats": get_stats(uid),
        "occurrences": list_history(uid, char_id=char_id, limit=limit),
    }
