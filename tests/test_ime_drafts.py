import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import ime_drafts as store


def row(**changes):
    now = int(time.time() * 1000)
    return dict(id=1, created_at=now - 1000, updated_at=now, revision=1,
                app_package='example.editor', source='keyboard', content='示例*段', **{}) | changes


def test_revision_replaces_full_text_and_device_isolation():
    store.receive('phone-a', [row()])
    store.receive('phone-a', [row(revision=2, source='mixed', content='完整新版本')])
    store.receive('phone-a', [row(), row(revision=2, content='重复版本不覆盖')])
    store.receive('phone-b', [row(content='另一设备')])
    assert store.query(device_id='phone-a')[0]['content'] == '完整新版本'
    assert store.query(device_id='phone-a')[0]['source'] == 'mixed'
    assert len(store.query()) == 2


def test_retention_uses_updated_time_and_read_does_not_create(sandbox):
    assert store.query() == []
    assert not sandbox.ime_drafts_db().exists()
    now = int(time.time() * 1000)
    store.receive('phone', [row(created_at=now - 5 * 3600000)], now_ms=now)
    assert len(store.query(now_ms=now)) == 1
    assert store.query(now_ms=now + store.RETENTION_MS) == []
    store.receive('phone', [], now_ms=now + store.RETENTION_MS)
    assert store.query(now_ms=now) == []


def test_batch_rolls_back_on_storage_failure():
    store.receive('phone', [row()])
    with pytest.raises(KeyError):
        store.receive('phone', [row(revision=2, content='must roll back'), {'updated_at': int(time.time()*1000)}])
    assert store.query()[0]['revision'] == 1


@pytest.fixture
def client(monkeypatch):
    from admin import auth
    from admin.routers import ime_drafts as routes
    monkeypatch.setattr(auth, 'resolve_token', lambda token: auth.TokenInfo('phone-fixture', frozenset({token})))
    monkeypatch.setattr(routes, 'get_config', lambda: {'ime_ingest': {'enabled': True}})
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_atomic_ack_and_observation(client):
    headers = {'Authorization': 'Bearer sensor.write'}
    for _ in range(2):
        assert client.post('/v1/ime/drafts', headers=headers, json=[row()]).status_code == 204
    response = client.get('/observability/ime-drafts', headers={'Authorization': 'Bearer admin'})
    assert len(response.json()['entries']) == 1
    assert response.json()['mode'] == 'receive_only'


@pytest.mark.parametrize('payload', [{}, [row(source='unknown')], [row(revision=0)], [row(id=True)], [row(device_id='injected')]])
def test_invalid_batches_not_acknowledged(client, payload):
    response = client.post('/v1/ime/drafts', headers={'Authorization': 'Bearer sensor.write'}, json=payload)
    assert response.status_code == 422
    assert '示例' not in response.text
    assert store.query() == []


def test_disabled_and_wrong_scope_do_not_write(client, monkeypatch):
    assert client.post('/v1/ime/drafts', headers={'Authorization': 'Bearer chat'}, json=[row()]).status_code == 403
    monkeypatch.setattr('admin.routers.ime_drafts.get_config', lambda: {})
    assert client.post('/v1/ime/drafts', headers={'Authorization': 'Bearer sensor.write'}, json=[row()]).status_code == 503
    assert store.query() == []
