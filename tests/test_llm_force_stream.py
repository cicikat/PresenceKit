"""Buffered SSE compatibility uses complete turns, never partial tool calls."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from core.llm_protocol import create, UpstreamResponseFormatError
from core.model_registry import ModelClient


def chunk(text=None, calls=None, finish=None, reasoning=None):
    return NS(choices=[NS(index=0, delta=NS(content=text, tool_calls=calls,
               reasoning_content=reasoning), finish_reason=finish)])


def call(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


class Stream:
    def __init__(self, events):
        self.events = events
        self.close = AsyncMock()

    async def __aiter__(self):
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event


@pytest.fixture(autouse=True)
def no_archive(monkeypatch):
    monkeypatch.setattr("core.llm_reasoning_store.Capture.save", AsyncMock())


def client(events):
    stream = Stream(events)
    sdk_create = AsyncMock(return_value=stream)
    mc = ModelClient(name="test", provider_kind="openai", model="test",
        tool_call_mode="function_calling", prompt_style="narrative", params={},
        client=NS(chat=NS(completions=NS(create=sdk_create))), force_stream=True)
    return mc, stream, sdk_create


@pytest.mark.asyncio
async def test_collects_text_and_usage_and_closes():
    mc, stream, request = client([chunk("你好", reasoning="private"), chunk("。", finish="stop"),
                                 NS(choices=[], usage=NS(total_tokens=12))])
    result = await create(mc, [{"role": "user", "content": "hello"}], gen_kwargs={"max_tokens": 32})
    assert result.assistant_text == "你好。"
    assert "private" not in str(result.continuation_items)
    assert result.usage == {"total_tokens": 12}
    assert request.call_args.kwargs["stream"] is True
    assert request.call_args.kwargs["stream_options"] == {"include_usage": True}
    assert request.call_args.kwargs["max_tokens"] == 32
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_interleaved_tool_fragments_are_assembled_by_index():
    mc, stream, request = client([
        chunk(calls=[call(1, "b", "second", '{"x":'), call(0, "a", "first", "{")]),
        chunk(calls=[call(0, args="}"), call(1, args="2}")], finish="tool_calls"),
    ])
    tools = [{"type": "function", "function": {"name": "first"}}]
    result = await create(mc, [], tools=tools, tool_choice="auto", gen_kwargs={})
    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [("a", "first", {}), ("b", "second", {"x": 2})]
    assert request.call_args.kwargs["tools"] == [{
        "type": "function",
        "function": {"name": "first", "parameters": {"type": "object", "properties": {}}},
    }]
    assert result.continuation_items[0]["tool_calls"][1]["id"] == "b"
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("events", [
    [chunk("partial")],
    [chunk(calls=[call(0, "a", "tool", "{")], finish="tool_calls")],
    [chunk(calls=[call(0, "a", "tool", "{}")], finish="length")],
    [chunk(finish="tool_calls")],
    [chunk(calls=[call(None, "a", "tool", "{}")], finish="tool_calls")],
])
async def test_rejects_incomplete_or_malformed_turn(events):
    mc, stream, _ = client(events)
    with pytest.raises(UpstreamResponseFormatError):
        await create(mc, [], gen_kwargs={})
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_closes_stream():
    mc, stream, _ = client([chunk("partial"), asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await create(mc, [], gen_kwargs={})
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_preserves_nonstream_request():
    mc, _, request = client([])
    mc.force_stream = False
    request.return_value = NS(choices=[NS(message=NS(content="normal"), finish_reason="stop")])
    assert (await create(mc, [], gen_kwargs={})).assistant_text == "normal"
    assert "stream" not in request.call_args.kwargs


@pytest.mark.parametrize("protocol, enabled", [("responses", True), ("anthropic_messages", True), ("chat_completions", "true")])
def test_registry_rejects_unsupported_force_stream(monkeypatch, protocol, enabled):
    from core import model_registry as registry
    monkeypatch.setattr(registry, "get_config", lambda: {"model_presets": {"presets": {
        "test": {"provider_kind": "openai", "model": "test", "api_protocol": protocol, "force_stream": enabled},
    }}})
    with pytest.raises(ValueError, match="force_stream"):
        registry._build_model_client("test")
