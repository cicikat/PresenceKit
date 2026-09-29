"""Uid-global persistence for objective health and device observations."""

from __future__ import annotations

import copy
import json
import logging
from datetime import date
from pathlib import Path
from threading import Lock, RLock
from typing import Callable

from core.data_paths import DEFAULT_CHAR_ID
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope
from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)

DEFAULT_HEALTH_STATE = {
    "sleep_segments": [],
    "heart_rate_events": [],
    "hds_samples": [],
    "hds_last_signal": {},
    "hds_last_analysis": {},
    "phone_sensor_log": [],
    "phone_sensor_today": None,
    "last_period_date": None,
}

_HEALTH_FIELDS = tuple(DEFAULT_HEALTH_STATE)
_LEGACY_OBJECTIVE_HEALTH_FIELDS = tuple(field for field in _HEALTH_FIELDS if field not in {"last_period_date", "hds_samples", "hds_last_signal", "hds_last_analysis"})
_PERIOD_MIGRATION_MARKER = "_period_date_migration_complete"
_health_locks: dict[str, RLock] = {}
_health_locks_guard = Lock()


def _health_lock(uid: str) -> RLock:
    key = str(uid)
    with _health_locks_guard:
        lock = _health_locks.get(key)
        if lock is None:
            lock = RLock()
            _health_locks[key] = lock
        return lock


def _path(uid: str) -> Path:
    return resolve_path(MemoryScope.global_scope(str(uid)), "health_state")


def _default_state() -> dict:
    return copy.deepcopy(DEFAULT_HEALTH_STATE)


def _normalized_state(raw: object) -> dict:
    state = _default_state()
    if isinstance(raw, dict):
        for field in _HEALTH_FIELDS:
            if field in raw:
                state[field] = copy.deepcopy(raw[field])
    if _valid_period_date(state["last_period_date"]) is None:
        state["last_period_date"] = None
    if isinstance(raw, dict) and raw.get(_PERIOD_MIGRATION_MARKER) is True:
        state[_PERIOD_MIGRATION_MARKER] = True
    return state


def _valid_period_date(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _parse_period_date(value: object) -> str:
    parsed = _valid_period_date(value)
    if parsed is None:
        raise ValueError("last_period_date must be a valid YYYY-MM-DD date")
    return parsed


def _has_legacy_data(profile: dict) -> bool:
    return any(profile.get(field) not in (None, [], {}) for field in _LEGACY_OBJECTIVE_HEALTH_FIELDS)


def _active_character_id() -> str | None:
    try:
        from core import pipeline_registry

        pipeline = pipeline_registry.get()
        candidate = getattr(pipeline, "_active_character_id", None)
        return candidate if isinstance(candidate, str) and candidate else None
    except Exception:
        return None


def _legacy_profiles(uid: str) -> list[tuple[str, dict]]:
    """Return populated legacy profiles without constructing any new paths."""
    try:
        from core.sandbox import get_paths
        from core.memory import user_profile

        memory_root = get_paths()._p("runtime", "memory")
        if not memory_root.is_dir():
            return []
        profiles: list[tuple[str, dict]] = []
        for char_dir in sorted(memory_root.iterdir(), key=lambda path: path.name):
            if not char_dir.is_dir() or char_dir.name == "global":
                continue
            profile_path = char_dir / str(uid) / "profile.json"
            if not profile_path.is_file():
                continue
            profile = user_profile.load(uid, char_id=char_dir.name)
            if _has_legacy_data(profile):
                profiles.append((char_dir.name, profile))
        return profiles
    except Exception:
        logger.warning("Unable to inspect legacy health state for uid=%s", uid, exc_info=True)
        return []


def _legacy_period_profiles(uid: str) -> list[tuple[str, dict]]:
    """Return legacy profiles that contain a valid period date only."""
    try:
        from core.sandbox import get_paths
        from core.memory import user_profile

        memory_root = get_paths()._p("runtime", "memory")
        if not memory_root.is_dir():
            return []
        profiles: list[tuple[str, dict]] = []
        for char_dir in sorted(memory_root.iterdir(), key=lambda path: path.name):
            if not char_dir.is_dir() or char_dir.name == "global":
                continue
            profile_path = char_dir / str(uid) / "profile.json"
            if not profile_path.is_file():
                continue
            profile = user_profile.load(uid, char_id=char_dir.name)
            period_date = _valid_period_date(profile.get("last_period_date"))
            if period_date is not None:
                profiles.append((char_dir.name, {"last_period_date": period_date}))
        return profiles
    except Exception:
        logger.warning("Unable to inspect legacy period state for uid=%s", uid, exc_info=True)
        return []


def _select_legacy_profile(profiles: list[tuple[str, dict]]) -> tuple[str, dict]:
    active = _active_character_id()
    by_char = dict(profiles)
    selected_char = next(
        (
            candidate
            for candidate in (active, DEFAULT_CHAR_ID)
            if candidate is not None and candidate in by_char
        ),
        profiles[0][0],
    )
    return selected_char, by_char[selected_char]


def _load_legacy_state(uid: str) -> dict | None:
    profiles = _legacy_profiles(uid)
    if not profiles:
        return None

    selected_char, selected = _select_legacy_profile(profiles)

    variants = {
        json.dumps(
            {field: profile.get(field) for field in _HEALTH_FIELDS},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        for _, profile in profiles
    }
    if len(variants) > 1:
        logger.warning(
            "Legacy health state conflict for uid=%s across %d character profiles; selected char_id=%s",
            uid,
            len(profiles),
            selected_char,
        )
    return _normalized_state(selected)


def _load_unlocked(uid: str) -> dict:
    path = _path(uid)
    try:
        if path.is_file():
            return _normalized_state(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.warning("Unable to load health state for uid=%s", uid, exc_info=True)
        return _default_state()

    legacy = _load_legacy_state(uid)
    if legacy is not None:
        # The existence check and first import are serialized by the uid lock.
        if not save(uid, legacy):
            logger.warning("Unable to persist migrated health state for uid=%s", uid)
        return legacy
    return _default_state()


def load(uid: str) -> dict:
    """Load a uid-global health snapshot, lazily importing legacy profile data once."""
    with _health_lock(uid):
        return _load_unlocked(uid)


def save(uid: str, state: dict) -> bool:
    """Atomically save the fields owned by this state, without a character scope."""
    with _health_lock(uid):
        return safe_write_json(_path(uid), _normalized_state(state), keep_bak=False)


def mutate(uid: str, callback: Callable[[dict], None]) -> dict:
    """Reload, modify, and atomically save under one uid-specific lock."""
    with _health_lock(uid):
        state = _load_unlocked(uid)
        callback(state)
        save(uid, state)
        return state


def get_period_info(uid: str) -> dict:
    """Return the uid-global period input, lazily importing one legacy value."""
    with _health_lock(uid):
        state = _load_unlocked(uid)
        current = _valid_period_date(state.get("last_period_date"))
        if current is not None:
            return {"last_period_date": current}
        if state.get(_PERIOD_MIGRATION_MARKER) is True:
            return {"last_period_date": None}

        profiles = _legacy_period_profiles(uid)
        if not profiles:
            return {"last_period_date": None}

        selected_char, selected = _select_legacy_profile(profiles)
        variants = {profile["last_period_date"] for _, profile in profiles}
        if len(variants) > 1:
            logger.warning(
                "Legacy period state conflict for uid=%s across %d character profiles; selected char_id=%s",
                uid,
                len(profiles),
                selected_char,
            )
        state["last_period_date"] = selected["last_period_date"]
        state[_PERIOD_MIGRATION_MARKER] = True
        if not save(uid, state):
            logger.warning("Unable to persist migrated period state for uid=%s", uid)
        return {"last_period_date": state["last_period_date"]}


def set_period_date(uid: str, date_str: str) -> dict:
    """Set the uid-global period input after strict date validation."""
    parsed = _parse_period_date(date_str)
    mutate(uid, lambda state: state.__setitem__("last_period_date", parsed))
    return {"last_period_date": parsed}


def clear_period_date(uid: str) -> dict:
    """Clear the uid-global period input through an explicit operation."""
    def clear(state: dict) -> None:
        state["last_period_date"] = None
        state[_PERIOD_MIGRATION_MARKER] = True

    mutate(uid, clear)
    return {"last_period_date": None}
