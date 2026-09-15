"""Responses API adapter coverage using local fake SDK objects only."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from core.llm_protocol import (
    UpstreamResponseFormatError,
    anthropic_messages_input,
    chat_completions_input,
    chat_completions_tools,
    create,
    error_category_for_exception,
    responses_input,
    stream_text,
)
from core.model_registry import ModelClient


def _message(text: str):
    return SimpleNamespace(
        type="message",
        role="assistant",
        content=[SimpleNamespace(type="output_text", text=text)],
    )


def _function_call(call_id: str = "call_weather", arguments: str = '{"city":"Hangzhou"}'):
    return SimpleNamespace(type="function_call", call_id=call_id, name="get_weather", arguments=arguments)


def _response(*items, status="completed", usage=None):
    return SimpleNamespace(output=list(items), status=status, usage=usage)


def _client(*, response=None, stream_events=None):
    calls = []

    async def create_response(**kwargs):
        calls.append(kwargs)
        if stream_events is not None:
            async def stream():
                for event in stream_events:
                    yield event
            return stream()
        return response

    return SimpleNamespace(
        responses=SimpleNamespace(create=create_response),
        calls=calls,
    )


def _responses_mc(client):
    return ModelClient(
        name="responses-test",
        provider_kind="openai",
        model="test-model",
        tool_call_mode="function_calling",
        prompt_style="narrative",
        params={"temperature": 0.2, "max_tokens": 64, "presence_penalty": 1.0},
        client=client,
        api_protocol="responses",
    )


def _anthropic_mc(client, *, auth_mode="bearer"):
    return ModelClient(
        name="anthropic-test",
        provider_kind="anthropic_compat",
        model="claude-test",
        tool_call_mode="function_calling",
        prompt_style="xml",
        params={},
        client=client,
        api_protocol="anthropic_messages",
        base_url="https://relay.example",
        api_key="test-secret",
        anthropic_auth_mode=auth_mode,
    )


@pytest.mark.asyncio
async def test_responses_text_is_normalized_and_uses_responses_wire_shape():
    client = _client(response=_response(_message("hello"), usage=SimpleNamespace(input_tokens=3, output_tokens=2)))
    result = await create(
        _responses_mc(client),
        [{"role": "system", "content": "be concise"}, {"role": "user", "content": "hi"}],
        tools=None,
        tool_choice=None,
        gen_kwargs={"temperature": 0.2, "max_tokens": 64, "presence_penalty": 1.0, "timeout": 8},
    )

    assert result.assistant_text == "hello"
    assert result.tool_calls == []
    assert result.usage == {"input_tokens": 3, "output_tokens": 2}
    request = client.calls[0]
    assert request["store"] is False
    assert request["max_output_tokens"] == 64
    assert "max_tokens" not in request
    assert "presence_penalty" not in request
    assert request["input"][0]["role"] == "system"
    assert request["input"][1]["content"][0] == {"type": "input_text", "text": "hi"}


@pytest.mark.asyncio
async def test_responses_function_call_round_trips_same_call_id_into_tool_output():
    client = _client(response=_response(_function_call()))
    result = await create(
        _responses_mc(client),
        [{"role": "user", "content": "weather"}],
        tools=[{"type": "function", "function": {"name": "get_weather", "description": "weather", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        gen_kwargs={},
    )

    assert [(call.id, call.name, call.arguments) for call in result.tool_calls] == [
        ("call_weather", "get_weather", {"city": "Hangzhou"}),
    ]
    assert client.calls[0]["tools"] == [{
        "type": "function", "name": "get_weather", "description": "weather", "parameters": {"type": "object"},
    }]
    continuation = responses_input(result.continuation_items + [{
        "role": "tool", "tool_call_id": "call_weather", "content": "sunny",
    }])
    assert continuation[-2] == {
        "type": "function_call", "call_id": "call_weather", "name": "get_weather", "arguments": '{"city":"Hangzhou"}',
    }
    assert continuation[-1] == {"type": "function_call_output", "call_id": "call_weather", "output": "sunny"}


@pytest.mark.asyncio
async def test_responses_stream_requires_completed_event_and_yields_text_deltas():
    final = _response(_message("hello"))
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_text.delta", delta="hel"),
        SimpleNamespace(type="response.function_call_arguments.delta", item_id="ignored", delta="{"),
        SimpleNamespace(type="response.output_text.delta", delta="lo"),
        SimpleNamespace(type="response.output_item.done"),
        SimpleNamespace(type="response.completed", response=final),
    ]
    pieces = [piece async for piece in stream_text(
        _responses_mc(_client(stream_events=events)),
        [{"role": "user", "content": "hi"}],
        gen_kwargs={},
    )]
    assert "".join(pieces) == "hello"


@pytest.mark.asyncio
async def test_responses_rejects_chat_completion_shape_and_empty_or_unknown_output():
    mc = _responses_mc(_client(response=SimpleNamespace(choices=[])))
    with pytest.raises(UpstreamResponseFormatError, match="missing completed status"):
        await create(mc, [], tools=None, tool_choice=None, gen_kwargs={})

    mc.client.responses.create = _client(response=_response()).responses.create
    with pytest.raises(UpstreamResponseFormatError, match="no consumable items"):
        await create(mc, [], tools=None, tool_choice=None, gen_kwargs={})

    mc.client.responses.create = _client(response=_response(SimpleNamespace(type="mystery"))).responses.create
    with pytest.raises(UpstreamResponseFormatError, match="unknown item type"):
        await create(mc, [], tools=None, tool_choice=None, gen_kwargs={})


@pytest.mark.asyncio
async def test_chat_protocol_rejects_responses_shape_and_missing_sdk_capability_is_explicit():
    async def wrong_shape(**_kwargs):
        return _response(_message("wrong"))

    chat_mc = ModelClient(
        name="chat-test", provider_kind="openai", model="test", tool_call_mode="function_calling",
        prompt_style="narrative", params={}, client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=wrong_shape,
        ))),
    )
    with pytest.raises(UpstreamResponseFormatError, match="expected an OpenAI ChatCompletion"):
        await create(chat_mc, [], tools=None, tool_choice=None, gen_kwargs={})

    missing_sdk = _responses_mc(SimpleNamespace())
    with pytest.raises(UpstreamResponseFormatError, match="does not support client.responses.create"):
        await create(missing_sdk, [], tools=None, tool_choice=None, gen_kwargs={})


@pytest.mark.asyncio
async def test_chat_turn_consumes_responses_tool_call(monkeypatch):
    from core import llm_client

    client = _client(response=_response(_function_call("call_42", '{"query":"rain"}')))
    mc = _responses_mc(client)
    monkeypatch.setattr(llm_client, "get_model_client", lambda *_args, **_kwargs: mc)
    monkeypatch.setattr(llm_client, "_record_debug_request", lambda **_kwargs: None)
    monkeypatch.setattr(llm_client, "_record_api_call", lambda **_kwargs: None)

    turn = await llm_client.chat_turn(
        [{"role": "user", "content": "search"}],
        [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
    )
    assert turn.tool_calls == [{"id": "call_42", "name": "get_weather", "arguments": {"query": "rain"}}]
    assert turn.continuation_items == [{
        "type": "function_call", "call_id": "call_42", "name": "get_weather", "arguments": '{"query":"rain"}',
    }]


@pytest.mark.asyncio
async def test_responses_tool_loop_second_step_receives_function_call_output(monkeypatch):
    from core import llm_client

    client = _client(response=None)
    responses = iter([
        _response(_function_call("call_9", '{"city":"Hangzhou"}')),
        _response(_message("The weather is clear.")),
    ])

    async def create_response(**kwargs):
        client.calls.append(kwargs)
        return next(responses)

    client.responses.create = create_response
    mc = _responses_mc(client)
    monkeypatch.setattr(llm_client, "get_model_client", lambda *_args, **_kwargs: mc)
    monkeypatch.setattr(llm_client, "_record_debug_request", lambda **_kwargs: None)
    monkeypatch.setattr(llm_client, "_record_api_call", lambda **_kwargs: None)
    tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]

    first = await llm_client.chat_turn([{"role": "user", "content": "weather"}], tools)
    second = await llm_client.chat_turn(
        first.continuation_items + [{
            "role": "tool", "tool_call_id": "call_9", "content": "clear",
        }],
        tools,
    )

    assert second.content == "The weather is clear."
    second_input = client.calls[1]["input"]
    assert {"type": "function_call_output", "call_id": "call_9", "output": "clear"} in second_input


@pytest.mark.asyncio
async def test_anthropic_messages_converts_system_tools_and_bearer_auth():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await create(
            _anthropic_mc(client),
            [{"role": "system", "content": "be concise"}, {"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {
                "name": "weather", "description": "weather", "parameters": {"type": "object"},
            }}],
            tool_choice="auto",
            gen_kwargs={"max_tokens": 64, "temperature": 0.2, "timeout": 8},
        )
    finally:
        await client.aclose()

    assert result.assistant_text == "hello"
    assert result.usage == {"input_tokens": 3, "output_tokens": 2}
    assert captured["path"] == "/v1/messages"
    assert captured["headers"]["authorization"] == "Bearer test-secret"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["json"]["system"] == "be concise"
    assert captured["json"]["tools"] == [{
        "name": "weather", "description": "weather", "input_schema": {"type": "object"},
    }]
    assert captured["json"]["tool_choice"] == {"type": "auto"}
    assert "timeout" not in captured["json"]


@pytest.mark.asyncio
async def test_anthropic_messages_tool_call_round_trips_into_tool_result():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={
                "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "weather", "input": {"city": "Hangzhou"}}],
            })
        return httpx.Response(200, json={
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "sunny"}],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mc = _anthropic_mc(client, auth_mode="x_api_key")
    tools = [{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}]
    try:
        first = await create(mc, [{"role": "user", "content": "weather"}], tools=tools, tool_choice="auto", gen_kwargs={})
        second = await create(
            mc,
            first.continuation_items + [{"role": "tool", "tool_call_id": "toolu_1", "content": "clear"}],
            tools=tools,
            tool_choice="auto",
            gen_kwargs={},
        )
    finally:
        await client.aclose()

    assert [(call.id, call.name, call.arguments) for call in first.tool_calls] == [
        ("toolu_1", "weather", {"city": "Hangzhou"}),
    ]
    assert second.assistant_text == "sunny"
    assert requests[1]["messages"] == [
        {"role": "assistant", "content": [{
            "type": "tool_use", "id": "toolu_1", "name": "weather", "input": {"city": "Hangzhou"},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_1", "content": "clear",
        }]},
    ]


@pytest.mark.asyncio
async def test_anthropic_messages_stream_yields_text_and_requires_message_stop():
    sse = (
        'event: content_block_delta\n'
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hel"}}\n\n'
        'event: content_block_delta\n'
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}\n\n'
        'event: message_stop\n'
        'data: {"type":"message_stop"}\n\n'
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        pieces = [piece async for piece in stream_text(
            _anthropic_mc(client), [{"role": "user", "content": "hi"}], gen_kwargs={},
        )]
    finally:
        await client.aclose()
    assert "".join(pieces) == "hello"


def test_anthropic_messages_input_groups_tool_results_as_a_user_message():
    system, messages = anthropic_messages_input([
        {"role": "system", "content": "one"},
        {"role": "developer", "content": "two"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "done"},
    ])
    assert system == "one\n\ntwo"
    assert messages[-1] == {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "t1", "content": "done",
    }]}


def _chat_mc(client):
    return ModelClient(
        name="chat-test",
        provider_kind="openai",
        model="test-model",
        tool_call_mode="function_calling",
        prompt_style="narrative",
        params={"temperature": 0.2, "max_tokens": 64},
        client=client,
        api_protocol="chat_completions",
    )


def test_chat_completions_input_strips_sdk_and_internal_fields():
    rebuilt = chat_completions_input([
        {"role": "system", "content": "stay in character", "_layer": "1_core", "refusal": "no"},
        {
            "role": "assistant",
            "content": None,
            "refusal": "I cannot",
            "annotations": [{"type": "url"}],
            "audio": {"id": "a1"},
            "reasoning_content": "secret",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "index": 0,
                "extra": "drop-me",
                "function": {"name": "lookup", "arguments": '{"q":"rain"}', "parsed": {"q": "rain"}},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "lookup",
            "content": "wet",
            "_continuity_receipt": {"id": "hidden"},
            "status": "ok",
        },
        {"role": "user", "content": [{"type": "text", "text": "hi"}], "timestamp": 1},
    ])
    assert rebuilt == [
        {"role": "system", "content": "stay in character"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q":"rain"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "lookup", "content": "wet"},
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    ]


def test_chat_completions_tools_rebuild_openai_shape_and_encode_type_unions():
    rebuilt = chat_completions_tools([{
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "find things",
            "parameters": {
                "type": "object",
                "properties": {"after": {"type": ["number", "null"], "minimum": 0}},
                "required": ["after"],
            },
            "extra": "drop-me",
        },
        "strict": True,
    }])
    assert rebuilt == [{
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "find things",
            "parameters": {
                "type": "object",
                "properties": {"after": {"anyOf": [
                    {"type": "number", "minimum": 0},
                    {"type": "null", "minimum": 0},
                ]}},
                "required": ["after"],
            },
        },
    }]


@pytest.mark.asyncio
async def test_chat_completions_tool_loop_second_step_sends_whitelisted_history():
    calls = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(
            content=None,
            refusal="I cannot",
            annotations=[{"type": "url"}],
            reasoning_content="hidden",
            tool_calls=[SimpleNamespace(
                id="call_9",
                type="function",
                index=0,
                function=SimpleNamespace(name="lookup", arguments='{"q":"rain"}'),
                model_dump=lambda exclude_none=True: {
                    "id": "call_9", "type": "function", "index": 0,
                    "function": {"name": "lookup", "arguments": '{"q":"rain"}', "parsed": {"q": "rain"}},
                },
            )],
            model_dump=lambda exclude_none=True: {
                "role": "assistant", "content": None, "refusal": "I cannot",
                "annotations": [{"type": "url"}], "reasoning_content": "hidden",
                "tool_calls": [{
                    "id": "call_9", "type": "function", "index": 0,
                    "function": {"name": "lookup", "arguments": '{"q":"rain"}'},
                }],
            },
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")])

    mc = _chat_mc(SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))))
    first = await create(
        mc,
        [{"role": "user", "content": "search", "_layer": "user"}],
        tools=[{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        gen_kwargs={"timeout": 8, "temperature": 0.2},
    )
    assert first.continuation_items == [{
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_9",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"q":"rain"}'},
        }],
    }]

    await create(
        mc,
        first.continuation_items + [{
            "role": "tool",
            "tool_call_id": "call_9",
            "content": "wet",
            "_continuity_receipt": {"id": "hidden"},
        }],
        tools=[{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        gen_kwargs={"timeout": 8},
    )
    assert calls[0]["messages"] == [{"role": "user", "content": "search"}]
    assert calls[1]["messages"] == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_9",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q":"rain"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_9", "content": "wet"},
    ]
    assert "refusal" not in calls[1]["messages"][0]
    assert calls[0]["tools"][0]["function"]["name"] == "lookup"
    assert calls[0]["timeout"] == 8


def test_error_category_for_exception_maps_http_and_protocol_failures():
    class FakeHttpError(Exception):
        status_code = 400

    assert error_category_for_exception(FakeHttpError()) == "upstream_request_rejected"
    assert error_category_for_exception(UpstreamResponseFormatError("bad", http_status=404)) == "upstream_not_found"
    assert error_category_for_exception(RuntimeError("offline")) == "protocol_incompatible"
