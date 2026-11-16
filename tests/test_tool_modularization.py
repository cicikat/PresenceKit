"""Registry lifecycle and explicit, identity-preserving builtin overrides."""

from tests.tool_registry import patch_registry

import pytest


@pytest.mark.asyncio
async def test_replaced_builtin_spec_preserves_principal_and_scope_guard(monkeypatch):
    from core import tool_dispatcher as dispatcher

    calls = []

    async def reader(document_id, *, user_id, char_id):
        calls.append((document_id, user_id, char_id))
        return "fixture document"

    # The explicit override resolves the builtin adapter before invocation.
    patch_registry(monkeypatch, {
        "read_document": {
            "func": reader, "category": "memory", "dangerous": False,
            "parameters": {"type": "object", "properties": {
                "document_id": {"type": "string"},
            }, "required": ["document_id"]},
        },
    })
    monkeypatch.setattr('core.tool_runtime.policy._is_tool_enabled', lambda _: True)

    async def execute(char_id, arguments):
        return await dispatcher.execute_structured(
            "read_document", arguments, "fixture_owner", "fixture_owner", False, None,
            origin="assistant_loop", char_id=char_id,
        )

    result = await execute("fixture_character", {"document_id": "fixture-document"})
    assert result.status == "tool_executed"
    assert calls == [("fixture-document", "fixture_owner", "fixture_character")]

    invalid_scope = await execute("", {"document_id": "fixture-document"})
    assert invalid_scope.status == "tool_failed"
    forged_principal = await execute("fixture_character", {
        "document_id": "fixture-document", "user_id": "another-owner",
    })
    assert forged_principal.status == "tool_failed"
    assert calls == [("fixture-document", "fixture_owner", "fixture_character")]


@pytest.mark.asyncio
async def test_registry_identity_reload_and_dynamic_execution(monkeypatch):
    """New/removed entries are immediately visible to schema and execution."""
    import importlib
    from core import tool_dispatcher as dispatcher
    from core.tool_runtime import registry

    identity = registry._TOOL_REGISTRY
    original_names = list(identity)
    registry.build_registry(identity)
    importlib.reload(registry)
    assert identity is registry._TOOL_REGISTRY is dispatcher._TOOL_REGISTRY
    assert list(identity) == original_names


    async def inspect_fixture():
        return "fixture inspected"

    name = "mcp__fixture__inspect"
    with registry.temporary_registry({name: {
        "func": inspect_fixture, "category": "mcp", "mcp_server": "fixture", "mcp_tool": "inspect",
        "parameters": {"type": "object", "properties": {}},
    }}):
        assert [s["function"]["name"] for s in dispatcher.get_tools_schema()] == [name]
        result = await dispatcher.execute_structured(
            name, {}, "fixture_owner", "fixture_owner", False, None,
            origin="assistant_loop", char_id="fixture_character",
        )
        assert result.status == "tool_executed", result
        identity.pop(name)
        assert dispatcher.get_tools_schema() == []
        result = await dispatcher.execute_structured(
            name, {}, "fixture_owner", "fixture_owner", False, None,
            origin="assistant_loop", char_id="fixture_character",
        )
        assert result.status == "tool_unknown"
    assert list(identity) == original_names


@pytest.mark.asyncio
async def test_execution_failure_logs_only_content_free_diagnostics(caplog):
    """Exception text and arguments must not expose memory/MCP credentials."""
    from core import tool_dispatcher as dispatcher
    from core.tool_runtime.registry import temporary_registry

    private_argument = "fixture-private-memory-and-credential"
    private_exception = "fixture-private-exception-body"

    async def failing_tool(**arguments):
        raise RuntimeError(private_exception)

    with temporary_registry({"weather": {
        "func": failing_tool, "category": "info",
        "parameters": {"type": "object", "properties": {}},
    }}):
        result = await dispatcher.execute_structured(
            "weather", {"private": private_argument}, "fixture_owner", "fixture_owner", False, None,
            origin="assistant_loop", char_id="fixture_character",
        )
    assert result.status == "tool_failed"
    assert private_argument not in caplog.text
    assert private_exception not in caplog.text
    assert "error_type=RuntimeError" in caplog.text
