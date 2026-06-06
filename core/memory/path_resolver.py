"""
core/memory/path_resolver.py
============================
P1-2A: standalone resolver — MemoryScope + artifact key → Path.

Not wired to pipeline / slow_queue / Dream / admin / store.
Resolver only returns paths; it never creates directories.
"""
from __future__ import annotations

from pathlib import Path

from core.memory.scope import MemoryScope
from core.sandbox import get_paths, safe_user_id

# ── artifact → required domain ───────────────────────────────────────────────
_ARTIFACT_DOMAIN: dict[str, str] = {
    # reality-scoped: (char_id, uid)
    "history":           "reality",
    "event_log":         "reality",
    "mid_term":          "reality",
    "episodic":          "reality",
    "memory_index":      "reality",
    "fixation_state":    "reality",
    "profile":           "reality",
    "identity":          "reality",
    "hidden_state":      "reality",
    "afterglow_residue": "reality",
    "character_growth":  "reality",
    "impression":        "reality",   # dream-origin but reality-scoped
    # character-global scoped: (char_id) only, uid ignored in path — still domain=reality
    "mood_state":        "reality",
    "trait_state":       "reality",
    "author_note_state": "reality",
    "observations":      "reality",
    "garden_plants":     "reality",
    "garden_storage":    "reality",
    # global-scoped: (uid) only
    "user_facts":        "global",
    # dream-scoped: (char_id, uid, world_id)
    "dream_state":       "dream",
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
        return paths.fixation_state_dir(char_id=char_id) / f"{uid}.json"

    if artifact == "profile":
        return paths.user_memory_root(uid, char_id=char_id) / "profile.json"

    if artifact == "identity":
        return paths.user_memory_root(uid, char_id=char_id) / "identity.yaml"

    if artifact == "hidden_state":
        return paths.user_memory_root(uid, char_id=char_id) / "hidden_state.json"

    if artifact == "afterglow_residue":
        return paths.user_memory_root(uid, char_id=char_id) / "afterglow_residue.json"

    if artifact == "character_growth":
        # Temporary central point — legacy callers write {char_name}_{uid}.md filenames;
        # future P1-2B alignment will use {uid}.md under per-char growth dir.
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
        # Temporary central point — no existing DataPaths helper for global user facts.
        # Planned layout: {data_base}/global_facts/{uid}.json
        return paths._p("global_facts", uid + ".json")

    # ── dream-scoped: (char_id, uid, world_id) ───────────────────────────────

    if artifact == "dream_state":
        # Extend current dream_state_path with a world_id layer.
        # Current layout: …/dreams/{char_id}/state/{uid}/dream_state.json
        # World-scoped:   …/dreams/{char_id}/state/{uid}/{world_id}/dream_state.json
        base = paths.dream_state_path(uid, char_id=char_id)
        return base.parent / scope.world_id / base.name  # type: ignore[arg-type]

    raise AssertionError(f"unhandled artifact after domain check: {artifact!r}")
