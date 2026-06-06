"""
core/memory/path_resolver.py
============================
P1-2A: standalone resolver — MemoryScope + artifact key → Path.
P1-2K: resolver guard — artifact allowlist + domain rules.

Not wired to pipeline / slow_queue / Dream / admin / store.
Resolver only returns paths; it never creates directories.
"""
from __future__ import annotations

from pathlib import Path

from core.memory.scope import MemoryScope
from core.sandbox import get_paths, safe_user_id

# ── Artifact allowlists ───────────────────────────────────────────────────────

# Reality-scoped, per-user: path uses both char_id and uid.
REALITY_USER_ARTIFACTS: frozenset[str] = frozenset({
    "history",
    "event_log",
    "mid_term",
    "episodic",
    "memory_index",
    "fixation_state",
    "profile",
    "identity",
    "hidden_state",
    "afterglow_residue",
    "impression",        # dream-origin but reality-scoped
})

# Reality-scoped, character-global: path uses char_id only, uid is NOT part of path.
REALITY_CHARACTER_ARTIFACTS: frozenset[str] = frozenset({
    "mood_state",
    "trait_state",
    "author_note_state",
    "observations",
    "garden_plants",
    "garden_storage",
})

# Global-scoped: path uses uid only, no char_id in scope.
GLOBAL_USER_ARTIFACTS: frozenset[str] = frozenset({
    "user_facts",
})

# Dream-scoped: path uses char_id, uid, and world_id.
DREAM_ARTIFACTS: frozenset[str] = frozenset({
    "dream_state",
})

# Legacy / dead-registered artifacts: not migrated to active production use.
# Kept so resolver stays backward-compatible; callers should not write new paths via these.
# character_growth: legacy tool — paths still resolve for audit/compat, never migrated.
LEGACY_ARTIFACTS: frozenset[str] = frozenset({
    "character_growth",
})

# All actively migrated artifacts (excludes LEGACY_ARTIFACTS).
MIGRATED_ARTIFACTS: frozenset[str] = (
    REALITY_USER_ARTIFACTS
    | REALITY_CHARACTER_ARTIFACTS
    | GLOBAL_USER_ARTIFACTS
    | DREAM_ARTIFACTS
)

# ── artifact → required domain ───────────────────────────────────────────────
_ARTIFACT_DOMAIN: dict[str, str] = {
    **{a: "reality" for a in REALITY_USER_ARTIFACTS},
    **{a: "reality" for a in REALITY_CHARACTER_ARTIFACTS},
    **{a: "reality" for a in LEGACY_ARTIFACTS},
    **{a: "global"  for a in GLOBAL_USER_ARTIFACTS},
    **{a: "dream"   for a in DREAM_ARTIFACTS},
}


def resolve_path(scope: MemoryScope, artifact: str) -> Path:
    """Resolve a MemoryScope + artifact key to a concrete filesystem Path.

    Raises ValueError if:
      - artifact is not in the known artifact table
      - scope.domain does not match the artifact's required domain

    Never creates directories.  Caller is responsible for mkdir when writing.
    """
    required_domain = _ARTIFACT_DOMAIN.get(artifact)
    if required_domain is None:
        raise ValueError(f"unknown artifact: {artifact!r}")
    if scope.domain != required_domain:
        raise ValueError(
            f"artifact {artifact!r} requires domain={required_domain!r}, "
            f"got domain={scope.domain!r}"
        )

    paths = get_paths()
    uid = safe_user_id(scope.uid)
    # character_id is guaranteed non-None for reality/dream by MemoryScope.__post_init__
    char_id: str = scope.character_id  # type: ignore[assignment]

    # ── reality-scoped: (char_id, uid) ──────────────────────────────────────

    if artifact == "history":
        return paths.user_memory_root(uid, char_id=char_id) / "history.json"

    if artifact == "event_log":
        return paths.user_memory_root(uid, char_id=char_id) / "event_log"

    if artifact == "mid_term":
        return paths.user_memory_root(uid, char_id=char_id) / "mid_term.json"

    if artifact == "episodic":
        return paths.user_memory_root(uid, char_id=char_id) / "episodic.json"

    if artifact == "memory_index":
        return paths.user_memory_root(uid, char_id=char_id) / "memory_index.json"

    if artifact == "fixation_state":
        return paths.user_memory_root(uid, char_id=char_id) / "fixation_state.json"

    if artifact == "profile":
        return paths.user_memory_root(uid, char_id=char_id) / "profile.json"

    if artifact == "identity":
        return paths.user_memory_root(uid, char_id=char_id) / "identity.yaml"

    if artifact == "hidden_state":
        return paths.user_memory_root(uid, char_id=char_id) / "hidden_state.json"

    if artifact == "afterglow_residue":
        return paths.user_memory_root(uid, char_id=char_id) / "afterglow_residue.json"

    if artifact == "character_growth":
        # legacy/dead registered tool — callers should not write new paths via this key.
        # Kept for backward-compat only; not in MIGRATED_ARTIFACTS.
        return paths.character_growth(char_id=char_id) / f"{uid}.md"

    if artifact == "impression":
        # Dream-origin but reality-scoped: per-user file under dreams_impressions_dir.
        return paths.dreams_impressions_dir(char_id=char_id) / f"{uid}.json"

    # ── character-global scoped: (char_id) only — uid is not part of path ───

    if artifact == "mood_state":
        return paths.mood_state(char_id=char_id)

    if artifact == "trait_state":
        return paths.trait_state(char_id=char_id)

    if artifact == "author_note_state":
        return paths.author_note_state(char_id=char_id)

    if artifact == "observations":
        return paths.observations(char_id=char_id)

    if artifact == "garden_plants":
        return paths.garden(char_id=char_id) / "plants.json"

    if artifact == "garden_storage":
        return paths.garden(char_id=char_id) / "storage.json"

    # ── global-scoped: (uid) only — char_id is None in scope ────────────────

    if artifact == "user_facts":
        # Planned layout: {data_base}/global_facts/{uid}.json
        return paths._p("global_facts", uid + ".json")

    # ── dream-scoped: (char_id, uid, world_id) ───────────────────────────────

    if artifact == "dream_state":
        # Layout: …/dreams/{char_id}/state/{uid}/{world_id}/dream_state.json
        base = paths.dream_state_path(uid, char_id=char_id)
        return base.parent / scope.world_id / base.name  # type: ignore[arg-type]

    raise AssertionError(f"unhandled artifact after domain check: {artifact!r}")
