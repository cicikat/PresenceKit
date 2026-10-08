"""Closed secondary decisions keep existing conservative consumers."""
import json
from types import SimpleNamespace

import httpx
import pytest

from core import model_registry, llm_failover


@pytest.mark.asyncio
async def test_native_letter_score_relation_and_reconcile(monkeypatch):
    from core.mail import letter_writer
    from core.dream import invariants
    from core.dream.scenario_reconciler import build_reconcile_messages, parse_decision
    from core.decision_contract import closed, DecisionRequest, Question
    llm_failover.clear_breakers()
    calls = []
    async def handle(req):
        body = json.loads(req.content)
        calls.append(body)
        answers = {}
        for key, q in body['questions'].items():
            if q['type'] == 'score':
                answers[key] = {'type': 'score', 'score': 4, 'confidence': .9,
                    'probabilities': {str(i): float(i == 4) for i in range(5)}}
            else:
                selected = 'same' if key == 'relation' else 'uncertain'
                answers[key] = {'type': 'choice', 'choice': selected, 'confidence': .9,
                    'probabilities': {k: float(k == selected) for k in q['criteria']}}
        return httpx.Response(200, json={'model': 'resolved', 'answers': answers})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        mc = SimpleNamespace(name='native', api_protocol='systemone', client=http,
            base_url='https://example.test/v1', api_key='fixture', model='jev', provider_kind='openai')
        monkeypatch.setattr(model_registry, 'get_model_client', lambda *a, **kw: mc)
        monkeypatch.setattr(llm_failover, 'resolve_fallback_client', lambda *a, **kw: (None, 'not_configured'))
        assert await letter_writer.evaluate_letter('具体的合成细节' * 35) == 5
        assert await invariants._relation({'situation': 's', 'response': 'r'}, {'situation': 's', 'response': 'r'}, char_id='fixture') == 'same'
        messages = build_reconcile_messages({'dialogue': [{'role': 'user', 'text': 'maybe'}]})
        request = DecisionRequest('scenario_reconcile', json.loads(messages[1]['content']), {
            'decision': Question('choice', messages[0]['content'], {'stay': '', 'advance_next': '', 'uncertain': ''})})
        assert parse_decision(await closed(request, messages, call_category='scenario_reconcile', max_tokens=32, char_id='fixture')) == 'uncertain'
    assert len(calls) == 3


def test_invariant_relation_route_preserves_summary_compatibility(monkeypatch):
    cfg = {'presets': {'text': {'model': 'small'}, 'jev': {'api_protocol': 'systemone'}},
        'default_preset': 'text', 'active_routing': 'fixture',
        'routing_profiles': {'fixture': {'summary': 'text'}}}
    monkeypatch.setattr(model_registry, '_get_preset_config', lambda: cfg)
    monkeypatch.setattr(model_registry, '_active_char_model_routing', lambda: None)
    assert model_registry.resolve_category_info('invariants_relation')['source'] == 'summary_fallback'
    assert model_registry._resolve_preset_name('invariants_relation') == 'text'
    cfg['routing_profiles']['fixture']['invariants_relation'] = 'jev'
    assert model_registry.resolve_category_info('invariants_relation')['api_protocol'] == 'systemone'
    assert model_registry._resolve_preset_name('summary') == 'text'
