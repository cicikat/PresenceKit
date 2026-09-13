import json
import time
from unittest.mock import AsyncMock

import pytest

from core import context_continuity as continuity, life_records, character_document_library as library


@pytest.fixture
def configured(sandbox, monkeypatch):
    from core import config_loader, tool_dispatcher
    config = {'scheduler': {'owner_id': 'owner-test'}, 'life_records': {'enabled': True, 'character_readable': True}}
    for module in (config_loader, life_records, tool_dispatcher):
        monkeypatch.setattr(module, 'get_config', lambda: config)
    monkeypatch.setattr(tool_dispatcher, 'get_tools_schema', lambda **kw: [
        {'function': {'name': name}} for name in ('read_document', 'reread_image', 'read_life_records')])
    monkeypatch.setattr(tool_dispatcher, '_is_tool_enabled', lambda _: True)
    monkeypatch.setattr('core.self_management.policy.tool_allowed', lambda *args: True)
    return config


def record(identity='record-a', operation='operation-a', revision=0, title='lunch'):
    return {'record_id': identity, 'operation_id': operation, 'action': 'upsert', 'base_revision': revision,
            'record': {'category': 'diet', 'occurred_on': '2026-09-13', 'title': title, 'items': [], 'user_edited_fields': []}}


def pending(messages):
    return [m for m in messages if m['_layer'] == '10.6_pending_material']


def test_ready_record_stays_pending_until_evaluated_then_becomes_reference(configured):
    life_records.sync('owner-test', 'device', record())
    first = continuity.messages('owner-test', 'char-test')
    assert 'lunch' in str(first) and len(pending(first)) == 1
    assert len(pending(continuity.messages('owner-test', 'char-test'))) == 1
    continuity.acknowledge(first)  # success, including a silent model response
    later = continuity.messages('owner-test', 'char-test')
    assert not pending(later) and 'lunch' in str(later)
    assert len(pending(continuity.messages('owner-test', 'other-char'))) == 1
    assert continuity.messages('other-owner', 'char-test') == []
    state = continuity.observability('owner-test', 'char-test')
    assert state['receipts'] and 'lunch' not in json.dumps(state)


def test_latest_revision_deletion_and_permission_revocation(configured):
    life_records.sync('owner-test', 'device', record())
    old = continuity.messages('owner-test', 'char-test')
    life_records.sync('owner-test', 'device', record(operation='edit', revision=1, title='corrected'))
    continuity.acknowledge(old)
    current = continuity.messages('owner-test', 'char-test')
    assert pending(current) and 'corrected' in str(current) and 'lunch' not in str(current)
    continuity.acknowledge(current)
    configured['life_records']['character_readable'] = False
    assert continuity.messages('owner-test', 'char-test') == []
    configured['life_records']['character_readable'] = True
    life_records.sync('owner-test', 'device', {'record_id': 'record-a', 'operation_id': 'delete', 'action': 'delete', 'base_revision': 2})
    assert continuity.messages('owner-test', 'char-test') == []


def test_pending_recognition_waits_and_receipt_does_not_invent_content(configured):
    body = record()
    body['image_mime'] = 'image/png'
    life_records.sync('owner-test', 'device', body, image=b'fixture')
    assert continuity.messages('owner-test', 'char-test') == []
    job = life_records.claim()
    life_records.finish(job, {'recognition_description': 'noodles'})
    assert 'noodles' in str(pending(continuity.messages('owner-test', 'char-test')))


def test_upload_receipt_trimming_and_tombstone(configured):
    doc = library.store_upload(uid='owner-test', char_id='char-test', filename='notes.txt', media_type='text/plain',
                               sha256='a' * 64, searchable_text='chapter detail', source='upload_file')
    msg = continuity.messages('owner-test', 'char-test')
    continuity.acknowledge([])  # trimmed/ablated context must not be marked seen
    assert pending(continuity.messages('owner-test', 'char-test'))
    continuity.acknowledge(msg)
    assert not pending(continuity.messages('owner-test', 'char-test'))
    assert library.delete('owner-test', 'char-test', doc)
    assert continuity.messages('owner-test', 'char-test') == []


def test_tool_results_survive_silence_are_bounded_and_do_not_leak_raw(configured):
    from core.tools.tool_result import ToolResult
    for i in range(20):
        continuity.retain_result('owner-test', 'char-test', 'weather', ToolResult(raw_data='SECRET', safe_summary=f'observed rain {i}'))
    restored = continuity.messages('owner-test', 'char-test')
    assert len(restored) == 3 and 'observed rain 19' in str(restored)
    assert 'SECRET' not in str(restored) and '历史结果' in str(restored)
    assert continuity.messages('owner-test', 'other-char') == []
    assert len(continuity.observability('owner-test', 'char-test')['tool_results']) == 12
    assert continuity.messages('owner-test', 'char-test', now=time.time() + 25 * 3600) == []
    configured['action_trace'] = {'enabled': False}
    assert continuity.messages('owner-test', 'char-test') == []


def test_screen_success_only_and_revocation(configured, monkeypatch):
    from core.perception import screen_observation
    monkeypatch.setattr(screen_observation, 'enabled', lambda: True)
    continuity.retain_result('owner-test', 'char-test', 'observe_user_screen', '{"status":"sensitive"}')
    assert continuity.messages('owner-test', 'char-test') == []
    continuity.retain_result('owner-test', 'char-test', 'observe_user_screen', '{"status":"ok","caption":"spreadsheet"}')
    assert 'spreadsheet' in str(continuity.messages('owner-test', 'char-test'))
    monkeypatch.setattr(screen_observation, 'enabled', lambda: False)
    assert continuity.messages('owner-test', 'char-test') == []


@pytest.mark.asyncio
async def test_life_tool_reads_detail_in_pages(configured):
    from core.tool_dispatcher import _life_records_wrapper
    body = record()
    body['record']['note'] = 'abc' * 1200 + 'tail detail'
    life_records.sync('owner-test', 'device', body)
    index = await _life_records_wrapper('owner-test', char_id='char-test')
    assert 'record-a' in index.safe_summary
    assert pending(continuity.messages('owner-test', 'char-test'))  # index alone is not a detail read
    detail = await _life_records_wrapper('owner-test', record_id='record-a', char_id='char-test')
    assert 'next_offset=1400' in detail.safe_summary
    assert pending(continuity.messages('owner-test', 'char-test'))
    continuity.acknowledge([{'_continuity_receipt': detail.meta['continuity_receipt']}])
    assert not pending(continuity.messages('owner-test', 'char-test'))
    tail = await _life_records_wrapper('owner-test', record_id='record-a', offset=2800, char_id='char-test')
    assert 'tail detail' in tail.safe_summary and len(tail.safe_summary) < 2000


def test_autonomy_context_includes_pending_and_previous_result(configured):
    from core.autonomy.runner import _context_messages
    life_records.sync('owner-test', 'device', record())
    continuity.retain_result('owner-test', 'char-test', 'weather', 'observed rain')
    context = _context_messages('owner-test', 'char-test')
    assert pending(context) and 'observed rain' in str(context)
    assert pending(continuity.messages('owner-test', 'char-test'))


@pytest.mark.asyncio
async def test_failed_generation_does_not_consume_pending(configured, monkeypatch):
    from core.pipeline import Pipeline
    from core import llm_client, error_handler
    monkeypatch.setattr(error_handler, 'with_retry', lambda **kw: lambda fn: fn)
    monkeypatch.setattr(llm_client, 'chat', AsyncMock(side_effect=RuntimeError('fixture failure')))
    life_records.sync('owner-test', 'device', record())
    pipeline = object.__new__(Pipeline)
    with pytest.raises(RuntimeError):
        await pipeline.run_llm(continuity.messages('owner-test', 'char-test'), char_id='char-test')
    assert pending(continuity.messages('owner-test', 'char-test'))


@pytest.mark.asyncio
async def test_real_dispatcher_silent_autonomy_keeps_screen_result(configured, monkeypatch):
    from types import SimpleNamespace
    from core import tool_dispatcher, llm_client
    from core.autonomy import runner, store
    from core.autonomy.models import Job, Run
    from core.perception import screen_observation
    life_records.sync('owner-test', 'device', record())
    monkeypatch.setattr(screen_observation, 'enabled', lambda: True)
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY['observe_user_screen'], 'func', AsyncMock(return_value='{"status":"ok","caption":"fixture spreadsheet"}'))
    monkeypatch.setattr(runner, '_user_became_active', lambda _: False)
    monkeypatch.setattr(runner, '_runtime_tools', lambda *args: ([{'type': 'function', 'function': {'name': 'observe_user_screen', 'parameters': {'type': 'object', 'properties': {}}}}], None))
    monkeypatch.setattr(runner.talk_gate, 'check', lambda *args, **kw: ('hard', 'fixture-silent'))
    monkeypatch.setattr(runner, '_autonomy_still_enabled', lambda *args: True)
    turns = iter([
        SimpleNamespace(tool_calls=[{'id': 'fixture-call', 'name': 'observe_user_screen', 'arguments': {}}], continuation_items=[], assistant_message={}),
        SimpleNamespace(tool_calls=[], continuation_items=[], assistant_message={}),
    ])
    async def chat(*args, **kwargs):
        return next(turns)
    monkeypatch.setattr(llm_client, 'chat_turn', chat)
    state = store.load('owner-test', 'char-test')
    state['config']['enabled'] = True
    run = await runner._run_locked(Job(uid='owner-test', char_id='char-test', source='manual'), state,
                                   Run(uid='owner-test', char_id='char-test', source='manual', job_id='fixture-job'))
    assert run.disposition == 'completed_tools_only' and not run.talk_sent
    later = continuity.messages('owner-test', 'char-test')
    assert not pending(later)
    assert 'fixture spreadsheet' in str(later)
    assert 'lunch' in str(later)


def test_observability_requires_scope_and_hides_content(configured, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin.auth import TokenInfo
    from admin.routers.character_library import router
    monkeypatch.setattr('admin.auth.resolve_token', lambda token: TokenInfo('fixture', frozenset({token})))
    continuity.retain_result('owner-test', 'char-test', 'weather', 'private weather detail')
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    url = '/observability/context-continuity?uid=owner-test&char_id=char-test'
    assert client.get(url, headers={'Authorization': 'Bearer chat'}).status_code == 403
    response = client.get(url, headers={'Authorization': 'Bearer state.read'})
    assert response.status_code == 200 and response.json()['tool_results']
    assert 'private weather detail' not in response.text
