"""
Per-character dream session settings.

These switches control ONLY what goes into the frozen snapshot at dream entry.
They NEVER open live memory access during the dream — that is always blocked.
Physical authority is ``data/runtime/dreams/{char_id}/settings/{uid}.json``.
Callers must pass the character that owns the dream; live active / config
default directories are not a shared authority.

memory_access tiers (D4_frozen_reality content):
  card_only            — relationship_state + entry_reason only (sandbox mode)
  relationship_summary — + recent_reality_context + profile_impression
  full_snapshot        — + episodic_summary + mid_term_context

boundary_level (D5 body projection visibility for the character):
  vague / body_perceptible (default) / numbers_visible / threshold_break(seam)

Migration from legacy booleans:
  amnesia=True                           → card_only
  amnesia=False, keep_impression=True    → relationship_summary
  amnesia=False, keep_impression=False   → card_only
"""

import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from core.safe_write import safe_write_json
from core.data_paths import DEFAULT_CHAR_ID, _LAYOUT_DREAM
from core.memory.scope import require_character_id
from core.migration import for_read
from core.sandbox import get_paths, safe_user_id

logger = logging.getLogger(__name__)


class MemoryAccess(str, Enum):
    card_only = "card_only"
    relationship_summary = "relationship_summary"
    full_snapshot = "full_snapshot"


SCENARIO_INJECTION_MODES: frozenset[str] = frozenset({"strict_stage", "full_script"})
DEFAULT_SCENARIO_INJECTION_MODE = "strict_stage"


_DEFAULTS: dict[str, Any] = {
    "enable_dream_lorebook": True,
    "memory_access": MemoryAccess.relationship_summary.value,
    "boundary_level": "body_perceptible",
    "world_layer": "reality_derived",
    "lucid_mode": "lucid_shared",
    "jailbreak_presets": ["default"],
    "display": {"physiological_arousal": False},
    "reality_context_full_turns": 3,
    "scenario_arc_mode": "linear",
    "scenario_injection_mode": DEFAULT_SCENARIO_INJECTION_MODE,
}


def _migrate_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """One-shot migration from old amnesia/keep_impression booleans and single jailbreak_preset string."""
    if "memory_access" not in data:
        amnesia = data.get("amnesia", False)
        keep_impression = data.get("keep_impression", True)
        if amnesia:
            data["memory_access"] = MemoryAccess.card_only.value
        elif keep_impression:
            data["memory_access"] = MemoryAccess.relationship_summary.value
        else:
            data["memory_access"] = MemoryAccess.card_only.value
    # Migrate single jailbreak_preset string → jailbreak_presets list
    if "jailbreak_presets" not in data and "jailbreak_preset" in data:
        data["jailbreak_presets"] = [data.pop("jailbreak_preset")]
    elif "jailbreak_preset" in data:
        data.pop("jailbreak_preset")
    return data


def _configured_default_char_id() -> str:
    """Live ``character.default``. Distinct from the frozen historical owner."""
    from core.config_loader import get_config

    default = str((get_config().get("character") or {}).get("default") or "").strip()
    return default or DEFAULT_CHAR_ID


def _legacy_owner_record_path(user_id: str) -> Path:
    """Per-owner freeze of which character may read the uid-only tree."""
    return get_paths()._p(
        "runtime", "dreams", "global", safe_user_id(user_id), "legacy_dream_settings_owner.json",
    )


def _legacy_uid_only_path(user_id: str | int) -> Path:
    """Physical uid-only old file. Does not grant any character read eligibility."""
    return get_paths()._p("dreams", "settings", safe_user_id(user_id) + ".json")


def _read_frozen_legacy_owner(user_id: str) -> str | None:
    path = _legacy_owner_record_path(user_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[dream_settings] legacy owner read failed uid=%s: %s", user_id, e)
        return None
    if not isinstance(payload, dict):
        return None
    owner = str(payload.get("char_id") or "").strip()
    return owner or None


def _freeze_legacy_owner(user_id: str, char_id: str) -> str:
    """Persist historical default ownership once. Never reassigns on later switches."""
    require_character_id(char_id)
    existing = _read_frozen_legacy_owner(user_id)
    if existing:
        return existing
    record = {
        "char_id": char_id,
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "source": "configured_default_at_first_compatible_read",
    }
    if not safe_write_json(_legacy_owner_record_path(user_id), record, keep_bak=False):
        logger.warning(
            "[dream_settings] failed to freeze legacy owner uid=%s char=%s; using in-memory claim only",
            user_id,
            char_id,
        )
    return char_id


def historical_legacy_dream_settings_char_id(user_id: str | int) -> str | None:
    """Return the frozen historical default character for this owner's uid-only settings.

    Distinct from live ``character.default`` and the current active character.
    Returns None when this owner has no uid-only dream settings file.
    """
    uid = safe_user_id(user_id)
    if not _legacy_uid_only_path(uid).is_file():
        return _read_frozen_legacy_owner(uid)
    frozen = _read_frozen_legacy_owner(uid)
    if frozen:
        return frozen
    return _freeze_legacy_owner(uid, _configured_default_char_id())


def may_read_legacy_dream_settings(user_id: str | int, char_id: str) -> bool:
    """True only for the frozen historical default character of this owner."""
    require_character_id(char_id)
    owner = historical_legacy_dream_settings_char_id(user_id)
    return owner is not None and owner == char_id


def _path(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID):
    require_character_id(char_id)
    return get_paths().dream_settings_path(user_id, char_id=char_id)


def _read_path(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID):
    """Canonical per-char file, with gated uid-only fallback for the frozen historical default.

    Writes always go to ``_path()``. Old ``data/dreams/settings/{uid}.json`` is never
    copied into other character trees and is never deleted by this module.
    """
    require_character_id(char_id)
    new = _path(user_id, char_id=char_id)
    if _LAYOUT_DREAM == "legacy":
        return new
    if new.exists():
        return new
    if not may_read_legacy_dream_settings(user_id, char_id):
        return new
    old = _legacy_uid_only_path(user_id)
    return for_read(new, old)


def load(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    require_character_id(char_id)
    path = _read_path(user_id, char_id=char_id)
    if not path.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(_DEFAULTS)
        data = _migrate_legacy(data)
        return {**_DEFAULTS, **data}
    except Exception as e:
        logger.warning("[dream_settings] read failed uid=%s char=%s: %s", user_id, char_id, e)
        return dict(_DEFAULTS)


def save(user_id: str | int, settings: dict[str, Any], *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    require_character_id(char_id)
    merged = {**_DEFAULTS, **settings}
    p = _path(user_id, char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    return safe_write_json(p, merged)


def set_field(user_id: str | int, key: str, value: Any, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    s = load(user_id, char_id=char_id)
    s[key] = value
    return save(user_id, s, char_id=char_id)


def observability_snapshot(user_id: str | int, *, char_id: str) -> dict[str, Any]:
    """Content-free effective-state snapshot for ops. Never includes settings body."""
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    canonical = _path(uid, char_id=char_id)
    legacy = _legacy_uid_only_path(uid)
    frozen = historical_legacy_dream_settings_char_id(uid)
    return {
        "uid": uid,
        "char_id": char_id,
        "canonical_exists": canonical.is_file(),
        "legacy_uid_only_exists": legacy.is_file(),
        "legacy_eligible": may_read_legacy_dream_settings(uid, char_id),
        "frozen_historical_char_id": frozen,
        "configured_default_char_id": _configured_default_char_id(),
    }
