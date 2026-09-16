"""Resolve the model-visible tool surface for each tool path.

The exposure policy is deliberately independent from the transport channel.
QQ, desktop, and mobile share one Path A surface; Path C has its own surface.
Both surfaces can be narrowed by categories, explicit tool names, and excludes.
"""
from __future__ import annotations

from dataclasses import dataclass


_DEFAULTS = {
    "path_a": {"categories": ["info", "desktop"], "tools": None, "exclude_tools": []},
    "path_c": {"categories": ["info", "desktop", "memory", "artifacts"], "tools": None, "exclude_tools": []},
}


@dataclass(frozen=True)
class ToolExposure:
    path: str
    categories: tuple[str, ...]
    tools: frozenset[str] | None
    exclude_tools: frozenset[str]
    source: str
    # ``resolve`` deliberately keeps ordinary chat fail-soft when an authored
    # card cannot be loaded.  Read-only capability diagnostics can still use
    # this bit to fail closed instead of mistaking global defaults for a
    # successfully loaded character policy.
    character_load_failed: bool = False


def _clean_names(value, *, allow_empty: bool = True) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return [] if allow_empty else None
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def resolve(path: str, *, char_id: str | None = None) -> ToolExposure:
    """Resolve one path's effective exposure without inspecting the channel."""
    if path not in _DEFAULTS:
        raise ValueError(f"unknown tool exposure path: {path}")
    from core.config_loader import get_config

    cfg = get_config() or {}
    root = cfg.get("tool_exposure") if isinstance(cfg.get("tool_exposure"), dict) else {}
    block = root.get(path) if isinstance(root.get(path), dict) else {}

    # Path C keeps its established tool_loop.categories/exclude_tools fallback.
    if path == "path_c":
        legacy = cfg.get("tool_loop") if isinstance(cfg.get("tool_loop"), dict) else {}
        base_categories = legacy.get("categories", _DEFAULTS[path]["categories"])
        base_excludes = legacy.get("exclude_tools", [])
    else:
        base_categories = _DEFAULTS[path]["categories"]
        base_excludes = []

    categories = block.get("categories", base_categories)
    if not isinstance(categories, list):
        categories = list(_DEFAULTS[path]["categories"])
    tools = _clean_names(block.get("tools"))
    excludes = _clean_names(block.get("exclude_tools")) or []
    excludes.extend(_clean_names(base_excludes) or [])
    source = f"config.tool_exposure.{path}" if block else (
        "config.tool_loop" if path == "path_c" and isinstance(cfg.get("tool_loop"), dict) else "default"
    )
    character_load_failed = False

    if char_id:
        try:
            from core.character_loader import load

            ext = load(char_id).presence_ext or {}
            path_key = "tool_categories_path_a" if path == "path_a" else "tool_categories_path_c"
            char_categories = ext.get(path_key)
            if path == "path_c" and char_categories is None:
                # Existing authored cards use this field for Path C.
                char_categories = ext.get("tool_categories")
            if isinstance(char_categories, list):
                categories = char_categories
                source = f"presence_ext.{path_key}"
                if path == "path_c" and path_key not in ext and "tool_categories" in ext:
                    source = "presence_ext.tool_categories"
            char_tools = ext.get("tool_tools_path_a" if path == "path_a" else "tool_tools_path_c")
            if isinstance(char_tools, list):
                tools = _clean_names(char_tools)
                source = f"presence_ext.tool_tools_{path[-1]}"
            char_excludes = ext.get("tool_exclude_path_a" if path == "path_a" else "tool_exclude_path_c")
            if isinstance(char_excludes, list):
                excludes.extend(_clean_names(char_excludes) or [])
        except Exception:
            # Character loading must not make ordinary chat unavailable.
            character_load_failed = True

    return ToolExposure(
        path=path,
        categories=tuple(dict.fromkeys(str(item).strip() for item in categories if str(item).strip())),
        tools=None if tools is None else frozenset(tools),
        exclude_tools=frozenset(excludes),
        source=source,
        character_load_failed=character_load_failed,
    )


def filter_schemas(schemas: list[dict], exposure: ToolExposure) -> list[dict]:
    """Apply explicit names/excludes after category and security filtering."""
    result = []
    for schema in schemas:
        function = schema.get("function") or schema
        name = str(function.get("name") or "")
        if not name:
            continue
        if exposure.tools is not None and name not in exposure.tools:
            continue
        if name in exposure.exclude_tools:
            continue
        result.append(schema)
    return result
