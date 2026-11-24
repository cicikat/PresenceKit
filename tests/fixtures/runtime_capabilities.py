"""Explicit composition for tests which install a legacy runtime double.

Production HTTP dependencies are bound by main; these tests bypass startup.
Keep this adapter in tests and pass it explicitly at the tested entry point.
"""
from core.character_session import CharacterContextSnapshot


def read_registered_character():
    from core.pipeline_registry import get
    instance = get()
    if instance is None:
        return None
    return CharacterContextSnapshot(
        getattr(instance, "character", None),
        getattr(instance, "lore_engine", None),
        getattr(instance, "_active_character_id", ""),
    )


def bind_character_http(app):
    from admin.character_dependencies import require_character_reader
    app.dependency_overrides[require_character_reader] = lambda: read_registered_character
