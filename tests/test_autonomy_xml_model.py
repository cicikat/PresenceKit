import asyncio
from types import SimpleNamespace
import pytest
from core import llm_client


def test_xml_autonomy_calls_keep_ids_and_use_plain_tool_continuation(monkeypatch):
    monkeypatch.setattr(llm_client, '_prepare_call', lambda *args, **kwargs: (SimpleNamespace(tool_call_mode='xml_fallback'), [], {}))
    requests = []
    async def chat(messages, **kwargs):
        requests.append((messages, kwargs))
        return '<tool_call>{"name":"talk_owner","arguments":{"text":"hello"}}</tool_call>'
    monkeypatch.setattr(llm_client, 'chat', chat)
    tools = [{'type': 'function', 'function': {'name': 'talk_owner'}}]
    result = asyncio.run(llm_client.chat_turn([{'role':'tool','tool_call_id':'old','content':'safe fact'}], tools, char_id='char', is_proactive=True, allow_xml_fallback=True))
    assert result.tool_calls[0]['name'] == 'talk_owner'
    assert result.tool_calls[0]['id']
    assert requests[0][0][0]['role'] == 'system'
    assert requests[0][1]['char_id'] == 'char'
    assert requests[0][1]['is_proactive']


@pytest.mark.parametrize('response', ['<tool_call>{"name":"forbidden","arguments":{}}</tool_call>', '<tool_call>broken</tool_call>'])
def test_xml_rejects_unknown_and_malformed_tools(monkeypatch, response):
    monkeypatch.setattr(llm_client, '_prepare_call', lambda *args, **kwargs: (SimpleNamespace(tool_call_mode='xml_fallback'), [], {}))
    async def chat(*args, **kwargs): return response
    monkeypatch.setattr(llm_client, 'chat', chat)
    with pytest.raises(ValueError, match='autonomy_tool_encoding_invalid'):
        asyncio.run(llm_client.chat_turn([], [], allow_xml_fallback=True))


def test_xml_prose_is_private_silence(monkeypatch):
    monkeypatch.setattr(llm_client, '_prepare_call', lambda *args, **kwargs: (SimpleNamespace(tool_call_mode='xml_fallback'), [], {}))
    async def chat(*args, **kwargs): return 'I will stay quiet.'
    monkeypatch.setattr(llm_client, 'chat', chat)
    result = asyncio.run(llm_client.chat_turn([], [], allow_xml_fallback=True))
    assert result.tool_calls == []


def test_existing_callers_remain_native_only(monkeypatch):
    monkeypatch.setattr(llm_client, '_prepare_call', lambda *args, **kwargs: (SimpleNamespace(name='fixture', tool_call_mode='xml_fallback'), [], {}))
    with pytest.raises(ValueError, match='function_calling'):
        asyncio.run(llm_client.chat_turn([], []))
