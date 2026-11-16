"""Trusted active-character fallback for legacy direct tool callers.

Execution always binds the explicit per-invocation character instead.
"""

def _active_char_id() -> str:
    try:
        from core.pipeline_registry import get as _get_pipeline
        from core.data_paths import DEFAULT_CHAR_ID
        return str(getattr(_get_pipeline(), "_active_character_id", None) or DEFAULT_CHAR_ID)
    except Exception:
        from core.data_paths import DEFAULT_CHAR_ID
        return DEFAULT_CHAR_ID
