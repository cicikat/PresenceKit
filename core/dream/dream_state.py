import json
import logging
import threading
import time
from enum import Enum
from typing import Any

from core.safe_write import safe_write_json
from core.sandbox import get_paths, safe_user_id

logger = logging.getLogger(__name__)
_STATE_WRITE_LOCK = threading.RLock()

_FORCED_ROUNDS = 3

# Cooldown window during which REALITY_AFTERGLOW is still projected as "cooldown"
# rather than "idle" for GET /dream/state (Brief 94 §2). Mirrors the afterglow
# content TTL (core/dream/dream_afterglow.py _AFTERGLOW_TTL_SECONDS / the 8h
# dream_afterglow_soft_hint window in docs/dream.md) — kept as an independent
# constant since it governs UI status semantics, not content injection.
DREAM_COOLDOWN_SECONDS: float = 8 * 3600

# Heuristic-only: how long a dreaming-bucket status may run before GET /dream/state
# flags it as `stuck` for the UI ("状态异常，可重启后端" hint). Dream sessions are
# normally minutes long; this never affects hard_exit or any dream invariant.
DREAM_STUCK_THRESHOLD_SECONDS: float = 2 * 3600

DREAM_ARTIFACT_SENTINEL = {
    "never_retrieve": True,
    "not_memory_source": True,
    "reality_boundary": "dream_only",
}


class DreamStatus(str, Enum):
    REALITY_CHAT = "REALITY_CHAT"
    DREAM_ENTRANCE_AVAILABLE = "DREAM_ENTRANCE_AVAILABLE"
    DREAM_ACTIVE = "DREAM_ACTIVE"
    DREAM_EXIT_REQUESTED = "DREAM_EXIT_REQUESTED"
    DREAM_LOCKED = "DREAM_LOCKED"
    DREAM_CLOSING = "DREAM_CLOSING"
    REALITY_AFTERGLOW = "REALITY_AFTERGLOW"


class DreamMode(str, Enum):
    """Dream session mode — frozen at enter_dream, immutable for session lifetime."""
    sandbox = "sandbox"
    scenario = "scenario"
    mirror = "mirror"
    rpg = "rpg"


VALID_DREAM_MODES: frozenset[str] = frozenset(m.value for m in DreamMode)


def default_state(user_id: str | int) -> dict[str, Any]:
    return {
        "user_id": safe_user_id(user_id),
        "status": DreamStatus.REALITY_CHAT.value,
        "state_version": 0,
    }


class DreamGuardStatus(str, Enum):
    ALLOW = "ALLOW"
    BLOCK_ACTIVE = "BLOCK_ACTIVE"
    BLOCK_UNCERTAIN = "BLOCK_UNCERTAIN"


def get_reality_guard_status(user_id: str | int) -> DreamGuardStatus:
    """
    Fail-closed dream guard for reality turns.

    ALLOW:           File missing (normal no-dream state) or dream is inactive,
                     AND no group dream (Dream Stage, Brief 100) owned by this
                     user is active either.
    BLOCK_ACTIVE:    Solo dream is DREAM_ACTIVE/DREAM_CLOSING, OR any group
                     dream owned by this user is ACTIVE/CLOSING — reject
                     reality turn.
    BLOCK_UNCERTAIN: File exists but unreadable / corrupt / invalid status —
                     caller must treat as fail-closed (reject reality turn).

    Only FileNotFoundError → ALLOW; any other I/O or parse error → BLOCK_UNCERTAIN.
    """
    path = get_paths().dream_state_path(user_id)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DreamGuardStatus.ALLOW if not _has_active_group_dream(user_id) else DreamGuardStatus.BLOCK_ACTIVE
    except Exception as exc:
        logger.error("[dream_guard] state unreadable uid=%s: %s", user_id, exc)
        return DreamGuardStatus.BLOCK_UNCERTAIN

    try:
        data = json.loads(text)
    except Exception as exc:
        logger.error("[dream_guard] state unparseable uid=%s: %s", user_id, exc)
        return DreamGuardStatus.BLOCK_UNCERTAIN

    if not isinstance(data, dict):
        logger.error("[dream_guard] state invalid shape uid=%s", user_id)
        return DreamGuardStatus.BLOCK_UNCERTAIN

    status = data.get("status")
    if status not in {item.value for item in DreamStatus}:
        logger.error("[dream_guard] unknown status uid=%s: %r", user_id, status)
        return DreamGuardStatus.BLOCK_UNCERTAIN

    if status in (DreamStatus.DREAM_ACTIVE.value, DreamStatus.DREAM_CLOSING.value):
        return DreamGuardStatus.BLOCK_ACTIVE

    if _has_active_group_dream(user_id):
        return DreamGuardStatus.BLOCK_ACTIVE

    return DreamGuardStatus.ALLOW


def _has_active_group_dream(user_id: str | int) -> bool:
    """Cross-domain check (Brief 100 §3): does this owner have an active Dream
    Stage (group dream) anywhere? Kept as a lazy import — core/stage/ depends
    on core/dream/ elsewhere (e.g. sentinel reuse), so importing it back here
    at module load time would create a cycle; deferring the import avoids that
    without weakening the check itself.
    """
    try:
        from core.stage.dream_state import has_active_group_dream_for_owner
        return has_active_group_dream_for_owner(user_id)
    except Exception as exc:
        logger.error("[dream_guard] group dream scan failed uid=%s: %s", user_id, exc)
        return True  # fail-closed: cannot rule out an active group dream


def apply_dream_artifact_sentinel(record: dict[str, Any]) -> dict[str, Any]:
    """Attach the required boundary fields for tmp/archive/summary dream artifacts."""
    if not isinstance(record, dict):
        raise TypeError("dream artifact record must be a dict")
    return {**record, **DREAM_ARTIFACT_SENTINEL}


def read_state(user_id: str | int) -> dict[str, Any]:
    path = get_paths().dream_state_path(user_id)
    if not path.exists():
        return default_state(user_id)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[dream_state] read failed uid={user_id}: {e}")
        return default_state(user_id)

    if not isinstance(data, dict):
        logger.warning(f"[dream_state] invalid state shape uid={user_id}")
        return default_state(user_id)

    status = data.get("status")
    if status not in {item.value for item in DreamStatus}:
        logger.warning(f"[dream_state] unknown status uid={user_id}: {status!r}")
        return default_state(user_id)

    data.setdefault("user_id", safe_user_id(user_id))
    try:
        data["state_version"] = max(0, int(data.get("state_version", 0)))
    except (TypeError, ValueError):
        data["state_version"] = 0
    return data


def read_state_checked(user_id: str | int) -> tuple[dict[str, Any], bool]:
    """Read a transition state without turning corruption into a valid idle state.

    ``read_state`` intentionally supplies a safe UI default for read-only callers.
    Exit/resume actions need to distinguish that fallback from a real idle state so
    they cannot report a successful close or mutate a later session after a corrupt
    state file is encountered.
    """
    path = get_paths().dream_state_path(user_id)
    if not path.exists():
        return default_state(user_id), True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("[dream_state] transition read failed uid=%s: %s", user_id, exc)
        return default_state(user_id), False
    if not isinstance(data, dict):
        logger.error("[dream_state] transition state shape invalid uid=%s", user_id)
        return default_state(user_id), False
    if data.get("status") not in {item.value for item in DreamStatus}:
        logger.error("[dream_state] transition status invalid uid=%s: %r", user_id, data.get("status"))
        return default_state(user_id), False
    data.setdefault("user_id", safe_user_id(user_id))
    try:
        data["state_version"] = max(0, int(data.get("state_version", 0)))
    except (TypeError, ValueError):
        data["state_version"] = 0
    return data, True


def write_state(user_id: str | int, state: dict[str, Any]) -> bool:
    if not isinstance(state, dict):
        raise TypeError("dream state must be a dict")

    status = state.get("status")
    if isinstance(status, DreamStatus):
        state = {**state, "status": status.value}
        status = state["status"]
    if status not in {item.value for item in DreamStatus}:
        raise ValueError(f"unknown dream status: {status!r}")

    with _STATE_WRITE_LOCK:
        path = get_paths().dream_state_path(user_id)
        current_version = 0
        try:
            if path.exists():
                current = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(current, dict):
                    current_version = max(0, int(current.get("state_version", 0)))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            current_version = 0
        try:
            requested_version = max(0, int(state.get("state_version", 0)))
        except (TypeError, ValueError):
            requested_version = 0
        next_version = max(current_version, requested_version) + 1
        state["state_version"] = next_version
        payload = {**state, "user_id": safe_user_id(user_id), "state_version": next_version}
        path.parent.mkdir(parents=True, exist_ok=True)
        return safe_write_json(path, payload)


def write_state_if_version(
    user_id: str | int,
    state: dict[str, Any],
    *,
    expected_version: int,
) -> bool:
    """Write a state transition only when the durable version is unchanged."""
    if not isinstance(state, dict):
        raise TypeError("dream state must be a dict")
    try:
        expected = max(0, int(expected_version))
    except (TypeError, ValueError):
        return False
    with _STATE_WRITE_LOCK:
        path = get_paths().dream_state_path(user_id)
        try:
            current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else default_state(user_id)
        except Exception:
            return False
        if not isinstance(current, dict):
            return False
        try:
            current_version = max(0, int(current.get("state_version", 0)))
        except (TypeError, ValueError):
            current_version = 0
        if current_version != expected:
            return False
        state["state_version"] = expected
        return write_state(user_id, state)


def configured_forced_impression_rounds() -> int:
    """Return configured post-exit forced rounds, clamped to a safe integer."""
    try:
        from core.config_loader import get_config

        raw = get_config().get("dream", {}).get("impression", {}).get(
            "forced_rounds", _FORCED_ROUNDS
        )
        return max(0, int(raw))
    except Exception:
        return _FORCED_ROUNDS


def consume_forced_impression_round(user_id: str | int, *, reality_owner_turn: bool) -> int:
    """Consume one forced round only for a real user-driven reality turn."""
    state = read_state(user_id)
    try:
        remaining = max(0, int(state.get("forced_impression_rounds_left", 0)))
    except (TypeError, ValueError):
        remaining = 0
    if reality_owner_turn and remaining > 0:
        state["forced_impression_rounds_left"] = remaining - 1
        write_state(user_id, state)
    return remaining


# ── Dream-local volatile state helpers ───────────────────────────────────────
# These fields live only inside dream_state while a dream is active.
# They are cleared by clear_local_state() at dream close and never persist
# to any reality store.


def get_local_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return the dream-local volatile fields from a state dict."""
    return {
        "emotional_tension": float(state.get("emotional_tension", 0.0)),
        "scene_state": state.get("scene_state"),
        "symbolic_anchors": list(state.get("symbolic_anchors", [])),
        # her cyber body state (dream-local, cleared at close)
        "body_state": state.get("body_state") or {},
    }


def patch_local_state(
    state: dict[str, Any],
    emotional_tension: float | None = None,
    scene_state: str | None = None,
    symbolic_anchors: list[str] | None = None,
    body_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a new state dict with updated dream-local volatile fields."""
    updated = dict(state)
    if emotional_tension is not None:
        updated["emotional_tension"] = max(0.0, min(1.0, float(emotional_tension)))
    if scene_state is not None:
        updated["scene_state"] = scene_state
    if symbolic_anchors is not None:
        updated["symbolic_anchors"] = list(symbolic_anchors)
    if body_state is not None:
        updated["body_state"] = body_state
    return updated


def clear_local_state(state: dict[str, Any]) -> dict[str, Any]:
    """Strip all dream-local volatile fields (call at dream close)."""
    out = dict(state)
    for key in (
        "emotional_tension", "scene_state", "symbolic_anchors",
        "body_state",  # her cyber body — dream-local, cleared at close
        "context_snapshot", "dream_id", "dream_started_at",
        "frozen_world",  # re-frozen from settings at next enter_dream
        "lucid_mode",  # session-local, cleared at dream close
        "dream_mode",  # session-local, cleared at dream close
        "scenario_injection_mode",  # session-local, cleared at dream close
        "last_assistant_turn_id",  # reconciler CAS identity, cleared at dream close
        "scenario_core",  # scenario kernel — session-local, cleared at dream close
        "mirror_core",  # mirror kernel — session-local, cleared at dream close
        "rpg_session",
    ):
        out.pop(key, None)
    return out


# ── UI status projection (Brief 94 §2) ───────────────────────────────────────
# Coarse, front-end-facing view of the raw DreamStatus state machine for
# GET /dream/state. Existing desktop client collapses every non-REALITY_CHAT
# status into "正在做梦无法聊天", which is wrong for REALITY_AFTERGLOW (chat is
# NOT blocked there — see get_reality_guard_status, which only blocks
# DREAM_ACTIVE / DREAM_CLOSING). This projection gives the client the real
# bucket plus enough timing data to render an accurate wait/idle message and
# to flag a stuck session, without adding a second polling endpoint.

_DREAMING_STATUSES: frozenset[str] = frozenset({
    DreamStatus.DREAM_ACTIVE.value,
    DreamStatus.DREAM_CLOSING.value,
    DreamStatus.DREAM_EXIT_REQUESTED.value,
    DreamStatus.DREAM_LOCKED.value,
})


def derive_dream_state_projection(state: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Project raw dream_state into {dream_state, since, expected_end, stuck}.

    dream_state: "idle" | "dreaming" | "cooldown" — coarse bucket over DreamStatus.
      cooldown only lasts DREAM_COOLDOWN_SECONDS after REALITY_AFTERGLOW starts;
      past that it reports "idle" (the raw status field itself never transitions
      back to REALITY_CHAT — see docs/dream.md §七).
    since: unix epoch seconds marking the start of the current bucket, or None
      when there is nothing to time (idle has no meaningful start).
    expected_end: unix epoch estimate, or None when it cannot be predicted.
      Dream length is user/LLM-driven and never estimable; cooldown's end is
      known (since + DREAM_COOLDOWN_SECONDS).
    stuck: dreaming bucket has run past DREAM_STUCK_THRESHOLD_SECONDS — a
      heuristic signal only, never affects hard_exit or any dream invariant.

    blocks_chat is intentionally NOT computed here — callers must use
    get_reality_guard_status(uid), the same fail-closed check chat.py enforces,
    to avoid the two diverging (this function only sees the already-loaded,
    fail-open-to-default state dict).
    """
    if now is None:
        now = time.time()

    status = state.get("status", DreamStatus.REALITY_CHAT.value)

    if status in _DREAMING_STATUSES:
        since = state.get("dream_started_at")
        stuck = since is not None and (now - float(since)) > DREAM_STUCK_THRESHOLD_SECONDS
        return {
            "dream_state": "dreaming",
            "since": since,
            "expected_end": None,
            "stuck": bool(stuck),
        }

    if status == DreamStatus.REALITY_AFTERGLOW.value:
        last_exited_at = state.get("last_exited_at")
        if last_exited_at is not None and (now - float(last_exited_at)) < DREAM_COOLDOWN_SECONDS:
            since = float(last_exited_at)
            return {
                "dream_state": "cooldown",
                "since": since,
                "expected_end": since + DREAM_COOLDOWN_SECONDS,
                "stuck": False,
            }

    return {"dream_state": "idle", "since": None, "expected_end": None, "stuck": False}
