"""User facts owned by one character and user. Missing data stays empty.

The legacy global_facts wire field is accepted by the fixation writers, but
its contents are persisted and recalled only for the frozen character.
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from core.data_paths import DEFAULT_CHAR_ID
from core.error_handler import log_error
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope
from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)

# ── Whitelist: facts learned by this character ───────────────────────────────

ALLOWED_FIELDS: frozenset[str] = frozenset({
    "preferred_language",
    "timezone",
    "device_os",
    "project_paths",
    "stable_preferences",
    "known_projects",
    "writing_style_preferences",
    "tool_usage_preferences",
    "pronoun",   # 用于记忆渲染：她/他/祂/TA/它，跨角色客观属性
})

_VALID_PRONOUNS: frozenset[str] = frozenset({"她", "他", "祂", "TA", "它"})

# Brief 89: cap on how many global_facts entries a single fixation/salvage run may persist
_MAX_GLOBAL_FACTS_PER_RUN = 3

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

def _path(uid: str, *, char_id: str = DEFAULT_CHAR_ID):
    scope = MemoryScope.reality_scope(str(uid), char_id)
    return resolve_path(scope, "user_facts")


# ── Public API ────────────────────────────────────────────────────────────────

def load_user_facts(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict:
    """Load this character's user facts; missing or unreadable files stay empty."""
    import json
    p = _path(uid, char_id=char_id)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        log_error("user_facts.load", e)
    return {}


def _serialized_facts(fn):
    @wraps(fn)
    def wrapped(uid, *args, char_id=DEFAULT_CHAR_ID, **kwargs):
        from core.memory.user_profile import _profile_lock
        with _profile_lock(uid, char_id):
            return fn(uid, *args, char_id=char_id, **kwargs)
    return wrapped


@_serialized_facts
def save_user_facts(uid: str, facts: dict, *, char_id: str = DEFAULT_CHAR_ID, trigger_signal: str = "facts_update") -> bool:
    """Overwrite user_facts with *facts* (must be a dict).

    Silently drops keys not in ALLOWED_FIELDS before saving.
    Returns True on success.
    """
    if not isinstance(facts, dict):
        logger.warning("[user_facts] save called with non-dict: %r", type(facts))
        return False
    clean = {k: v for k, v in facts.items() if k in ALLOWED_FIELDS}
    p = _path(uid, char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    before = load_user_facts(uid, char_id=char_id)
    saved = safe_write_json(p, clean)
    if saved:
        try:
            from core.memory.provenance_log import append
            for key in before.keys() | clean.keys():
                if before.get(key) != clean.get(key):
                    append(uid, char_id, artifact="user_facts", field=key,
                           before_gist=str(before.get(key, ""))[:120],
                           after_gist=str(clean.get(key, ""))[:120], trigger_signal=trigger_signal)
        except Exception as exc:
            log_error("user_facts.provenance", exc)
    return saved


@_serialized_facts
def update_user_facts(uid: str, patch: dict, *, char_id: str = DEFAULT_CHAR_ID, trigger_signal: str = "facts_update") -> tuple[dict, list[str]]:
    """Merge *patch* into existing user_facts.

    - Keys in ALLOWED_FIELDS are accepted.
    - Keys in DENIED_FIELDS or otherwise unknown are rejected (not written).
    - For list-valued fields, extend rather than replace.

    Returns (updated_facts, rejected_keys).
    """
    if not isinstance(patch, dict):
        logger.warning("[user_facts] update called with non-dict: %r", type(patch))
        return load_user_facts(uid, char_id=char_id), []

    current = load_user_facts(uid, char_id=char_id)
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
            for item in value:
                if item not in existing:
                    existing.append(item)
            current[key] = existing
        elif isinstance(existing, list) and not isinstance(value, list):
            if value not in existing:
                existing.append(value)
            current[key] = existing
        else:
            # Scalar: replace (allow explicit update of global facts)
            current[key] = value

    save_user_facts(uid, current, char_id=char_id, trigger_signal=trigger_signal)
    return current, rejected


def get_user_pronoun(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> str:
    """Return the user's preferred third-person pronoun (她/他/祂/TA/它).

    Defaults to '她' when unset or invalid.
    """
    p = load_user_facts(uid, char_id=char_id).get("pronoun")
    return p if p in _VALID_PRONOUNS else "她"


@_serialized_facts
def delete_user_fact(uid: str, key: str, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """Delete one key from user_facts.

    Returns True if key existed and was removed.
    Rejected for unknown / denied keys (returns False without error).
    """
    if key not in ALLOWED_FIELDS:
        logger.info("[user_facts] delete_user_fact: key %r not in ALLOWED_FIELDS, skip", key)
        return False
    current = load_user_facts(uid, char_id=char_id)
    if key not in current:
        return False
    before_val = current.pop(key)
    if not save_user_facts(uid, current, char_id=char_id):
        return False
    logger.info("[user_facts] deleted key=%r uid=%s (was=%r)", key, uid, before_val)
    return True


def clear_user_facts(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """Wipe user_facts to an empty dict."""
    return save_user_facts(uid, {}, char_id=char_id, trigger_signal="facts_clear")


def format_for_prompt(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> str:
    """Render user_facts as a compact prompt string.

    Returns '' when no facts are present so callers can skip the layer.
    """
    facts = load_user_facts(uid, char_id=char_id)
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


def apply_global_facts_patch(
    uid: str,
    char_id: str,
    items: list,
    *,
    trigger_signal: str = "",
) -> None:
    """Apply an LLM-proposed `global_facts` patch (Brief 89 fixation-chain分流).

    *items* is the parsed `[{key, value}]` list from an optional LLM output
    section (consolidate_to_identity / event_log_salvage).  Caps to
    `_MAX_GLOBAL_FACTS_PER_RUN` entries, skips keys whose value is unchanged
    (no write, no provenance), and routes accepted writes through
    `update_user_facts` (which enforces ALLOWED/DENIED).  Rejected keys are
    logged as a warning (observability: did the prompt-side exclusion leak?).

    Defensive by design: never raises.  Called after the caller's main
    artifact has already been persisted, so a malformed section here must
    not affect that write.
    """
    try:
        if not isinstance(items, list) or not items:
            return

        if len(items) > _MAX_GLOBAL_FACTS_PER_RUN:
            logger.info(
                "[user_facts] global_facts truncated %d -> %d uid=%s",
                len(items), _MAX_GLOBAL_FACTS_PER_RUN, uid,
            )
            items = items[:_MAX_GLOBAL_FACTS_PER_RUN]

        before = load_user_facts(uid, char_id=char_id)
        patch: dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if not isinstance(key, str) or not key:
                continue
            if "value" not in item:
                continue
            value = item["value"]
            if key in before and before[key] == value:
                continue  # same value as current — skip, avoid provenance noise
            patch[key] = value

        if not patch:
            return

        updated, rejected = update_user_facts(uid, patch, char_id=char_id, trigger_signal=trigger_signal)
        if rejected:
            logger.warning(
                "[user_facts] global_facts rejected keys (prompt exclusion leak?): "
                "%r uid=%s char_id=%s",
                rejected, uid, char_id,
            )

    except Exception as e:
        log_error("user_facts.apply_global_facts_patch", e)
