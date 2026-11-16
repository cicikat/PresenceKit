"""Identity-preserving registry overrides with pytest's automatic teardown."""

from core.tool_runtime.registry import _TOOL_REGISTRY, _BUILTIN_ADAPTERS


def patch_registry(monkeypatch, entries):
    replacements = {name: dict(spec) for name, spec in entries.items()}
    for name, spec in replacements.items():
        if name in _BUILTIN_ADAPTERS:
            spec.setdefault("invoke", _BUILTIN_ADAPTERS[name])
    for name in list(_TOOL_REGISTRY):
        monkeypatch.delitem(_TOOL_REGISTRY, name)
    for name, spec in replacements.items():
        monkeypatch.setitem(_TOOL_REGISTRY, name, spec)
