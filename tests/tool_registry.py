"""Identity-preserving registry overrides with pytest's automatic teardown."""

from core.tool_runtime.registry import _TOOL_REGISTRY, prepare_overrides


def patch_registry(monkeypatch, entries):
    replacements = prepare_overrides(entries, inherit_builtin_adapters=True)
    for name in list(_TOOL_REGISTRY):
        monkeypatch.delitem(_TOOL_REGISTRY, name)
    for name, spec in replacements.items():
        monkeypatch.setitem(_TOOL_REGISTRY, name, spec)
