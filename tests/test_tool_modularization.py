"""Regression: replacing a legacy builtin spec must retain its scoped adapter."""

import pytest


@pytest.mark.asyncio
async def test_replaced_builtin_spec_preserves_principal_and_scope_guard(monkeypatch):
    from core import tool_dispatcher as dispatcher

    calls = []

    async def reader(document_id, *, user_id, char_id):
        calls.append((document_id, user_id, char_id))
        return "fixture document"

    # Historical local extensions can replace the complete spec, without the
    # new invoke metadata. This remains an explicit supported injection seam.
    monkeypatch.setattr(dispatcher, "_TOOL_REGISTRY", {
        "read_document": {
            "func": reader, "category": "memory", "dangerous": False,
            "parameters": {"type": "object", "properties": {
                "document_id": {"type": "string"},
            }, "required": ["document_id"]},
        },
    })
    monkeypatch.setattr(dispatcher, "_is_tool_enabled", lambda _: True)

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
