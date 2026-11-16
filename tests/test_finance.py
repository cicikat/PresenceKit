"""Mounted HTTP regressions: authorization, decimal integrity, atomic receipts and edits."""
import copy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core import finance as store
from admin.routers import finance as routes


@pytest.fixture
def client(monkeypatch, sandbox):
    from admin.auth import TokenInfo
    config = {'scheduler': {'owner_id': 'owner'}, 'finance_ingest': {'enabled': True, 'character_readable': False}}
    monkeypatch.setattr(routes, 'get_config', lambda: config)
    monkeypatch.setattr(store, 'get_config', lambda: config)
    monkeypatch.setattr('admin.auth.resolve_token', lambda token: TokenInfo('fixture', frozenset({token})))
    app = FastAPI(); app.include_router(routes.router)
    with TestClient(app) as client:
        client.config = config
        yield client


def body():
    return {'operation_id': 'op-a', 'candidate_id': 'candidate-a', 'base_revision': 0, 'record': {
        'candidate_id': 'candidate-a', 'source_app': 'com.tencent.mm', 'source_event_key': 'a' * 64,
        'fingerprint': 'b' * 64, 'occurred_at': 1794801600000, 'observed_at': 1794801601000,
        'timezone_offset_minutes': 480, 'amount_minor': 1600, 'currency': 'CNY', 'merchant': 'Fixture store',
        'category': None, 'confidence': 'medium', 'reason_codes': ['rule_v1', 'payment_signal'],
        'direction': 'expense', 'status': 'candidate'}}


HEADERS = {'Authorization': 'Bearer finance_ingest'}
def post(client, payload):
    return client.post('/finance/candidates/sync', json=payload, headers=HEADERS)


def test_shared_mobile_fixture_and_backup_inventory(client, sandbox):
    import json
    from pathlib import Path
    from core import backup_state
    fixture = json.loads((Path(__file__).parent / 'fixtures/finance-v1.json').read_text(encoding='utf-8'))
    receipt = post(client, fixture['operation'])
    assert receipt.status_code == 200
    assert receipt.json()['record']['amount_minor'] == 1600
    # The owning protection selector must include ledger and journal, not treat them as cache.
    assert not backup_state._is_excluded_data_path(backup_state.PurePosixPath('data/runtime/finance/ledger.sqlite3'))
    assert sandbox.finance_db().exists()


def test_http_idempotent_timeout_edit_history_and_tombstone(client):
    payload = body(); first = post(client, payload)
    assert first.status_code == 200, first.text
    assert post(client, payload).json() == first.json()
    payload.update(operation_id='op-confirm', base_revision=1)
    payload['record']['status'] = 'confirmed'
    confirmed = post(client, payload).json()
    transaction = confirmed['record']['linked_transaction_id']
    assert transaction
    payload.update(operation_id='op-edit', base_revision=2)
    payload['record'].update(amount_minor=1900, merchant='Corrected')
    corrected = post(client, payload)
    assert corrected.status_code == 200
    assert corrected.json()['record']['linked_transaction_id'] == transaction
    assert post(client, body()).json() == first.json()  # old receipt never mutates
    stale = copy.deepcopy(payload); stale['operation_id'] = 'stale'
    assert post(client, stale).status_code == 409
    payload.update(operation_id='op-delete', base_revision=3)
    payload['record']['status'] = 'deleted'
    assert post(client, payload).status_code == 200
    result = client.get('/finance/candidates/candidate-a', headers=HEADERS).json()
    assert result['record']['status'] == 'deleted'
    assert [r['revision'] for r in result['history']] == [4, 3, 2, 1]
    assert store.listing('owner', confirmed_only=True)['records'] == []
    assert store.observe('owner')['operation_count'] == 4


def test_auth_owner_scope_and_raw_notifications_denied(client):
    assert client.get('/finance/candidates').status_code == 401
    assert client.get('/finance/candidates', headers={'Authorization': 'Bearer chat'}).status_code == 403
    payload = body(); payload['owner_id'] = 'other'
    assert post(client, payload).status_code == 422
    payload = body(); payload['record']['raw_text'] = 'private notification'
    assert post(client, payload).status_code == 422
    assert store.observe('owner')['operation_count'] == 0
    assert client.get('/settings/finance', headers=HEADERS).status_code == 403
    client.config['finance_ingest']['enabled'] = False
    assert client.get('/finance/capabilities', headers=HEADERS).json()['effective'] is False
    assert post(client, body()).status_code == 503
    assert client.get('/finance/candidates', headers=HEADERS).status_code == 503


@pytest.mark.parametrize('field,value', [('amount_minor', 16.01), ('amount_minor', True), ('amount_minor', -1), ('currency', 'USD'), ('direction', 'bad')])
def test_no_float_money_or_invalid_confirmation(client, field, value):
    payload = body(); payload['record'].update(status='confirmed'); payload['record'][field] = value
    assert post(client, payload).status_code == 422
    assert store.observe('owner')['operation_count'] == 0


def test_duplicate_event_operation_collision_and_parallel_receipt(client):
    payload = body()
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: post(client, payload), range(4)))
    assert all(r.status_code == 200 for r in replies)
    assert all(r.json() == replies[0].json() for r in replies)
    assert len(client.get('/finance/candidates', headers=HEADERS).json()['records']) == 1
    collision = body(); collision['record']['amount_minor'] = 1700
    assert post(client, collision).status_code == 409
    collision.update(candidate_id='candidate-b', operation_id='op-b')
    collision['record']['candidate_id'] = 'candidate-b'
    assert post(client, collision).status_code == 409
    assert store.observe('owner')['operation_count'] == 1
    assert store.listing('different-owner')['records'] == []


@pytest.mark.asyncio
async def test_character_read_gate_and_refunds(client, monkeypatch):
    from core.tools import finance_tools
    monkeypatch.setattr(finance_tools, 'get_config', lambda: client.config)
    payload = body(); payload['record'].update(status='confirmed', direction='refund')
    assert post(client, payload).status_code == 200
    call = SimpleNamespace(user_id='owner', tool_args={}, is_group=False, origin='assistant_loop')
    denied = await finance_tools.invoke(call)
    assert denied.meta['failure_reason'] == 'permission_denied'
    client.config['finance_ingest']['character_readable'] = True
    allowed = await finance_tools.invoke(call)
    assert 'refund' in allowed.raw_data
    assert '1600' in allowed.raw_data
    call.is_group = True
    assert (await finance_tools.invoke(call)).meta['failure_reason'] == 'permission_denied'
    call.is_group = False
    call.user_id = 'other'
    assert (await finance_tools.invoke(call)).meta['failure_reason'] == 'permission_denied'
