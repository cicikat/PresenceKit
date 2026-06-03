import json
import logging
from enum import Enum
from typing import Any

from core.safe_write import safe_write_json
from core.sandbox import get_paths, safe_user_id

logger = logging.getLogger(__name__)

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


def default_state(user_id: str | int) -> dict[str, Any]:
    return {
        "user_id": safe_user_id(user_id),
        "status": DreamStatus.REALITY_CHAT.value,
    }


class DreamGuardStatus(str, Enum):
    ALLOW = "ALLOW"
    BLOCK_ACTIVE = "BLOCK_ACTIVE"
    BLOCK_UNCERTAIN = "BLOCK_UNCERTAIN"


def get_reality_guard_status(user_id: str | int) -> DreamGuardStatus:
    """
    Fail-closed dream guard for reality turns.

    ALLOW:           File missing (normal no-dream state) or dream is inactive.
    BLOCK_ACTIVE:    Dream is DREAM_ACTIVE or DREAM_CLOSING — reject reality turn.
    BLOCK_UNCERTAIN: File exists but unreadable / corrupt / invalid status —
                     caller must treat as fail-closed (reject reality turn).

    Only FileNotFoundError → ALLOW; any other I/O or parse error → BLOCK_UNCERTAIN.
    """
    path = get_paths().dream_state_path(user_id)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DreamGuardStatus.ALLOW
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

    return DreamGuardStatus.ALLOW


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
    return data


def write_state(user_id: str | int, state: dict[str, Any]) -> bool:
    if not isinstance(state, dict):
        raise TypeError("dream state must be a dict")

    status = state.get("status")
    if isinstance(status, DreamStatus):
        state = {**state, "status": status.value}
        status = state["status"]
    if status not in {item.value for item in DreamStatus}:
        raise ValueError(f"unknown dream status: {status!r}")

    payload = {**state, "user_id": safe_user_id(user_id)}
    path = get_paths().dream_state_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return safe_write_json(path, payload)


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
        "context_snapshot", "dream_id",
        "frozen_world",  # re-frozen from settings at next enter_dream
        "lucid_mode",  # session-local, cleared at dream close
    ):
        out.pop(key, None)
    return out
