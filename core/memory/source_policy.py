"""Central visibility policy for evidence-ledger sources.

The ledger retains isolated evidence for forensic administration, but external
web results, Dream afterglow and coplay material must never re-enter an owner
Reality prompt through the event read tools or shadow comparison path.
"""
from __future__ import annotations

import threading
from typing import Any

ISOLATED_SOURCES = frozenset({"web", "dream_echo", "coplay", "legacy_unknown"})

_LOCK = threading.Lock()
_OBSERVABILITY: dict[str, int] = {
    "policy_filtered_query_count": 0,
    # Compatibility field. It no longer estimates rejected rows.
    "rejected": 0,
}


def normalize(source: object) -> str:
    return str(source or "").strip()


def is_isolated(source: object) -> bool:
    return normalize(source) in ISOLATED_SOURCES


def partition_key(source: object) -> str:
    """Use the source itself as the deterministic temporal partition key."""
    return normalize(source)


def role_source_allowed(source: object) -> bool:
    return not is_isolated(source)


def sql_predicate(column: str = "source", *, include_isolated: bool = False) -> tuple[str, tuple[Any, ...]]:
    """Return a SQL fragment for default Reality recall visibility."""
    if include_isolated:
        return "", ()
    placeholders = ", ".join("?" for _ in ISOLATED_SOURCES)
    return f" AND COALESCE({column}, '') NOT IN ({placeholders})", tuple(sorted(ISOLATED_SOURCES))


def record_filtered_query() -> None:
    with _LOCK:
        _OBSERVABILITY["policy_filtered_query_count"] += 1


def observability_snapshot() -> dict[str, int]:
    with _LOCK:
        return dict(_OBSERVABILITY)


def _reset_observability_for_tests() -> None:
    with _LOCK:
        _OBSERVABILITY["policy_filtered_query_count"] = 0
        _OBSERVABILITY["rejected"] = 0
