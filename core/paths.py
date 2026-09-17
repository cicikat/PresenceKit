# Experimental unused planning namespace. Not a production loader.
# Runtime paths stay in core.sandbox.get_paths() / core.data_paths.DataPaths.
"""Experimental future taxonomy helpers; not wired into production.

This module is an explicit experimental namespace for a staged data/ migration.
Nothing in core/, admin/, or channels/ currently imports it. It does not replace
core.sandbox.get_paths() and must not be wired into existing loaders. Deletion
or a real migration needs a separate authorized ticket; do not treat this file
as a silent leftover or as the current path authority.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_ROOT = PROJECT_ROOT / "data"
_CHARACTERS_ROOT = PROJECT_ROOT / "characters"
_PERSONA_ID_RE = re.compile(r"^p[0-9]{3,}$")


def _under(root: Path, *parts: str | Path) -> Path:
    """Join path parts under root without creating directories."""
    clean_parts: list[Path] = []
    for part in parts:
        path = Path(part)
        if path.is_absolute() or path.anchor:
            raise ValueError(f"unsafe path part: {part!r}")
        if any(segment == ".." for segment in path.parts):
            raise ValueError(f"unsafe path part: {part!r}")
        clean_parts.append(path)

    target = root.joinpath(*clean_parts)
    root_resolved = root.resolve()
    target_resolved = target.resolve()
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes root: {target}") from exc
    return target


def data_root() -> Path:
    return _DATA_ROOT


def runtime_root() -> Path:
    return _under(data_root(), "runtime")


def memory_root() -> Path:
    return _under(data_root(), "memory")


def generated_root() -> Path:
    return _under(data_root(), "generated")


def state_root() -> Path:
    return _under(data_root(), "state")


def archive_root() -> Path:
    return _under(data_root(), "archive")


def dreams_root() -> Path:
    return _under(data_root(), "dreams")


def config_root() -> Path:
    return _under(data_root(), "config")


def debug_root() -> Path:
    return _under(data_root(), "debug")


def personas_root() -> Path:
    return _under(data_root(), "personas")


def debug_llm_output_root() -> Path:
    return _under(debug_root(), "llm_output")


def runtime_channel_queue_file() -> Path:
    return _under(runtime_root(), "channel_queue.json")


def runtime_mobile_queue_file() -> Path:
    return _under(runtime_root(), "mobile_queue.json")


def runtime_agent_actions_file() -> Path:
    return _under(runtime_root(), "agent_actions.json")


def legacy_data_root() -> Path:
    return data_root()


def legacy_yexuan_inner_root() -> Path:
    return _under(legacy_data_root(), "yexuan_inner")


def legacy_characters_root() -> Path:
    return _CHARACTERS_ROOT


def legacy_character_growth_root() -> Path:
    return _under(legacy_data_root(), "character_growth")


def personas_active_file() -> Path:
    return _under(personas_root(), "active.json")


def personas_registry_file() -> Path:
    return _under(personas_root(), "registry.json")


def validate_persona_id(persona_id: str) -> str:
    """Accept stable ASCII ids such as p001; do not use display names."""
    value = str(persona_id)
    if not _PERSONA_ID_RE.fullmatch(value):
        raise ValueError(f"invalid persona_id: {persona_id!r}")
    return value


def persona_root(persona_id: str) -> Path:
    return _under(personas_root(), validate_persona_id(persona_id))


def persona_profile_root(persona_id: str) -> Path:
    return _under(persona_root(persona_id), "profile")


def persona_inner_state_root(persona_id: str) -> Path:
    return _under(persona_root(persona_id), "inner_state")


def persona_relationship_root(persona_id: str) -> Path:
    return _under(persona_root(persona_id), "relationship")


def persona_growth_root(persona_id: str) -> Path:
    return _under(persona_root(persona_id), "growth")
