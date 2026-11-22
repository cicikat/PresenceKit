"""Scoped vector projection reconciliation shared by the janitor and read-only checks."""

import re

from core.memory.scope import MemoryScope, require_character_id


def plan_missing_vectors(
    scope: MemoryScope, present: set[tuple[str, str]], *, include_legacy: bool = True,
) -> tuple[int, list[tuple[tuple[str, str], tuple[float, str]]]]:
    """Return canonical count and missing projections without making repairs."""
    from core.memory.episodic_memory import _load_memories
    from core.memory.event_log import _TURN_ID_RE, get_recent_days
    from core.memory.event_log_source import block_is_recallable, split_blocks
    from core.memory.source_policy import is_isolated

    if scope.domain != "reality":
        raise ValueError("vector reconciliation requires a Reality scope")
    char_id = require_character_id(scope.character_id)
    documents = {}
    for ep in _load_memories(scope.uid, char_id=char_id):
        sources = ep.get("source") or []
        sources = sources if isinstance(sources, list) else [sources]
        text = (ep.get("narrative_summary") or ep.get("summary") or " ".join(ep.get("raw_facts") or [])).strip()
        if ep.get("id") and text and not any(is_isolated(source) for source in sources):
            documents[("episodic", str(ep["id"]))] = (float(ep.get("occurred_at") or ep.get("timestamp") or 0), text)
    grouped = {}
    for block in split_blocks(get_recent_days(scope.uid, days=30, char_id=char_id, include_legacy=include_legacy)):
        if not block_is_recallable(block):
            continue
        ids = {value for line in block for value in _TURN_ID_RE.findall(line)}
        if len(ids) == 1:
            grouped.setdefault(next(iter(ids)), []).extend(block)
    for turn_id, lines in grouped.items():
        # Preserve the event-time used by the source. Legacy IDs lacking a
        # timestamp stay zero rather than becoming fresh evidence on recovery.
        match = re.search(r"_(\d{13})(?:_|$)", turn_id)
        documents[("event_log", turn_id)] = (int(match[1]) / 1000 if match else 0, "\n".join(lines))
    missing = [(key, value) for key, value in documents.items() if key not in present]
    return len(documents), missing


def inspect_vector_projections(scope: MemoryScope) -> dict[str, int]:
    """Return counts only. The index and source stores are opened for reading."""
    from core.memory.vector_store import read_projection_keys

    if scope.domain != "reality":
        raise ValueError("vector reconciliation requires a Reality scope")
    present = read_projection_keys(scope.uid, require_character_id(scope.character_id))
    canonical, missing = plan_missing_vectors(scope, present, include_legacy=False)
    return {"canonical": canonical, "indexed": len(present), "missing": len(missing)}
