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
