"""Player Adapter v0 (ticket 260 E).

The backend owns expected queue, command ledger and listen counts. Hosts report
actual playback. Fake adapters exist only for regression; they cannot close E.
The first real host is the first-party admin HTMLAudioElement player.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Protocol

from core.audio_music_contract import (
    AUDIO_ACCESS,
    COMMAND_OUTCOMES,
    COMMAND_RESULT_CACHE_CAP,
    CONFIG_ROOT,
    OPTIONAL_ADAPTER_ACTIONS,
    PLAYER_ADAPTER_VERSION,
    REQUIRED_ADAPTER_ACTIONS,
)
from core.listening_store import (
    apply_event,
    bind_host,
    get_track,
    listening_lock,
    load_session,
    persist_session,
    reconcile_after_restart,
)

FIRST_PARTY_ADAPTER = "first_party_admin"
FIRST_PARTY_DEVICE = "admin_html_audio"
DISCONNECT_POLICY = "local_may_continue_unsynced"


def music_control_enabled() -> bool:
    from core.config_loader import get_config
    block = get_config().get(CONFIG_ROOT) or {}
    return block.get("music_control") is True


def _owner(uid: str) -> str:
    value = str(uid or "").strip()
    if not value:
        raise ValueError("uid required")
    return value


def capabilities(*, audio_access: str = "backend_readable") -> dict[str, Any]:
    if audio_access not in AUDIO_ACCESS:
        audio_access = "unavailable"
    return {
        "adapter_version": PLAYER_ADAPTER_VERSION,
        "adapter": FIRST_PARTY_ADAPTER,
        "actions": sorted(REQUIRED_ADAPTER_ACTIONS | OPTIONAL_ADAPTER_ACTIONS),
        "seek": True,
        "audio_access": audio_access,
        "netease_or_media_key": False,
        "tts_queue_is_music_host": False,
        "disconnect_policy": DISCONNECT_POLICY,
        "mock_closes_e": False,
    }


def get_state(uid: str) -> dict[str, Any]:
    session = load_session(uid)
    return {
        "ok": True,
        "adapter_version": PLAYER_ADAPTER_VERSION,
        "session": session,
        "capabilities": capabilities(),
        "claimed_playing": bool(session.get("claimed_playing")),
        "host_online": bool(session.get("host_online")),
    }


def register_host(
    uid: str,
    *,
    host_id: str,
    adapter: str = FIRST_PARTY_ADAPTER,
    device: str = FIRST_PARTY_DEVICE,
) -> dict[str, Any]:
    uid = _owner(uid)
    session = bind_host(uid, host_id=host_id, adapter=adapter, device=device)
    return {"ok": True, "reason": "bound", "session": session, "capabilities": capabilities()}


def mark_host_offline(uid: str) -> dict[str, Any]:
    """Disconnect: do not accrue wall-clock; local audio may continue unsynced."""
    uid = _owner(uid)
    return reconcile_after_restart(uid)


def _cache_lookup(session: dict[str, Any], command_id: str) -> dict[str, Any] | None:
    for item in session.get("command_cache") or []:
        if isinstance(item, dict) and item.get("command_id") == command_id:
            return item
    return None


def _cache_store(session: dict[str, Any], record: dict[str, Any]) -> None:
    cache = [item for item in (session.get("command_cache") or []) if isinstance(item, dict)]
    cache = [item for item in cache if item.get("command_id") != record["command_id"]]
    cache.append(record)
    session["command_cache"] = cache[-COMMAND_RESULT_CACHE_CAP:]


def _reject(session: dict[str, Any], *, command_id: str, action: str, reason: str) -> dict[str, Any]:
    record = {
        "command_id": command_id,
        "action": action,
        "accepted": False,
        "outcome": "rejected",
        "reason": reason,
        "session": session,
    }
    _cache_store(session, {k: record[k] for k in ("command_id", "action", "accepted", "outcome", "reason")})
    persist_session(session)
    return record


def dispatch_command(uid: str, command: dict[str, Any]) -> dict[str, Any]:
    """Accept or reject a host command. Actual sound still needs a host event."""
    uid = _owner(uid)
    if not isinstance(command, dict):
        return {"ok": False, "accepted": False, "outcome": "rejected", "reason": "invalid_command"}
    command_id = str(command.get("command_id") or "").strip()
    action = str(command.get("action") or "").strip()
    if not command_id or not action:
        return {"ok": False, "accepted": False, "outcome": "rejected", "reason": "invalid_command"}
    if action not in REQUIRED_ADAPTER_ACTIONS and action not in OPTIONAL_ADAPTER_ACTIONS:
        return {"ok": False, "accepted": False, "outcome": "rejected", "reason": "unsupported"}
    if action == "seek" and action not in OPTIONAL_ADAPTER_ACTIONS:
        return {"ok": False, "accepted": False, "outcome": "rejected", "reason": "unsupported"}
    if not music_control_enabled() and action not in {"capabilities", "get_state"}:
        return {
            "ok": False,
            "accepted": False,
            "outcome": "rejected",
            "reason": "music_control_disabled",
            "command_id": command_id,
            "action": action,
        }

    with listening_lock(uid):
        session = load_session(uid)
        cached = _cache_lookup(session, command_id)
        if cached:
            return {
                "ok": cached.get("outcome") in {"accepted", "confirmed"},
                "accepted": bool(cached.get("accepted")),
                "outcome": cached.get("outcome") or "outcome_unknown",
                "reason": "duplicate",
                "command_id": command_id,
                "action": action,
                "session": session,
            }
        if int(command.get("generation") or 0) != int(session.get("generation") or 0):
            return _reject(session, command_id=command_id, action=action, reason="stale_host")
        if command.get("session_id") and command.get("session_id") != session.get("session_id"):
            return _reject(session, command_id=command_id, action=action, reason="stale_session")
        if action in {"set_queue", "next", "play"} and command.get("expected_revision") is not None:
            if int(command["expected_revision"]) != int(session.get("revision") or 0):
                return _reject(session, command_id=command_id, action=action, reason="revision_conflict")

        args = command.get("args") if isinstance(command.get("args"), dict) else {}
        outcome = "accepted"
        reason = ""
        if action == "capabilities":
            outcome = "confirmed"
        elif action == "get_state":
            outcome = "confirmed"
        elif action == "set_queue":
            expected = command.get("expected_revision", session.get("revision"))
            if int(session.get("revision") or 0) != int(expected):
                return _reject(session, command_id=command_id, action=action, reason="revision_conflict")
            session["queue"] = [str(item) for item in (args.get("queue") or []) if str(item)]
            session["revision"] = int(session.get("revision") or 0) + 1
            outcome = "confirmed"
        elif action == "play":
            track_id = str(args.get("track_id") or "")
            if not track_id or get_track(uid, track_id) is None:
                return _reject(session, command_id=command_id, action=action, reason="unknown_track")
            session["track_id"] = track_id
            if track_id not in (session.get("queue") or []):
                session["queue"] = list(session.get("queue") or []) + [track_id]
                session["revision"] = int(session.get("revision") or 0) + 1
            outcome = "accepted"
            reason = "awaiting_host"
        elif action == "next":
            queue = list(session.get("queue") or [])
            current = session.get("track_id")
            if current in queue:
                index = queue.index(current)
                nxt = queue[index + 1] if index + 1 < len(queue) else None
            else:
                nxt = queue[0] if queue else None
            if not nxt:
                return _reject(session, command_id=command_id, action=action, reason="no_next")
            session["track_id"] = nxt
            session["revision"] = int(session.get("revision") or 0) + 1
            outcome = "accepted"
            reason = "awaiting_host"
        elif action in {"pause", "resume", "stop", "seek"}:
            outcome = "accepted"
            reason = "awaiting_host"
        else:
            return _reject(session, command_id=command_id, action=action, reason="unsupported")

        record = {
            "command_id": command_id,
            "action": action,
            "accepted": True,
            "outcome": outcome,
            "reason": reason,
        }
        _cache_store(session, record)
        persist_session(session)
        return {
            "ok": True,
            "accepted": True,
            "outcome": outcome,
            "reason": reason,
            "command_id": command_id,
            "action": action,
            "session": session,
        }


def ingest_host_event(uid: str, event: dict[str, Any]) -> dict[str, Any]:
    """Commit a real host event into the listening ledger."""
    uid = _owner(uid)
    if not isinstance(event, dict):
        return {"ok": False, "reason": "invalid_event"}
    if not music_control_enabled():
        return {"ok": False, "reason": "music_control_disabled"}
    payload = dict(event)
    payload.setdefault("event_id", uuid.uuid4().hex)
    result = apply_event(uid, payload)
    causation = str(payload.get("causation_command_id") or "")
    if causation and result.get("ok"):
        with listening_lock(uid):
            session = load_session(uid)
            cached = _cache_lookup(session, causation)
            if cached and cached.get("outcome") in {"accepted", "outcome_unknown"}:
                cached["outcome"] = "confirmed"
                _cache_store(session, cached)
                persist_session(session)
                result["session"] = session
    elif causation and not result.get("ok") and result.get("reason") not in {"duplicate", "out_of_order"}:
        with listening_lock(uid):
            session = load_session(uid)
            cached = _cache_lookup(session, causation)
            if cached and cached.get("outcome") == "accepted":
                cached["outcome"] = "failed"
                cached["reason"] = result.get("reason") or "failed"
                _cache_store(session, cached)
                persist_session(session)
                result["session"] = session
    return result


async def commit_host_event(uid: str, event: dict[str, Any]) -> dict[str, Any]:
    """Commit the ledger first, then optionally enqueue a music_playback candidate."""
    result = ingest_host_event(uid, event)
    from core.music_playback_stimulus import maybe_emit_after_ledger
    result["stimulus"] = await maybe_emit_after_ledger(uid, event, result)
    return result


def mark_outcome_unknown(uid: str, command_id: str) -> dict[str, Any]:
    uid = _owner(uid)
    with listening_lock(uid):
        session = load_session(uid)
        cached = _cache_lookup(session, command_id)
        if not cached:
            return {"ok": False, "reason": "unknown_command"}
        if cached.get("outcome") == "accepted":
            cached["outcome"] = "outcome_unknown"
            _cache_store(session, cached)
            persist_session(session)
        return {"ok": True, "record": cached, "session": session}


class PlayerHost(Protocol):
    def capabilities(self) -> dict[str, Any]: ...
    def get_state(self) -> dict[str, Any]: ...
    def play(self, track_id: str) -> dict[str, Any]: ...
    def pause(self) -> dict[str, Any]: ...
    def resume(self) -> dict[str, Any]: ...
    def stop(self) -> dict[str, Any]: ...
    def set_queue(self, queue: list[str], *, expected_revision: int) -> dict[str, Any]: ...
    def next(self, *, expected_revision: int) -> dict[str, Any]: ...


class FakePlayerHost:
    """In-process fake. Allowed in tests; must not be treated as E completion."""

    def __init__(self, uid: str, *, host_id: str = "fake-host"):
        self.uid = uid
        self.host_id = host_id
        self.sequence = 0
        self.position_s = 0.0
        register_host(uid, host_id=host_id, adapter="fake", device="test")

    def capabilities(self) -> dict[str, Any]:
        caps = capabilities(audio_access="unavailable")
        caps["adapter"] = "fake"
        caps["mock_closes_e"] = False
        return caps

    def get_state(self) -> dict[str, Any]:
        return get_state(self.uid)

    def _emit(self, kind: str, **extra: Any) -> dict[str, Any]:
        self.sequence += 1
        session = load_session(self.uid)
        event = {
            "event_id": f"{kind}-{self.sequence}-{uuid.uuid4().hex[:8]}",
            "kind": kind,
            "generation": session["generation"],
            "session_id": session["session_id"],
            "sequence": self.sequence,
            "track_id": extra.pop("track_id", session.get("track_id")),
            "position_s": extra.pop("position_s", self.position_s),
            "occurred_at": time.time(),
        }
        event.update(extra)
        return ingest_host_event(self.uid, event)

    async def commit(self, kind: str, **extra: Any) -> dict[str, Any]:
        self.sequence += 1
        session = load_session(self.uid)
        event = {
            "event_id": f"{kind}-{self.sequence}-{uuid.uuid4().hex[:8]}",
            "kind": kind,
            "generation": session["generation"],
            "session_id": session["session_id"],
            "sequence": self.sequence,
            "track_id": extra.pop("track_id", session.get("track_id")),
            "position_s": extra.pop("position_s", self.position_s),
            "occurred_at": time.time(),
        }
        event.update(extra)
        return await commit_host_event(self.uid, event)

    def play(self, track_id: str) -> dict[str, Any]:
        command = dispatch_command(self.uid, {
            "command_id": uuid.uuid4().hex,
            "action": "play",
            "generation": load_session(self.uid)["generation"],
            "session_id": load_session(self.uid)["session_id"],
            "args": {"track_id": track_id},
        })
        if not command.get("accepted"):
            return command
        started = self._emit("started", track_id=track_id, causation_command_id=command["command_id"])
        command["event"] = started
        return command

    def pause(self) -> dict[str, Any]:
        return self._emit("paused")

    def resume(self) -> dict[str, Any]:
        return self._emit("resumed")

    def stop(self) -> dict[str, Any]:
        return self._emit("stopped")

    def set_queue(self, queue: list[str], *, expected_revision: int) -> dict[str, Any]:
        session = load_session(self.uid)
        return dispatch_command(self.uid, {
            "command_id": uuid.uuid4().hex,
            "action": "set_queue",
            "generation": session["generation"],
            "session_id": session["session_id"],
            "expected_revision": expected_revision,
            "args": {"queue": queue},
        })

    def next(self, *, expected_revision: int) -> dict[str, Any]:
        session = load_session(self.uid)
        command = dispatch_command(self.uid, {
            "command_id": uuid.uuid4().hex,
            "action": "next",
            "generation": session["generation"],
            "session_id": session["session_id"],
            "expected_revision": expected_revision,
        })
        if not command.get("accepted"):
            return command
        nxt = command["session"].get("track_id")
        command["event"] = self._emit("changed", track_id=nxt, causation_command_id=command["command_id"])
        command["event"] = self._emit("started", track_id=nxt, causation_command_id=command["command_id"])
        return command

    def progress(self, played_delta_s: float, *, position_s: float | None = None) -> dict[str, Any]:
        if position_s is not None:
            self.position_s = position_s
        return self._emit("progress", played_delta_s=played_delta_s, position_s=self.position_s)

    def finish(self) -> dict[str, Any]:
        return self._emit("finished")
