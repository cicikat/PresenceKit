"""Default tool permission has no mode or per-call confirmation gate."""
import pytest
from core import tool_dispatcher as td
from tests.fixtures.public_assets import TEST_CHAR_ID


@pytest.mark.asyncio
@pytest.mark.parametrize('category', ['desktop', 'system', 'phone_control', 'fs'])
async def test_default_permission_executes_without_confirmation(sandbox, monkeypatch, category):
    calls = []

    async def action(**kwargs):
        calls.append(kwargs)
        return 'executed'

    monkeypatch.setitem(td._TOOL_REGISTRY, '_permission_test', {
        'func': action, 'description': 'test', 'category': category,
        'dangerous': True, 'parameters': {},
    })
    monkeypatch.setattr(td, '_is_tool_enabled', lambda name: True)
    outcome = await td.execute_structured(
        '_permission_test', {}, 'u1', 'u1', False, object(),
        origin='user_live', char_id=TEST_CHAR_ID,
    )
    assert outcome.status == 'tool_executed'
    assert outcome.confirmation_request is None
    assert calls == [{}]


@pytest.mark.asyncio
async def test_explicit_character_denial_still_blocks(sandbox, monkeypatch):
    async def action(**kwargs):
        pytest.fail('denied tool executed')

    monkeypatch.setitem(td._TOOL_REGISTRY, '_permission_test', {
        'func': action, 'description': 'test', 'category': 'system',
        'dangerous': True, 'parameters': {},
    })
    monkeypatch.setattr('core.self_management.policy.tool_allowed', lambda *args: False)
    outcome = await td.execute_structured(
        '_permission_test', {}, 'u1', 'u1', False, object(),
        origin='user_live', char_id=TEST_CHAR_ID,
    )
    assert outcome.status == 'tool_failed'
    assert outcome.confirmation_request is None
