import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from core import decision_contract as dc, llm_failover


def request():
    return dc.DecisionRequest('minecraft_reaction', {'owner_request': 'stop'}, {
        'action': dc.Question('choice', 'choose action', {'none': 'nothing', 'stop': 'stop'}),
    })


def body():
    return {'model': 'jev-fixed', 'answers': {'action': {'type': 'choice', 'choice': 'stop',
        'probabilities': {'none': .1, 'stop': .9}, 'confidence': .8}}, 'usage': {'input_tokens': 12}}


@pytest.mark.parametrize('base,expected', [
    ('https://api.example', 'https://api.example/v1/systemone'),
    ('https://api.example/v1/', 'https://api.example/v1/systemone'),
    ('https://api.example/v1/systemone/', 'https://api.example/v1/systemone'),
    ('https://api.example/api/v1', 'https://api.example/api/v1/systemone'),
])
def test_endpoint(base, expected):
    assert dc.endpoint(base) == expected


@pytest.mark.asyncio
async def test_native_wire_has_no_chat_or_generation_fields():
    seen = []
    async def handle(req):
        seen.append(req)
        return httpx.Response(200, json=body())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        mc = SimpleNamespace(client=client, base_url='https://api.example/v1/systemone', model='jev-latest', api_key='fixture')
        response = await dc.create(mc, request(), timeout=3)
    assert response.assistant_text == '{"action": "stop"}'
    assert response.raw_response.model == 'jev-fixed'
    assert seen[0].url.path == '/v1/systemone'
    assert seen[0].headers['authorization'] == 'Bearer fixture'
    assert seen[0].headers['user-agent'] == 'PresenceKit/1.0'
    assert set(json.loads(seen[0].content)) == {'model', 'state', 'questions'}


@pytest.mark.parametrize('mutate', [
    lambda b: b['answers']['action'].update(choice='inject'),
    lambda b: b['answers']['action'].update(confidence=float('nan')),
    lambda b: b['answers']['action'].update(confidence=True),
    lambda b: b['answers']['action'].update(probabilities={'none': .1, 'stop': .1}),
    lambda b: b['answers'].update(unexpected={}),
    lambda b: b['answers']['action'].update(type='score'),
])
def test_rejects_bad_answers(mutate):
    value = body()
    mutate(value)
    with pytest.raises(ValueError):
        dc.parse_response(request(), value)


def test_score_indices_are_scaled_and_noul_threshold_is_explicit():
    req = dc.DecisionRequest('test', '', {
        'score': dc.Question('score', 'score', ('low','medium','high'), scale=(0, 100)),
        'affection': dc.Question('noul', 'affection', threshold=.65),
    })
    result = dc.parse_response(req, {'model':'fixed', 'answers': {
        'score': {'type':'score','score':1.5,'probabilities':{'0':0,'1':.5,'2':.5},'confidence':.5},
        'affection': {'type':'noul','noul':.64},
    }})
    assert result.values == {'score':75,'affection':False}


def test_forbids_chat_default_and_native_fallback():
    mp = {'presets':{'text':{}, 'jev':{'api_protocol':'systemone'}},
        'routing_profiles':{'default':{'chat':'jev','sensor_judge':'jev'}},
        'fallback_routes':{'default':{'sensor_judge':'jev'}},'default_preset':'jev'}
    assert len(dc.validate_routes(mp)) == 3


@pytest.mark.asyncio
async def test_decision_native_failure_can_use_one_explicit_text_fallback(monkeypatch):
    llm_failover.clear_breakers()
    async def fail(req):
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        native = SimpleNamespace(name='native', api_protocol='systemone', client=http,
            base_url='https://api.example/v1', api_key='fixture', model='jev', provider_kind='openai')
        text = SimpleNamespace(name='text', api_protocol='chat_completions', prompt_style='narrative', model='small', provider_kind='openai')
        monkeypatch.setattr(llm_failover, 'resolve_fallback_client', lambda *a,**k:(text,''))
        calls=[]
        async def text_create(mc, *args, **kwargs):
            calls.append(mc.name)
            return SimpleNamespace(assistant_text='{"action":"stop"}')
        monkeypatch.setattr('core.llm_protocol.create', text_create)
        out = await llm_failover.execute_create(call_category='minecraft_reaction', caller='test', primary_mc=native,
            prepare=lambda target: dc.prepare(target,request(),messages=[{'role':'user','content':'choose'}],gen_kwargs={'timeout':3}))
    assert out.ok and out.switched and calls == ['text']


@pytest.mark.asyncio
async def test_native_auth_failure_never_implicitly_calls_chat(monkeypatch):
    llm_failover.clear_breakers()
    async def fail(req):
        return httpx.Response(401)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        native = SimpleNamespace(name='native', api_protocol='systemone', client=http,
            base_url='https://api.example/v1', api_key='fixture', model='jev', provider_kind='openai')
        monkeypatch.setattr(llm_failover, 'resolve_fallback_client', lambda *a,**k:(None,'not_configured'))
        out = await llm_failover.execute_create(call_category='sensor_judge', caller='test', primary_mc=native,
            prepare=lambda target: dc.prepare(target,request(),messages=[],gen_kwargs={'timeout':1}))
    assert not out.ok and out.error_category == 'upstream_auth_failed'


@pytest.mark.asyncio
async def test_business_emotion_affection_sensor_and_minecraft_use_native(monkeypatch):
    from core import llm_client, model_registry
    from core.scheduler import sensor_judge
    from core.activity import minecraft_reaction
    llm_failover.clear_breakers()
    calls=[]
    async def handle(req):
        payload=json.loads(req.content)
        calls.append(payload)
        answers={}
        for key,q in payload['questions'].items():
            if q['type']=='choice':
                selected = 'stop' if key=='action' else 'happy'
                answers[key]={'type':'choice','choice':selected,'confidence':.9,
                    'probabilities':{k:float(k==selected) for k in q['criteria']}}
            elif q['type']=='score':
                answers[key]={'type':'score','score':3,'confidence':.8,
                    'probabilities':{str(i):float(i==3) for i in range(len(q['criteria']))}}
            else:
                answers[key]={'type':'noul','noul':.9 if key=='affection' else .1}
        return httpx.Response(200,json={'model':'fixed','answers':answers,'usage':{}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        native=SimpleNamespace(name='native',api_protocol='systemone',client=http,base_url='https://api.example/v1',
            api_key='fixture',model='jev',provider_kind='openai',request_timeout_s=3)
        monkeypatch.setattr(llm_client,'get_model_client',lambda *a,**k:native)
        monkeypatch.setattr(model_registry,'get_model_client',lambda *a,**k:native)
        monkeypatch.setattr(sensor_judge,'get_model_client',lambda *a,**k:native)
        monkeypatch.setattr(llm_failover,'resolve_fallback_client',lambda *a,**k:(None,'not_configured'))
        assert await llm_client.detect_emotion('happy')=='happy'
        assert await llm_client.detect_affection('affection') is True
        assert (await sensor_judge.judge({'type':'test','context':{}}))['score']==75
        assert (await minecraft_reaction.judge('fixture',{},'stop')).action=='stop'
    assert len(calls)==4
    assert all(set(c)=={'state','questions','model'} for c in calls)


@pytest.mark.asyncio
async def test_chat_and_stream_refuse_native_without_http():
    from core import llm_protocol
    native=SimpleNamespace(api_protocol='systemone')
    with pytest.raises(ValueError):
        await llm_protocol.create(native,[],gen_kwargs={})
    with pytest.raises(ValueError):
        async for _ in llm_protocol.stream_text(native,[],gen_kwargs={}):
            pass
