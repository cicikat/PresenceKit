"""
core/memory/user_facts.py
=========================
P1-4: Global user facts — objective, cross-character facts about the user.

Scope:  MemoryScope.global_scope(uid)  — uid-only, NO char_id.
Path:   data/runtime/memory/global/{uid}/user_facts.json

What belongs here (ALLOWED):
  Low-risk objective facts that every character should know, regardless of
  which character is active.  Examples: preferred_language, timezone,
  device_os, project_paths, writing_style_preferences, stable_preferences,
  known_projects, tool_usage_preferences.

What does NOT belong here (DENIED):
  Anything character-subjective or relationship-specific — these stay in
  the per-character scoped profile/identity:
    - Character relationship history, nickname, intimacy level
    - Character's subjective impression or emotional evaluation of the user
    - afterglow / dream residue
    - mood / hidden_state
    - Current character's emotional judgement of the user

API:
    load_user_facts(uid) -> dict
    save_user_facts(uid, facts) -> bool
    update_user_facts(uid, patch) -> tuple[dict, list[str]]
        Returns (updated_facts, list_of_rejected_keys).
    clear_user_facts(uid) -> bool
    format_for_prompt(uid) -> str   (returns '' when empty)
"""

from __future__ import annotations

import logging
from typing import Any

from core.error_handler import log_error
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope
from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)

# ── Whitelist: objective facts that are safe across all characters ────────────

ALLOWED_FIELDS: frozenset[str] = frozenset({
    "preferred_language",
    "timezone",
    "device_os",
    "project_paths",
    "stable_preferences",
    "known_projects",
    "writing_style_preferences",
    "tool_usage_preferences",
    "pronoun",   # 用于记忆渲染：她/他/TA/它，跨角色客观属性
})

_VALID_PRONOUNS: frozenset[str] = frozenset({"她", "他", "TA", "它"})

# ── Explicit deny: subjective / relationship / emotional fields ───────────────

DENIED_FIELDS: frozenset[str] = frozenset({
    # relationship / character subjective
    "nickname",
    "affection",
    "relation",
    "intimacy",
    "impression",
    "character_opinion",
    # emotional / physical state
    "mood",
    "hidden_state",
    "afterglow",
    "afterglow_residue",
    "dream_residue",
    # profile fields that belong in scoped profile/identity
    "name",
    "location",
    "pets",
    "interests",
    "occupation",
    "important_facts",
    "last_period_date",
    "sleep_segments",
    "phone_sensor_today",
})


# ── Path helpers ──────────────────────────────────────────────────────────────

def _path(uid: str):
    scope = MemoryScope.global_scope(str(uid))
    return resolve_path(scope, "user_facts")


# ── Public API ────────────────────────────────────────────────────────────────

def load_user_facts(uid: str) -> dict:
    """Load global user facts.  Returns {} when file absent or unreadable."""
    import json
    p = _path(uid)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        log_error("user_facts.load", e)
    return {}


def save_user_facts(uid: str, facts: dict) -> bool:
    """Overwrite user_facts with *facts* (must be a dict).

    Silently drops keys not in ALLOWED_FIELDS before saving.
    Returns True on success.
    """
    if not isinstance(facts, dict):
        logger.warning("[user_facts] save called with non-dict: %r", type(facts))
        return False
    clean = {k: v for k, v in facts.items() if k in ALLOWED_FIELDS}
    p = _path(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    return safe_write_json(p, clean)


def update_user_facts(uid: str, patch: dict) -> tuple[dict, list[str]]:
    """Merge *patch* into existing user_facts.

    - Keys in ALLOWED_FIELDS are accepted.
    - Keys in DENIED_FIELDS or otherwise unknown are rejected (not written).
    - For list-valued fields, extend rather than replace.

    Returns (updated_facts, rejected_keys).
    """
    if not isinstance(patch, dict):
        logger.warning("[user_facts] update called with non-dict: %r", type(patch))
        return load_user_facts(uid), []

    current = load_user_facts(uid)
    rejected: list[str] = []

    for key, value in patch.items():
        if key in DENIED_FIELDS:
            logger.info("[user_facts] rejected denied field: %r", key)
            rejected.append(key)
            continue
        if key not in ALLOWED_FIELDS:
            logger.info("[user_facts] rejected unknown field: %r", key)
            rejected.append(key)
            continue
        # Merge logic: lists are extended; other values replace only if absent
        existing = current.get(key)
        if isinstance(existing, list) and isinstance(value, list):
            seen = set(existing)
            for item in value:
                if item not in seen:
                    existing.append(item)
                    seen.add(item)
            current[key] = existing
        elif isinstance(existing, list) and not isinstance(value, list):
            if value not in existing:
                existing.append(value)
            current[key] = existing
        else:
            # Scalar: replace (allow explicit update of global facts)
            current[key] = value

    save_user_facts(uid, current)
    return current, rejected


def get_user_pronoun(uid: str) -> str:
    """Return the user's preferred third-person pronoun (她/他/TA/它).

    Defaults to '她' when unset or invalid.
    """
    p = load_user_facts(uid).get("pronoun")
    return p if p in _VALID_PRONOUNS else "她"


def delete_user_fact(uid: str, key: str) -> bool:
    """Delete one key from user_facts.

    Returns True if key existed and was removed.
    Rejected for unknown / denied keys (returns False without error).
    """
    if key not in ALLOWED_FIELDS:
        logger.info("[user_facts] delete_user_fact: key %r not in ALLOWED_FIELDS, skip", key)
        return False
    current = load_user_facts(uid)
    if key not in current:
        return False
    before_val = current.pop(key)
    save_user_facts(uid, current)

    try:
        from core.memory import provenance_log
        from core.memory.scope import MemoryScope
        # user_facts is global-scope (no char_id) — use empty string char_id placeholder
        # provenance_log requires require_character_id; pass "global" as sentinel
        # We call append directly with a known-safe char sentinel understood by the path resolver.
        # Since provenance_log uses MemoryScope.reality_scope we need a char_id;
        # global facts don't have one — skip provenance for global-scope facts.
        pass  # provenance skipped: user_facts is global-scope, no char_id available
    except Exception:
        pass

    logger.info("[user_facts] deleted key=%r uid=%s (was=%r)", key, uid, before_val)
    return True


def clear_user_facts(uid: str) -> bool:
    """Wipe user_facts to an empty dict."""
    p = _path(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    return safe_write_json(p, {})


def format_for_prompt(uid: str) -> str:
    """Render user_facts as a compact prompt string.

    Returns '' when no facts are present so callers can skip the layer.
    """
    facts = load_user_facts(uid)
    if not facts:
        return ""
    lines: list[str] = []
    for key, value in facts.items():
        if key == "pronoun" or value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            lines.append(f"{key}: {', '.join(str(v) for v in value)}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)
