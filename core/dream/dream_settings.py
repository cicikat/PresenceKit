"""
Per-uid dream session settings.

These switches control ONLY what goes into the frozen snapshot at dream entry.
They NEVER open live memory access during the dream — that is always blocked.

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
from enum import Enum
from typing import Any

from core.safe_write import safe_write_json
from core.data_paths import _LAYOUT_DREAM
from core.migration import for_read
from core.sandbox import get_paths, safe_user_id

logger = logging.getLogger(__name__)


class MemoryAccess(str, Enum):
    card_only = "card_only"
    relationship_summary = "relationship_summary"
    full_snapshot = "full_snapshot"


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


def _path(user_id: str | int):
    return get_paths().dream_settings_path(user_id)


def _read_path(user_id: str | int):
    """S6 读降级：v1 新路径不存在时 fallback 到 legacy 路径。写始终走 _path()。"""
    new = _path(user_id)
    if _LAYOUT_DREAM == "legacy":
        return new
    old = get_paths()._p("dreams", "settings", safe_user_id(user_id) + ".json")
    return for_read(new, old)


def load(user_id: str | int) -> dict[str, Any]:
    path = _read_path(user_id)
    if not path.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(_DEFAULTS)
        data = _migrate_legacy(data)
        return {**_DEFAULTS, **data}
    except Exception as e:
        logger.warning(f"[dream_settings] read failed uid={user_id}: {e}")
        return dict(_DEFAULTS)


def save(user_id: str | int, settings: dict[str, Any]) -> bool:
    merged = {**_DEFAULTS, **settings}
    p = _path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    return safe_write_json(p, merged)


def set_field(user_id: str | int, key: str, value: Any) -> bool:
    s = load(user_id)
    s[key] = value
    return save(user_id, s)
