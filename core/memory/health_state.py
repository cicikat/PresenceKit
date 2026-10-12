"""Character-owned health and device observations; no cross-character fallback."""

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
_health_locks: dict[tuple[str, str], RLock] = {}
_health_locks_guard = Lock()


def _health_lock(uid: str, char_id: str) -> RLock:
    key = (str(uid), char_id)
    with _health_locks_guard:
        lock = _health_locks.get(key)
        if lock is None:
            lock = RLock()
            _health_locks[key] = lock
        return lock


def _path(uid: str, char_id: str) -> Path:
    return resolve_path(MemoryScope.reality_scope(str(uid), char_id), "health_state")


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


def _resolve_character(char_id: str | None) -> str:
    from core.memory.scope import require_character_id
    if char_id is not None:
        return require_character_id(char_id)
    from core.config_loader import get_config
    try:
        from core.pipeline_registry import get
        candidate = getattr(get(), "_active_character_id", None)
        if isinstance(candidate, str) and candidate:
            return require_character_id(candidate)
    except Exception:
        pass
    return require_character_id((get_config().get("character") or {}).get("default") or DEFAULT_CHAR_ID)


def _load_unlocked(uid: str, char_id: str) -> dict:
    path = _path(uid, char_id)
    try:
        if path.is_file():
            return _normalized_state(json.loads(path.read_text(encoding="utf-8")))
        # Historical profile data has explicit ownership. Read only this
        # character's profile, never the old shared health ledger.
        from core.memory.user_profile import load as load_profile
        legacy = _normalized_state(load_profile(uid, char_id=char_id))
        if any(legacy.get(field) not in (None, [], {}) for field in _HEALTH_FIELDS):
            save(uid, legacy, char_id=char_id)
        return legacy
    except Exception:
        logger.warning("Unable to load character health state uid=%s char=%s", uid, char_id, exc_info=True)
        return _default_state()


def load(uid: str, *, char_id: str | None = None) -> dict:
    char_id = _resolve_character(char_id)
    with _health_lock(uid, char_id):
        return _load_unlocked(uid, char_id)


def save(uid: str, state: dict, *, char_id: str | None = None) -> bool:
    char_id = _resolve_character(char_id)
    with _health_lock(uid, char_id):
        return safe_write_json(_path(uid, char_id), _normalized_state(state), keep_bak=False)


def mutate(uid: str, callback: Callable[[dict], None], *, char_id: str | None = None) -> dict:
    char_id = _resolve_character(char_id)
    with _health_lock(uid, char_id):
        state = _load_unlocked(uid, char_id)
        callback(state)
        if not save(uid, state, char_id=char_id):
            logger.warning("Unable to save health state uid=%s char=%s", uid, char_id)
        return state


def get_period_info(uid: str, *, char_id: str | None = None) -> dict:
    return {"last_period_date": load(uid, char_id=char_id)["last_period_date"]}


def set_period_date(uid: str, date_str: str, *, char_id: str | None = None) -> dict:
    parsed = _parse_period_date(date_str)
    mutate(uid, lambda state: state.__setitem__("last_period_date", parsed), char_id=char_id)
    return {"last_period_date": parsed}


def clear_period_date(uid: str, *, char_id: str | None = None) -> dict:
    def clear(state: dict) -> None:
        state["last_period_date"] = None
        state[_PERIOD_MIGRATION_MARKER] = True
    mutate(uid, clear, char_id=char_id)
    return {"last_period_date": None}
