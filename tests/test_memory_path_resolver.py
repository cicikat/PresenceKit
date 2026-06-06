"""
Unit tests for core/memory/path_resolver.py — P1-2A.

Covers:
  - Reality-scoped artifacts: path contains both char_id and uid
  - Character-global artifacts: path contains char_id, NOT uid
  - Global-scoped artifacts: path contains uid, NOT char_id
  - Dream-scoped artifacts: path contains char_id, uid, and world_id
  - Domain/artifact mismatch → ValueError
  - Unknown artifact → ValueError
  - No yexuan fallback
  - No directory creation side-effects
  - Import smoke test (circular dependency check)
"""

import pytest

from core.memory.scope import MemoryScope
from core.memory.path_resolver import resolve_path


# ---------------------------------------------------------------------------
# Shared scopes
# ---------------------------------------------------------------------------

UID = "u123"
CHAR = "char1"
WORLD = "w42"

REALITY = MemoryScope.reality_scope(UID, CHAR)
GLOBAL  = MemoryScope.global_scope(UID)
DREAM   = MemoryScope.dream_scope(UID, CHAR, WORLD)


def _s(path) -> str:
    return str(path).replace("\\", "/")


# ---------------------------------------------------------------------------
# 1. history — reality, (char_id, uid)
# ---------------------------------------------------------------------------

def test_history_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "history"))
    assert CHAR in p
    assert UID in p


# ---------------------------------------------------------------------------
# 2. event_log — reality, (char_id, uid)
# ---------------------------------------------------------------------------

def test_event_log_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "event_log"))
    assert CHAR in p
    assert UID in p


# ---------------------------------------------------------------------------
# 3. mid_term — reality, (char_id, uid)
# ---------------------------------------------------------------------------

def test_mid_term_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "mid_term"))
    assert CHAR in p
    assert UID in p


# ---------------------------------------------------------------------------
# 4. profile — reality, (char_id, uid)
# ---------------------------------------------------------------------------

def test_profile_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "profile"))
    assert CHAR in p
    assert UID in p


# ---------------------------------------------------------------------------
# 5. identity — reality, (char_id, uid)
# ---------------------------------------------------------------------------

def test_identity_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "identity"))
    assert CHAR in p
    assert UID in p


# ---------------------------------------------------------------------------
# 6. hidden_state — reality, (char_id, uid)
# ---------------------------------------------------------------------------

def test_hidden_state_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "hidden_state"))
    assert CHAR in p
    assert UID in p


# ---------------------------------------------------------------------------
# 7. afterglow_residue — reality, (char_id, uid)
# ---------------------------------------------------------------------------

def test_afterglow_residue_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "afterglow_residue"))
    assert CHAR in p
    assert UID in p


# ---------------------------------------------------------------------------
# 8. impression — reality (dream-origin), (char_id, uid)
# ---------------------------------------------------------------------------

def test_impression_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "impression"))
    assert CHAR in p
    assert UID in p


# ---------------------------------------------------------------------------
# 9. mood_state — character-global, char_id only
# ---------------------------------------------------------------------------

def test_mood_state_contains_char_not_uid(sandbox):
    p = _s(resolve_path(REALITY, "mood_state"))
    assert CHAR in p
    assert UID not in p


# ---------------------------------------------------------------------------
# 10. garden_plants / garden_storage — character-global, char_id only
# ---------------------------------------------------------------------------

def test_garden_plants_contains_char_not_uid(sandbox):
    p = _s(resolve_path(REALITY, "garden_plants"))
    assert CHAR in p
    assert UID not in p


def test_garden_storage_contains_char_not_uid(sandbox):
    p = _s(resolve_path(REALITY, "garden_storage"))
    assert CHAR in p
    assert UID not in p


# ---------------------------------------------------------------------------
# 11. user_facts — global, uid only
# ---------------------------------------------------------------------------

def test_user_facts_contains_uid_not_char(sandbox):
    p = _s(resolve_path(GLOBAL, "user_facts"))
    assert UID in p
    assert CHAR not in p


# ---------------------------------------------------------------------------
# 12. dream_state — dream, (char_id, uid, world_id)
# ---------------------------------------------------------------------------

def test_dream_state_contains_char_uid_world(sandbox):
    p = _s(resolve_path(DREAM, "dream_state"))
    assert CHAR in p
    assert UID in p
    assert WORLD in p


# ---------------------------------------------------------------------------
# 13. global scope + history → ValueError
# ---------------------------------------------------------------------------

def test_global_scope_resolves_history_raises(sandbox):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(GLOBAL, "history")


# ---------------------------------------------------------------------------
# 14. reality scope + user_facts → ValueError
# ---------------------------------------------------------------------------

def test_reality_scope_resolves_user_facts_raises(sandbox):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(REALITY, "user_facts")


# ---------------------------------------------------------------------------
# 15. reality scope + dream_state → ValueError
# ---------------------------------------------------------------------------

def test_reality_scope_resolves_dream_state_raises(sandbox):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(REALITY, "dream_state")


# ---------------------------------------------------------------------------
# 16. dream scope + history → ValueError
# ---------------------------------------------------------------------------

def test_dream_scope_resolves_history_raises(sandbox):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(DREAM, "history")


# ---------------------------------------------------------------------------
# 17. unknown artifact → ValueError
# ---------------------------------------------------------------------------

def test_unknown_artifact_raises(sandbox):
    with pytest.raises(ValueError, match="unknown artifact"):
        resolve_path(REALITY, "nonexistent_artifact")


def test_empty_string_artifact_raises(sandbox):
    with pytest.raises(ValueError, match="unknown artifact"):
        resolve_path(REALITY, "")


# ---------------------------------------------------------------------------
# 18. No yexuan fallback
# ---------------------------------------------------------------------------

def test_no_yexuan_fallback_missing_character_id_raises():
    """MemoryScope construction fails for reality scope without character_id."""
    with pytest.raises(ValueError, match="character_id"):
        MemoryScope.from_payload({"uid": "u1", "domain": "reality"})


def test_resolver_uses_scope_character_id_verbatim(sandbox):
    """Resolver emits scope.character_id in path; no hardcoded 'yexuan' default."""
    scope = MemoryScope.reality_scope("u1", "custom_char")
    p = _s(resolve_path(scope, "history"))
    assert "custom_char" in p
    assert "yexuan" not in p


def test_resolver_with_non_default_char_id_all_reality_artifacts(sandbox):
    """All reality artifacts use the scope's character_id, not a hardcoded fallback."""
    reality_char_uid_artifacts = [
        "history", "event_log", "mid_term", "episodic", "memory_index",
        "fixation_state", "profile", "identity", "hidden_state",
        "afterglow_residue", "character_growth", "impression",
    ]
    scope = MemoryScope.reality_scope("u77", "xchar")
    for art in reality_char_uid_artifacts:
        p = _s(resolve_path(scope, art))
        assert "xchar" in p, f"char_id missing from {art} path: {p}"
        assert "yexuan" not in p, f"yexuan default leaked into {art} path: {p}"


# ---------------------------------------------------------------------------
# 19. Resolver does not create directories
# ---------------------------------------------------------------------------

def test_resolve_does_not_create_directories(sandbox):
    """Calling resolve_path must not create any directories as a side-effect."""
    scope = MemoryScope.reality_scope("u999", "newchar")
    import os

    # snapshot tmp_path contents before
    before = set(str(p) for p in sandbox._base.rglob("*"))

    for artifact in (
        "history", "mid_term", "profile", "hidden_state",
        "mood_state", "garden_plants",
    ):
        resolve_path(scope, artifact)

    resolve_path(MemoryScope.global_scope("u999"), "user_facts")
    resolve_path(MemoryScope.dream_scope("u999", "newchar", "w1"), "dream_state")

    after = set(str(p) for p in sandbox._base.rglob("*"))
    assert before == after, f"resolve_path created unexpected paths: {after - before}"


# ---------------------------------------------------------------------------
# 20. Import / circular dependency smoke test
# ---------------------------------------------------------------------------

def test_import_does_not_raise():
    import importlib
    importlib.import_module("core.memory.path_resolver")


def test_resolve_path_is_callable():
    from core.memory.path_resolver import resolve_path as rp
    assert callable(rp)


# ---------------------------------------------------------------------------
# Additional: episodic, memory_index, fixation_state, character_growth
# contain both char_id and uid
# ---------------------------------------------------------------------------

def test_episodic_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "episodic"))
    assert CHAR in p
    assert UID in p


def test_memory_index_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "memory_index"))
    assert CHAR in p
    assert UID in p


def test_fixation_state_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "fixation_state"))
    assert CHAR in p
    assert UID in p


def test_character_growth_contains_char_and_uid(sandbox):
    p = _s(resolve_path(REALITY, "character_growth"))
    assert CHAR in p
    assert UID in p


# ---------------------------------------------------------------------------
# Additional: trait_state, author_note_state, observations — char only
# ---------------------------------------------------------------------------

def test_trait_state_contains_char_not_uid(sandbox):
    p = _s(resolve_path(REALITY, "trait_state"))
    assert CHAR in p
    assert UID not in p


def test_author_note_state_contains_char_not_uid(sandbox):
    p = _s(resolve_path(REALITY, "author_note_state"))
    assert CHAR in p
    assert UID not in p


def test_observations_contains_char_not_uid(sandbox):
    p = _s(resolve_path(REALITY, "observations"))
    assert CHAR in p
    assert UID not in p


# ---------------------------------------------------------------------------
# P1-2F: episodic / memory_index path layout consistency
# Both must live under runtime/memory/{char_id}/{uid}/ — same root as mid_term
# ---------------------------------------------------------------------------

def test_episodic_exact_layout(sandbox):
    """episodic → runtime/memory/{char_id}/{uid}/episodic.json"""
    p = _s(resolve_path(REALITY, "episodic"))
    assert f"runtime/memory/{CHAR}/{UID}/episodic.json" in p


def test_memory_index_exact_layout(sandbox):
    """memory_index → runtime/memory/{char_id}/{uid}/memory_index.json"""
    p = _s(resolve_path(REALITY, "memory_index"))
    assert f"runtime/memory/{CHAR}/{UID}/memory_index.json" in p


def test_episodic_same_root_as_mid_term(sandbox):
    """episodic and mid_term must share the same parent directory (user_memory_root)."""
    ep = resolve_path(REALITY, "episodic")
    mt = resolve_path(REALITY, "mid_term")
    assert ep.parent == mt.parent


def test_memory_index_same_root_as_mid_term(sandbox):
    """memory_index and mid_term must share the same parent directory."""
    idx = resolve_path(REALITY, "memory_index")
    mt = resolve_path(REALITY, "mid_term")
    assert idx.parent == mt.parent


# ---------------------------------------------------------------------------
# P1-2G: history path layout consistency
# history must live under runtime/memory/{char_id}/{uid}/ — same root as mid_term
# ---------------------------------------------------------------------------

def test_history_exact_layout(sandbox):
    """history → runtime/memory/{char_id}/{uid}/history.json"""
    p = _s(resolve_path(REALITY, "history"))
    assert f"runtime/memory/{CHAR}/{UID}/history.json" in p


def test_history_same_root_as_mid_term(sandbox):
    """history and mid_term must share the same parent directory (user_memory_root)."""
    hist = resolve_path(REALITY, "history")
    mt = resolve_path(REALITY, "mid_term")
    assert hist.parent == mt.parent


# ---------------------------------------------------------------------------
# P1-2H: event_log path layout consistency
# event_log must live under runtime/memory/{char_id}/{uid}/event_log
# ---------------------------------------------------------------------------

def test_event_log_exact_layout(sandbox):
    """event_log → runtime/memory/{char_id}/{uid}/event_log (directory, not a file)"""
    p = _s(resolve_path(REALITY, "event_log"))
    assert f"runtime/memory/{CHAR}/{UID}/event_log" in p


def test_event_log_same_root_as_mid_term(sandbox):
    """event_log parent must equal user_memory_root (same as mid_term's parent)."""
    el = resolve_path(REALITY, "event_log")
    mt = resolve_path(REALITY, "mid_term")
    assert el.parent == mt.parent
