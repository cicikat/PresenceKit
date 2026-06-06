"""
tests/test_memory_path_resolver_guard.py
========================================
P1-2K: resolver guard tests — artifact allowlist + domain enforcement.

Covers:
  - Allowlist completeness: all artifacts in correct frozensets
  - MIGRATED_ARTIFACTS excludes LEGACY_ARTIFACTS
  - Domain enforcement: every artifact category rejects wrong-domain scopes
  - Character-global paths: char_id present, uid absent
  - Reality-user paths: both char_id and uid present
  - Unknown artifacts raise in all scopes
  - Legacy character_growth resolves but is not in MIGRATED_ARTIFACTS
"""

import pytest

from core.memory.scope import MemoryScope
from core.memory.path_resolver import (
    resolve_path,
    REALITY_USER_ARTIFACTS,
    REALITY_CHARACTER_ARTIFACTS,
    GLOBAL_USER_ARTIFACTS,
    DREAM_ARTIFACTS,
    LEGACY_ARTIFACTS,
    MIGRATED_ARTIFACTS,
    _ARTIFACT_DOMAIN,
)


UID  = "u_guard"
CHAR = "cguard"
WORLD = "wguard"

REALITY = MemoryScope.reality_scope(UID, CHAR)
GLOBAL  = MemoryScope.global_scope(UID)
DREAM   = MemoryScope.dream_scope(UID, CHAR, WORLD)


def _s(path) -> str:
    return str(path).replace("\\", "/")


# ---------------------------------------------------------------------------
# 1. Allowlist structure & completeness
# ---------------------------------------------------------------------------

def test_migrated_artifacts_equals_union_of_non_legacy_sets():
    expected = (
        REALITY_USER_ARTIFACTS
        | REALITY_CHARACTER_ARTIFACTS
        | GLOBAL_USER_ARTIFACTS
        | DREAM_ARTIFACTS
    )
    assert MIGRATED_ARTIFACTS == expected


def test_all_allowlist_categories_are_disjoint():
    """No artifact appears in more than one category."""
    categories = [
        REALITY_USER_ARTIFACTS,
        REALITY_CHARACTER_ARTIFACTS,
        GLOBAL_USER_ARTIFACTS,
        DREAM_ARTIFACTS,
        LEGACY_ARTIFACTS,
    ]
    seen: set[str] = set()
    for cat in categories:
        overlap = seen & cat
        assert not overlap, f"Artifacts appear in multiple categories: {overlap}"
        seen |= cat


def test_domain_table_covers_all_known_artifacts():
    """_ARTIFACT_DOMAIN must cover every artifact in MIGRATED + LEGACY."""
    known = MIGRATED_ARTIFACTS | LEGACY_ARTIFACTS
    for art in known:
        assert art in _ARTIFACT_DOMAIN, f"Artifact {art!r} missing from _ARTIFACT_DOMAIN"


def test_domain_table_has_no_extra_artifacts():
    """_ARTIFACT_DOMAIN must not contain artifacts outside MIGRATED | LEGACY."""
    known = MIGRATED_ARTIFACTS | LEGACY_ARTIFACTS
    for art in _ARTIFACT_DOMAIN:
        assert art in known, f"_ARTIFACT_DOMAIN has unregistered artifact {art!r}"


def test_reality_user_artifacts_all_map_to_reality_domain():
    for art in REALITY_USER_ARTIFACTS:
        assert _ARTIFACT_DOMAIN[art] == "reality", f"{art!r} should map to 'reality'"


def test_reality_character_artifacts_all_map_to_reality_domain():
    for art in REALITY_CHARACTER_ARTIFACTS:
        assert _ARTIFACT_DOMAIN[art] == "reality", f"{art!r} should map to 'reality'"


def test_global_user_artifacts_all_map_to_global_domain():
    for art in GLOBAL_USER_ARTIFACTS:
        assert _ARTIFACT_DOMAIN[art] == "global", f"{art!r} should map to 'global'"


def test_dream_artifacts_all_map_to_dream_domain():
    for art in DREAM_ARTIFACTS:
        assert _ARTIFACT_DOMAIN[art] == "dream", f"{art!r} should map to 'dream'"


def test_legacy_artifacts_all_map_to_reality_domain():
    for art in LEGACY_ARTIFACTS:
        assert _ARTIFACT_DOMAIN[art] == "reality", f"Legacy {art!r} should map to 'reality'"


# ---------------------------------------------------------------------------
# 2. character_growth is legacy, not migrated
# ---------------------------------------------------------------------------

def test_character_growth_not_in_migrated_artifacts():
    assert "character_growth" not in MIGRATED_ARTIFACTS


def test_character_growth_in_legacy_artifacts():
    assert "character_growth" in LEGACY_ARTIFACTS


def test_character_growth_resolve_still_works(sandbox):
    """Legacy artifact still resolves without raising (backward compat)."""
    p = _s(resolve_path(REALITY, "character_growth"))
    assert CHAR in p
    assert UID in p


# ---------------------------------------------------------------------------
# 3. Domain enforcement — reality-user artifacts reject non-reality scopes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("artifact", sorted(REALITY_USER_ARTIFACTS))
def test_reality_user_artifact_rejects_global_scope(sandbox, artifact):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(GLOBAL, artifact)


@pytest.mark.parametrize("artifact", sorted(REALITY_USER_ARTIFACTS))
def test_reality_user_artifact_rejects_dream_scope(sandbox, artifact):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(DREAM, artifact)


# ---------------------------------------------------------------------------
# 4. Domain enforcement — character-global artifacts reject non-reality scopes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("artifact", sorted(REALITY_CHARACTER_ARTIFACTS))
def test_character_global_artifact_rejects_global_scope(sandbox, artifact):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(GLOBAL, artifact)


@pytest.mark.parametrize("artifact", sorted(REALITY_CHARACTER_ARTIFACTS))
def test_character_global_artifact_rejects_dream_scope(sandbox, artifact):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(DREAM, artifact)


# ---------------------------------------------------------------------------
# 5. Domain enforcement — global artifacts reject non-global scopes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("artifact", sorted(GLOBAL_USER_ARTIFACTS))
def test_global_artifact_rejects_reality_scope(sandbox, artifact):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(REALITY, artifact)


@pytest.mark.parametrize("artifact", sorted(GLOBAL_USER_ARTIFACTS))
def test_global_artifact_rejects_dream_scope(sandbox, artifact):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(DREAM, artifact)


# ---------------------------------------------------------------------------
# 6. Domain enforcement — dream artifacts reject non-dream scopes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("artifact", sorted(DREAM_ARTIFACTS))
def test_dream_artifact_rejects_reality_scope(sandbox, artifact):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(REALITY, artifact)


@pytest.mark.parametrize("artifact", sorted(DREAM_ARTIFACTS))
def test_dream_artifact_rejects_global_scope(sandbox, artifact):
    with pytest.raises(ValueError, match="domain"):
        resolve_path(GLOBAL, artifact)


# ---------------------------------------------------------------------------
# 7. Path layout: character-global paths exclude uid
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("artifact", sorted(REALITY_CHARACTER_ARTIFACTS))
def test_character_global_path_excludes_uid(sandbox, artifact):
    p = _s(resolve_path(REALITY, artifact))
    assert CHAR in p, f"{artifact}: char_id missing from path: {p}"
    assert UID not in p, f"{artifact}: uid unexpectedly present in path: {p}"


# ---------------------------------------------------------------------------
# 8. Path layout: reality-user paths include both char_id and uid
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("artifact", sorted(REALITY_USER_ARTIFACTS))
def test_reality_user_path_includes_char_and_uid(sandbox, artifact):
    p = _s(resolve_path(REALITY, artifact))
    assert CHAR in p, f"{artifact}: char_id missing from path: {p}"
    assert UID in p, f"{artifact}: uid missing from path: {p}"


# ---------------------------------------------------------------------------
# 9. Unknown artifacts raise for every scope
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scope", [REALITY, GLOBAL, DREAM])
def test_unknown_artifact_raises_for_any_scope(sandbox, scope):
    with pytest.raises(ValueError, match="unknown artifact"):
        resolve_path(scope, "totally_unknown_artifact_xyz")


# ---------------------------------------------------------------------------
# 10. AssertionError guard: _ARTIFACT_DOMAIN and if/elif chain stay in sync
#     If a new artifact is added to allowlists but not handled, an
#     AssertionError propagates — this test detects phantom entries.
# ---------------------------------------------------------------------------

def test_all_migrated_artifacts_resolve_without_assertion_error(sandbox):
    """Every artifact in MIGRATED_ARTIFACTS must be handled by resolve_path."""
    for art in sorted(REALITY_USER_ARTIFACTS):
        resolve_path(REALITY, art)            # must not raise AssertionError

    for art in sorted(REALITY_CHARACTER_ARTIFACTS):
        resolve_path(REALITY, art)

    for art in sorted(GLOBAL_USER_ARTIFACTS):
        resolve_path(GLOBAL, art)

    for art in sorted(DREAM_ARTIFACTS):
        resolve_path(DREAM, art)
