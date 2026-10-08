"""Tool gates must not broaden exposure or redraft a rejected action."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from core import decision_probe as probe, llm_failover, model_registry, llm_client


def schema(name='fixture_tool', properties=None, required=None, **kw):
    return {'type': 'function', 'function': {'name': name, 'description': 'fixture',
        'parameters': {'type': 'object', 'properties': properties or {}, 'required': required or [], **kw}}}


@pytest.fixture
async def native(monkeypatch):
    llm_failover.clear_breakers()
    state = {'choice': 'fixture_tool', 'calls': [], 'fallback': ''}
    async def handle(req):
        body = json.loads(req.content)
        state['calls'].append(body)
        answers = {}
        for key, q in body['questions'].items():
            chosen = state['choice'] if key == 'tool' else '1'
            answers[key] = {'type': 'choice', 'choice': chosen, 'confidence': .9,
                'probabilities': {k: float(k == chosen) for k in q['criteria']}}
        return httpx.Response(200, json={'model': 'resolved', 'answers': answers})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        mc = SimpleNamespace(name='native', api_protocol='systemone', client=http,
            base_url='https://example.test/v1', api_key='fixture', model='jev', provider_kind='openai')
        text = SimpleNamespace(name='text', api_protocol='chat_completions', prompt_style='narrative',
            tool_call_mode='function_calling', params={}, model='small', provider_kind='openai')
        monkeypatch.setattr(model_registry, 'get_model_client', lambda *a, **kw: text if kw.get('preset_name') else mc)
        monkeypatch.setattr(model_registry, 'resolve_fallback_route', lambda *a, **kw: {'preset': state['fallback']})
        monkeypatch.setattr(llm_failover, 'resolve_fallback_client', lambda *a, **kw: (None, 'not_configured'))
        text_call = AsyncMock(return_value=SimpleNamespace(assistant_text='', tool_calls=[
            SimpleNamespace(name='fixture_tool', arguments={'query': 'fixture'})]))
        monkeypatch.setattr('core.llm_protocol.create', text_call)
        yield state, text_call


MESSAGES = [{'role': 'system', 'content': 'fixture policy'}, {'role': 'user', 'content': 'fixture request'}]


@pytest.mark.asyncio
async def test_none_does_not_call_text(native):
    state, text = native
    state['choice'] = 'none'
    assert await probe.probe(MESSAGES, [schema()], 'fixture') == ''
    text.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_noarg_and_enum_remain_closed(native):
    state, text = native
    raw = await probe.probe(MESSAGES, [schema()], 'fixture')
    assert llm_client.parse_probe_response(raw).tool_calls == [{'name': 'fixture_tool', 'arguments': {}}]
    raw = await probe.probe(MESSAGES, [schema(properties={'mode': {'type': 'string', 'enum': ['a', 'b']}}, required=['mode'])], 'fixture')
    assert llm_client.parse_probe_response(raw).tool_calls[0]['arguments'] == {'mode': 'b'}
    assert len(state['calls']) == 3
    text.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_open_arg_without_text_stays_missing(native):
    _, text = native
    raw = await probe.probe(MESSAGES, [schema(properties={'query': {'type': 'string'}}, required=['query'])], 'fixture')
    assert llm_client.parse_probe_response(raw).tool_calls[0]['arguments'] == {}
    text.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_arg_model_sees_only_selected_tool(native):
    state, text = native
    state['fallback'] = 'text'
    raw = await probe.probe(MESSAGES, [schema(properties={'query': {'type': 'string'}}, required=['query']), schema('other')], 'fixture')
    assert llm_client.parse_probe_response(raw).tool_calls[0]['arguments'] == {'query': 'fixture'}
    assert len(text.call_args.kwargs['tools']) == 1
    assert text.call_args.kwargs['tools'][0]['function']['name'] == 'fixture_tool'
    assert len(state['calls']) == 1 and text.await_count == 1


@pytest.mark.asyncio
async def test_argument_model_cannot_change_selected_tool(native):
    state, text = native
    state['fallback'] = 'text'
    text.return_value.tool_calls[0].name = 'other'
    with pytest.raises(ValueError, match='arguments_invalid'):
        await probe.probe(MESSAGES, [schema(properties={'query': {'type': 'string'}}, required=['query'])], 'fixture')


@pytest.mark.asyncio
@pytest.mark.parametrize('schemas', [[schema('mcp__fixture')], [schema(additionalProperties=True)],
    [schema(str(i)) for i in range(32)]])
async def test_capability_refusal_requires_explicit_text(native, monkeypatch, schemas):
    state, _ = native
    with pytest.raises(ValueError, match='explicit_text'):
        await probe.probe(MESSAGES, schemas, 'fixture')
    assert state['calls'] == []
    state['fallback'] = 'text'
    chat = AsyncMock(return_value='')
    monkeypatch.setattr(llm_client, 'chat', chat)
    assert await probe.probe(MESSAGES, schemas, 'fixture') == ''
    assert chat.call_args.kwargs['preset_name'] == 'text' and chat.await_count == 1
