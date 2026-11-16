"""Single mutable registry identity, independent of builtin composition.

Domain policy may read this object while registry composes builtin definitions;
it never needs to import the composer or the public dispatcher.
"""

if "_TOOL_REGISTRY" not in globals():
    _TOOL_REGISTRY: dict[str, dict] = {}
