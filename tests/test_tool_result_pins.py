import pytest
from core import tool_result_pins as pins
from core.tools.tool_result import ToolResult


@pytest.fixture(autouse=True)
def policy(monkeypatch):
    monkeypatch.setattr(pins,'_owner',lambda uid:uid=='u')
    monkeypatch.setattr(pins,'allowed',lambda *args:True)
    from core.tool_dispatcher import _TOOL_REGISTRY
    monkeypatch.setitem(_TOOL_REGISTRY,'fixture_pin',{'trace_result':True})


def candidate():
    return pins.retain('u','c','fixture_pin',ToolResult(raw_data='private raw',safe_summary='safe reference',meta={'validity':'current_turn'}))


@pytest.mark.asyncio
async def test_real_pin_handle_survives_autonomy_and_chat_clipping(sandbox, monkeypatch):
    from core import tool_dispatcher as dispatcher
    from core.tools.tool_result import to_tool_result

    async def action():
        return 'x' * 2000

    monkeypatch.setitem(dispatcher._TOOL_REGISTRY, 'fixture_pin', {
        'func': action, 'description': 'fixture', 'category': 'memory',
        'parameters': {}, 'trace_result': True,
    })
    monkeypatch.setattr(dispatcher, '_is_tool_enabled', lambda name: True)
    outcome = await dispatcher.execute_structured(
        'fixture_pin', {}, 'u', 'u', False, object(),
        origin='assistant_loop', char_id='c',
    )
    assert outcome.status == 'tool_executed'
    assert outcome.result.startswith('工具已执行：fixture_pin，')
    bounded = to_tool_result(outcome.result).safe_summary[:1400]
    identifier = bounded.split('result_id=', 1)[1][:32]
    assert len(identifier) == 32
    pins.pin('u', 'c', identifier, 2)
    assert pins.view('u', 'c')[0]['content'] == 'x' * 2000


def test_autonomy_reminds_reuse_and_only_exposed_pin_tool():
    from core.autonomy.runner import _system_prompt
    prompt = _system_prompt(talk_available=False, available_tool_names={'pin_tool_result'})
    assert 'Review recent and pinned tool results' in prompt
    assert 'result_id' in prompt and 'rounds=1..10' in prompt
    assert 'does not refresh' in prompt
    hidden = _system_prompt(talk_available=False, available_tool_names={'get_time'})
    assert 'pin_tool_result' not in hidden


def test_rounds_commit_once_and_skip_creation_round(sandbox):
    pins.reset_projection('u','c')
    identifier=candidate();pins.pin('u','c',identifier,2)
    pins.consume('u','c','creation')
    assert pins.view('u','c')[0]['remaining']==2
    messages=pins.messages('u','c')
    assert messages[0]['_layer']=='10.9_pinned_tool_results'
    assert 'private raw' not in messages[0]['content']
    pins.consume('u','c','next1');pins.consume('u','c','next1')
    assert pins.view('u','c')[0]['remaining']==1
    pins.messages('u','c');pins.consume('u','c','next2')
    assert pins.view('u','c')==[]


def test_scope_limit_revocation_cancel(sandbox,monkeypatch):
    identifiers=[candidate() for _ in range(4)]
    for identifier in identifiers[:3]:pins.pin('u','c',identifier,10)
    with pytest.raises(ValueError):pins.pin('u','c',identifiers[3],1)
    with pytest.raises(ValueError):pins.pin('u','other',identifiers[0],1)
    with pytest.raises(ValueError):pins.pin('u','c','fabricated',1)
    with pytest.raises(ValueError):pins.pin('u','c',identifiers[0],11)
    monkeypatch.setattr(pins,'allowed',lambda *args:False)
    assert pins.messages('u','c')==[]
    assert pins.unpin('u','c',identifiers[0])['cancelled']


def test_repin_revision_and_expiry(sandbox,monkeypatch):
    identifier=candidate();pins.pin('u','c',identifier,1)
    pins.messages('u','c');pins.pin('u','c',identifier,3)
    pins.consume('u','c','repin_turn')
    assert pins.view('u','c')[0]['remaining']==3
    now=pins.time.time()
    monkeypatch.setattr(pins.time,'time',lambda:now+86401)
    assert pins.messages('u','c')==[]
    with pytest.raises(ValueError):pins.pin('u','c',identifier,2)


def test_snapshot_round_is_source_isolated():
    from core.memory import source_policy
    assert source_policy.is_isolated('tool_pin')
