import asyncio

import pytest

from core import qzone_service as service
from tests.fixtures.public_assets import TEST_CHAR_ID


@pytest.fixture
def paced(monkeypatch):
    cfg = {'scheduler': {'owner_id': 'test_owner'}, 'qzone': {
        'enabled': True, 'write_enabled': True, 'account_id': '10001',
        'allowed_char_ids': [TEST_CHAR_ID]}}
    monkeypatch.setattr(service, 'get_config', lambda: cfg)
    monkeypatch.setattr(service, '_request_lock', asyncio.Lock())
    monkeypatch.setattr(service, '_write_lock', asyncio.Lock())
    monkeypatch.setattr(service, '_pacing', {'last_request': 0.0, 'write_until': 0.0,
                        'backoff_until': 0.0, 'failures': 0, 'requests': 0, 'limited': 0})
    # Virtual clock keeps interval tests independent from wall time.
    clock = [1000.0]
    monkeypatch.setattr(service, '_now', lambda: clock[0])
    async def sleep(seconds):
        clock[0] += seconds
    monkeypatch.setattr(service, '_sleep', sleep)
    calls = []
    async def request(url, token, action, params):
        calls.append((action, clock[0]))
        return {'ok': True, 'data': {'user_id': '10001'}}
    monkeypatch.setattr(service, 'request', request)
    return cfg, clock, calls


@pytest.mark.asyncio
async def test_concurrent_reads_share_spacing_and_write_cooldown(paced):
    _, _, calls = paced
    await asyncio.gather(*(service.call('get_emotion_list', {}, uid='test_owner', char_id=TEST_CHAR_ID)
                           for _ in range(3)))
    assert len(calls) == 6
    assert all(b[1] - a[1] >= 3 for a, b in zip(calls, calls[1:]))
    result = await service.call('send_like', {}, uid='test_owner', char_id=TEST_CHAR_ID)
    assert result['ok']
    before = len(calls)
    result = await service.call('send_comment', {}, uid='test_owner', char_id=TEST_CHAR_ID)
    assert result['code'] == 'qzone_write_cooldown' and len(calls) == before
    assert service.snapshot()['pacing']['limited'] == 1


@pytest.mark.asyncio
async def test_unknown_write_backoff_no_retry_and_exponential_growth(paced, monkeypatch):
    _, clock, calls = paced
    async def request(url, token, action, params):
        calls.append((action, clock[0]))
        return {'ok': True, 'data': {'user_id': '10001'}} if action == 'get_login_info' else {'ok': False, 'code': 'outcome_unknown'}
    monkeypatch.setattr(service, 'request', request)
    result = await service.call('send_comment', {}, uid='test_owner', char_id=TEST_CHAR_ID)
    assert result['code'] == 'outcome_unknown'
    assert [c[0] for c in calls] == ['get_login_info', 'send_comment']
    assert service.snapshot()['pacing']['backoff_remaining_seconds'] == 60
    before = len(calls)
    assert (await service.call('get_emotion_list', {}, uid='test_owner', char_id=TEST_CHAR_ID))['code'] == 'qzone_backoff'
    assert len(calls) == before
    clock[0] += 61
    await service.call('get_emotion_list', {}, uid='test_owner', char_id=TEST_CHAR_ID)
    assert service.snapshot()['pacing']['backoff_remaining_seconds'] == 120


@pytest.mark.asyncio
async def test_hot_revoke_while_waiting_prevents_request(paced, monkeypatch):
    cfg, clock, calls = paced
    await service.paced_request(service.settings(), 'get_login_info', {})
    async def sleep(seconds):
        clock[0] += seconds
        cfg['qzone']['enabled'] = False
    monkeypatch.setattr(service, '_sleep', sleep)
    result = await service.paced_request(service.settings(), 'get_emotion_list', {})
    assert result['code'] == 'qzone_settings_changed' and len(calls) == 1


@pytest.mark.asyncio
async def test_owner_change_during_spacing_revokes_business_call(paced, monkeypatch):
    cfg, clock, calls = paced
    async def sleep(seconds):
        clock[0] += seconds
        cfg['scheduler']['owner_id'] = 'other_owner'
    monkeypatch.setattr(service, '_sleep', sleep)
    result = await service.call('get_emotion_list', {}, uid='test_owner', char_id=TEST_CHAR_ID)
    assert result['code'] == 'qzone_not_authorized'
    assert [action for action, _ in calls] == ['get_login_info']


@pytest.mark.asyncio
async def test_explicit_login_can_recover_during_failure_backoff(paced):
    _, clock, calls = paced
    service._pacing.update(backoff_until=clock[0] + 120, failures=2)
    cfg = service.settings()
    assert (await service.paced_request(cfg, 'get_emotion_list', {}))['code'] == 'qzone_backoff'
    assert not calls
    assert (await service.paced_request(cfg, 'login_cookie', {'cookie': 'test-cookie'}))['ok']
    assert service._pacing['backoff_until'] == 0
    assert service._pacing['failures'] == 0
    assert (await service.paced_request(cfg, 'get_emotion_list', {}))['ok']
    assert calls[1][1] - calls[0][1] >= cfg.request_interval_seconds
