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
        "function": {"name": "first", "description": "Tool first", "parameters": {"type": "object", "properties": {}}},
    }]
    assert result.continuation_items[0]["tool_calls"][1]["id"] == "b"
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_usage_choice_and_duplicate_terminal_preserve_tool_turn():
    from openai.types.chat import ChatCompletionChunk
    trailer = ChatCompletionChunk(id="fixture", created=0, model="fixture", object="chat.completion.chunk",
        choices=[{"index": 0, "delta": {}, "finish_reason": None}],
        usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})
    mc, stream, _ = client([
        chunk(calls=[call(0, "a", "first", '{"x":2}')], finish="tool_calls"),
        trailer, chunk(finish="tool_calls"),
    ])
    result = await create(mc, [], gen_kwargs={})
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].arguments == {"x": 2}
    assert result.usage["total_tokens"] == 5
    assert result.status == "tool_calls"
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_openlux_observed_trailers_preserve_completed_tool_call():
    """Gateway role/stop trailers must not erase a completed document action."""
    from openai.types.chat import ChatCompletionChunk
    def wire(delta, finish=None, usage=None):
        return ChatCompletionChunk(id="synthetic", created=0, model="fixture",
            object="chat.completion.chunk", choices=[{
                "index": 0, "delta": delta, "finish_reason": finish}], usage=usage)
    terminal = wire({"role": "assistant", "content": "", "tool_calls": [{
        "index": 0, "id": "doc", "type": "function", "function": {
            "name": "write_document", "arguments": '{"path":"protocol-test.md","content":"hello"}'}}]}, "tool_calls")
    mc, stream, _ = client([terminal, terminal,
        wire({"role": "assistant", "content": ""}), wire({}, "stop"),
        NS(choices=[], usage=NS(total_tokens=237))])
    mc.stream_compatibility = "openlux"
    result = await create(mc, [], gen_kwargs={})
    assert result.status == "tool_calls"
    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [
        ("doc", "write_document", {"path": "protocol-test.md", "content": "hello"})]
    assert result.usage["total_tokens"] == 237
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", [
    chunk("new text"), chunk(calls=[call(0, args="}")]),
    chunk(reasoning="new reasoning"), chunk(finish="length"),
    NS(choices=[NS(index=0, delta=NS(role="user"), finish_reason=None)]),
])
async def test_openlux_still_rejects_conflicts_after_tools(tail):
    """Opt-in compatibility must never execute extra or truncated tool data."""
    mc, stream, _ = client([
        chunk(calls=[call(0, "a", "write_document", '{}')], finish="tool_calls"), tail])
    mc.stream_compatibility = "openlux"
    with pytest.raises(UpstreamResponseFormatError):
        await create(mc, [], gen_kwargs={})
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_strict_mode_rejects_observed_role_and_stop_trailers():
    """Existing presets retain their validation unless explicitly opted in."""
    mc, _, _ = client([chunk(calls=[call(0, "a", "write_document", "{}")], finish="tool_calls"),
        NS(choices=[NS(index=0, delta=NS(role="assistant", content=""), finish_reason=None)]), chunk(finish="stop")])
    with pytest.raises(UpstreamResponseFormatError):
        await create(mc, [], gen_kwargs={})


@pytest.mark.asyncio
async def test_duplicate_tool_ids_are_rejected_before_execution():
    mc, _, _ = client([chunk(calls=[call(0, "same", "first", "{}"),
        call(1, "same", "second", "{}")], finish="tool_calls")])
    with pytest.raises(UpstreamResponseFormatError, match="duplicate tool call ids"):
        await create(mc, [], gen_kwargs={})


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", [
    chunk("late text"), chunk(calls=[call(0, args="}")]),
    chunk(reasoning="late reasoning"), chunk(finish="length"),
    NS(choices=[NS(index=0, delta=NS(refusal="late refusal"), finish_reason=None)]),
    NS(choices=[NS(index=0, delta=NS(future_field="new data"), finish_reason=None)]),
])
async def test_completed_turn_rejects_new_data_and_conflicting_terminal(tail):
    mc, stream, _ = client([chunk("complete", finish="stop"), tail])
    with pytest.raises(UpstreamResponseFormatError, match="choices after completion"):
        await create(mc, [], gen_kwargs={})
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


@pytest.mark.asyncio
@pytest.mark.parametrize('ending', [chunk('late', finish=None), RuntimeError('disconnected')])
async def test_failed_buffer_retains_safe_prose_without_claiming_success(ending):
    mc, stream, _ = client([chunk('<think>private</think>visible answer', finish='stop'), ending])
    with pytest.raises(Exception) as failure:
        await create(mc, [], gen_kwargs={})
    assert failure.value.partial_text == 'visible answer'
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_tool_buffer_never_exposes_arguments_as_partial_reply():
    mc, _, _ = client([chunk(calls=[call(0, 'x', 'write_document', '{')]), RuntimeError('disconnected')])
    with pytest.raises(RuntimeError) as failure:
        await create(mc, [], gen_kwargs={})
    assert not getattr(failure.value, 'partial_text', '')


@pytest.mark.asyncio
async def test_bad_trailer_diagnostic_contains_shape_not_payload():
    mc, _, _ = client([chunk('answer', finish='stop'), chunk('private-new-content')])
    with pytest.raises(UpstreamResponseFormatError) as failure:
        await create(mc, [], gen_kwargs={})
    assert 'delta_fields=' in str(failure.value)
    assert 'buffered_chars=6' in str(failure.value)
    assert 'buffered_tools=0' in str(failure.value)
    assert 'tail_content_matches_buffer=False' in str(failure.value)
    assert 'private-new-content' not in str(failure.value)
    assert failure.value.partial_text == 'answer'


@pytest.mark.asyncio
@pytest.mark.parametrize('tail_text, matches', [('answer', True), ('answer extra', False)])
async def test_rejected_text_replay_reports_equality_without_releasing_tail(tail_text, matches):
    """A repeated final answer can be diagnosed without accepting unseen text."""
    mc, stream, _ = client([chunk('answer', finish='stop'), chunk(tail_text, finish='stop')])
    mc.stream_compatibility = 'openlux'
    with pytest.raises(UpstreamResponseFormatError) as failure:
        await create(mc, [], gen_kwargs={})
    assert f'tail_content_matches_buffer={matches}' in str(failure.value)
    assert failure.value.partial_text == 'answer'
    assert tail_text not in str(failure.value)
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_tool_tail_reports_counts_without_arguments_or_partial_reply():
    """An operator can distinguish an interrupted tool turn from lost prose."""
    mc, stream, _ = client([
        chunk(calls=[call(0, 'fixture-call', 'read_fixture', '{"key":"private-fixture"}')], finish='tool_calls'),
        chunk(calls=[call(0, args='private-late-arguments')]),
    ])
    mc.stream_compatibility = 'openlux'
    with pytest.raises(UpstreamResponseFormatError) as failure:
        await create(mc, [], gen_kwargs={})
    diagnostic = str(failure.value)
    assert 'buffered_chars=0' in diagnostic
    assert 'buffered_tools=1' in diagnostic
    assert 'private-fixture' not in diagnostic and 'private-late-arguments' not in diagnostic
    assert not getattr(failure.value, 'partial_text', '')
    stream.close.assert_awaited_once()


def tool_snapshot(calls, *, finish='tool_calls', text=None):
    from openai.types.chat import ChatCompletionChunk
    delta = {'role': 'assistant', 'tool_calls': calls}
    if text is not None:
        delta['content'] = text
    return ChatCompletionChunk(id='fixture-event', created=0, model='fixture',
        object='chat.completion.chunk', choices=[{'index': 0, 'delta': delta, 'finish_reason': finish}])


@pytest.mark.asyncio
@pytest.mark.parametrize('fragmented', [False, True])
async def test_complete_tool_snapshot_replay_preserves_one_call(fragmented):
    """A gateway's full replay must not duplicate assembled arguments or actions."""
    full = {'index': 0, 'id': 'fixture-call', 'type': 'function',
            'function': {'name': 'read_fixture', 'arguments': '{"key":"fixture"}'},
            'extra_content': {'google': {'thought_signature': 'synthetic-signature'}}}
    if fragmented:
        first = {**full, 'function': {**full['function'], 'arguments': '{"key":'}}
        events = [tool_snapshot([first], finish=None), tool_snapshot([
            {'index': 0, 'function': {'arguments': '"fixture"}'}}])]
    else:
        events = [tool_snapshot([full], text='')]
    events += [tool_snapshot([full]), chunk(finish='stop')]
    mc, stream, _ = client(events)
    mc.stream_compatibility = 'openlux'
    result = await create(mc, [], gen_kwargs={})
    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [
        ('fixture-call', 'read_fixture', {'key': 'fixture'})]
    assert result.continuation_items[0]['tool_calls'][0]['extra_content'] == full['extra_content']
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['id', 'name', 'arguments', 'signature', 'extra', 'text', 'strict', 'event_id', 'choice_extra'])
async def test_tool_snapshot_replay_never_accepts_changed_actions_or_metadata(change):
    """Duplicate handling must reject altered tool identity, data and signatures."""
    full = {'index': 0, 'id': 'fixture-call', 'type': 'function',
            'function': {'name': 'read_fixture', 'arguments': '{"key":"fixture"}'},
            'extra_content': {'google': {'thought_signature': 'synthetic-signature'}}}
    import copy
    changed = copy.deepcopy(full)
    if change == 'id': changed['id'] = 'other-call'
    if change in {'name', 'arguments'}: changed['function'][change] = 'changed'
    if change == 'signature': changed['extra_content']['google']['thought_signature'] = 'changed'
    if change == 'extra': changed['new_field'] = 'changed'
    tail = tool_snapshot([changed], text='new text' if change == 'text' else None)
    if change == 'event_id': tail.id = 'other-event'
    if change == 'choice_extra': tail.choices[0].future_field = 'changed'
    mc, stream, _ = client([tool_snapshot([full], text=''), tail])
    mc.stream_compatibility = 'strict' if change == 'strict' else 'openlux'
    with pytest.raises(UpstreamResponseFormatError):
        await create(mc, [], gen_kwargs={})
    stream.close.assert_awaited_once()
