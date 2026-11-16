"""Domain additions bind at registration/transport boundaries, without facades."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
from fastapi import APIRouter, Depends, FastAPI


async def test_new_domain_tool_from_synthetic_model_stream(monkeypatch):
    """A new streamed tool name uses only its registered schema and adapter."""
    from core import llm_protocol, tool_dispatcher
    from core.model_registry import ModelClient
    from core.tool_runtime.registry import temporary_registry

    calls = []

    async def observe(value, *, user_id, char_id):
        calls.append((value, user_id, char_id))
        return "synthetic observation recorded"

    async def invoke(call):
        return await call.func(**call.tool_args, user_id=call.user_id, char_id=call.char_id)

    class Stream:
        async def __aiter__(self):
            for name, arguments, finish in [
                ("synthetic_observation", '{"value":', None),
                (None, '"fixture"}', "tool_calls"),
            ]:
                yield NS(choices=[NS(index=0, delta=NS(content=None, tool_calls=[NS(
                    index=0, id="fixture-call" if name else None,
                    function=NS(name=name, arguments=arguments),
                )]), finish_reason=finish)])

        close = AsyncMock()

    stream = Stream()
    request = AsyncMock(return_value=stream)
    client = ModelClient(
        name="fixture", provider_kind="openai", model="fixture",
        tool_call_mode="function_calling", prompt_style="narrative", params={},
        client=NS(chat=NS(completions=NS(create=request))), force_stream=True,
    )
    monkeypatch.setattr("core.llm_reasoning_store.Capture.save", AsyncMock())
    with temporary_registry({"synthetic_observation": {
        "func": observe, "invoke": invoke, "category": "synthetic_domain",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                       "required": ["value"], "additionalProperties": False},
    }}):
        schemas = tool_dispatcher.get_tools_schema(categories=["synthetic_domain"])
        response = await llm_protocol.create(client, [], tools=schemas, gen_kwargs={})
        call, = response.tool_calls
        result = await tool_dispatcher.execute_structured(
            call.name, call.arguments, "fixture_owner", "fixture_owner", False, None,
            origin="assistant_loop", char_id="fixture_character",
        )
    assert result.status == "tool_executed"
    assert "synthetic observation recorded" in result.result
    assert calls == [("fixture", "fixture_owner", "fixture_character")]
    stream.close.assert_awaited_once()


async def test_new_dream_child_route_needs_only_common_contract(monkeypatch):
    """A mounted child keeps auth/ID errors without calling dream composition."""
    from admin import auth
    from admin.routers import dream_common

    monkeypatch.setattr(auth, "resolve_token", lambda token: auth.TokenInfo(
        label="fixture", scopes=frozenset({"activity"}),
    ) if token == "fixture-activity" else None)
    router = APIRouter()

    @router.get("/synthetic/dream")
    async def project(dream_id: str, _auth=Depends(auth.require_scopes("activity"))):
        return {"dream_id": dream_common._validated_dream_id(dream_id)}

    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Authorization": "Bearer fixture-activity"}
        assert (await client.get("/synthetic/dream", params={"dream_id": "fixture"})).status_code == 401
        accepted = await client.get("/synthetic/dream", params={"dream_id": "fixture-dream"}, headers=headers)
        rejected = await client.get("/synthetic/dream", params={"dream_id": "invalid.value"}, headers=headers)
    assert accepted.json() == {"dream_id": "fixture-dream"}
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "dream_id 不合法"
