"""Mixed decisions preserve rejection, evidence and publication boundaries."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from core import ime_awareness as awareness, llm_failover, model_registry


@pytest.fixture
async def native(monkeypatch):
    llm_failover.clear_breakers()
    state = {'worth': .9, 'confidence': .9, 'status': 200, 'calls': 0}
    async def handle(req):
        state['calls'] += 1
        body = json.loads(req.content)
        assert len(body['questions']) == 3 and body['state']['content'] == 'fixture'
        return httpx.Response(state['status'], json={'model': 'resolved', 'answers': {
            'activity': {'type': 'choice', 'choice': 'work', 'confidence': .8,
                'probabilities': {'work': 1, 'chat': 0, 'mixed': 0, 'unknown': 0}},
            'worth_contact': {'type': 'noul', 'noul': state['worth']},
            'confidence': {'type': 'noul', 'noul': state['confidence']},
        }})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        mc = SimpleNamespace(name='native', api_protocol='systemone', client=http,
            base_url='https://example.test/v1', api_key='fixture', model='jev', provider_kind='openai')
        text = SimpleNamespace(name='text', api_protocol='chat_completions', prompt_style='narrative',
            model='small', provider_kind='openai')
        monkeypatch.setattr(model_registry, 'get_model_client', lambda *a, **kw: text if kw.get('preset_name') else mc)
        monkeypatch.setattr(model_registry, 'resolve_fallback_route', lambda *a, **kw: {'preset': 'text'})
        monkeypatch.setattr(llm_failover, 'resolve_fallback_client', lambda *a, **kw: (text, ''))
        draft = AsyncMock(return_value=SimpleNamespace(assistant_text=json.dumps({
            'summary': '她在记录感受', 'evidence': 'fixture', 'uncertainty': '发送未知', 'topic': '日记'})))
        monkeypatch.setattr('core.llm_protocol.create', draft)
        yield state, draft


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('worth', .1), ('confidence', .4)])
async def test_native_rejection_never_drafts(native, field, value):
    state, draft = native
    state[field] = value
    result = awareness.Assessment.model_validate_json(await awareness.assess({'content': 'fixture'}, 'fixture'))
    assert state['calls'] == 1 and result.evidence == ''
    draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_success_drafts_only_open_fields(native):
    state, draft = native
    result = awareness.Assessment.model_validate_json(await awareness.assess({'content': 'fixture'}, 'fixture'))
    assert result.worth_contact and result.confidence == .9 and result.activity == 'work'
    assert result.evidence == 'fixture' and state['calls'] == 1 and draft.await_count == 1


@pytest.mark.asyncio
async def test_draft_cannot_override_gate(native):
    _, draft = native
    draft.return_value.assistant_text = json.dumps({'summary': '', 'evidence': '', 'uncertainty': '',
        'topic': '', 'worth_contact': True})
    with pytest.raises(Exception):
        await awareness.assess({'content': 'fixture'}, 'fixture')
    assert draft.await_count == 1


@pytest.mark.asyncio
async def test_native_without_explicit_draft_fails_closed(native, monkeypatch):
    _, draft = native
    monkeypatch.setattr(model_registry, 'resolve_fallback_route', lambda *a, **kw: {'preset': ''})
    with pytest.raises(ValueError, match='text_preset_missing'):
        await awareness.assess({'content': 'fixture'}, 'fixture')
    draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_failover_is_one_complete_assessment(native):
    state, draft = native
    state['status'] = 503
    draft.return_value.assistant_text = json.dumps(dict(activity='chat', confidence=.8, worth_contact=True,
        summary='她在聊天', evidence='fixture', uncertainty='', topic='交流'))
    result = awareness.Assessment.model_validate_json(await awareness.assess({'content': 'fixture'}, 'fixture'))
    assert result.activity == 'chat' and state['calls'] == 1 and draft.await_count == 1


@pytest.mark.asyncio
async def test_draft_failure_cannot_publish(native, monkeypatch):
    from core import ime_drafts
    from core.autonomy import store
    from tests.test_ime_awareness import draft as row
    _, model = native
    model.side_effect = TimeoutError('fixture')
    monkeypatch.setattr(awareness, 'effective_state', lambda *a: {'effective': True})
    ime_drafts.receive('fixture', [row(content='fixture')])
    await awareness.tick('owner-fixture', 'character-fixture')
    assert ime_drafts.analysis_query()[0]['status'] == 'failed'
    assert store.drain_pending_signals('owner-fixture', 'character-fixture') == []
