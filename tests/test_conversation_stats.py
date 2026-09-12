from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import conversation_stats as stats
from admin.routers import chat_log


def test_usage_protocols_and_missing():
    assert stats.normalize_usage(None) == (None, None, None)
    assert stats.normalize_usage({'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25,
        'prompt_tokens_details': {'cached_tokens': 10}}) == (20, 5, 25)
    assert stats.normalize_usage({'input_tokens': 3, 'output_tokens': 2,
        'cache_read_input_tokens': 10, 'cache_creation_input_tokens': 4}) == (17, 2, 19)
    assert stats.normalize_usage({'input_tokens': -1, 'output_tokens': True}) == (None, None, None)


def test_read_does_not_create_store(sandbox):
    result = stats.query(date.today(), date.today(), uid='owner', char_id='role')
    assert result['days'][0]['chat_rounds'] is None
    assert not stats.get_paths().conversation_stats_db().exists()


def test_scope_dedupe_usage_and_calendar(sandbox):
    now = datetime.now().timestamp()
    for _ in range(2):
        stats.record('chat_round', uid='owner', char_id='role', event_id='same', ts=now)
    stats.record('chat_round', uid='someone_else', char_id='role')
    stats.record('chat_round', uid='owner', char_id='another_role')
    stats.record('tool_call', uid='owner', char_id='role')
    stats.record('image_view', uid='owner', char_id='role', count=3)
    stats.record('model_call', uid='owner', char_id='role', usage={'input_tokens': 10, 'output_tokens': 2})
    stats.record('model_call', uid='owner', char_id='role')
    stats.record('model_call', uid='owner', char_id='another_role', usage={'total_tokens': 999})
    stats.record('image_view', uid='owner', char_id='another_role', count=99)
    result = stats.query(date.today()-timedelta(days=1), date.today(), uid='owner', char_id='role')
    assert result['days'][0]['coverage'] == 'unavailable'
    today = result['days'][1]
    assert (today['chat_rounds'], today['tool_calls'], today['image_views']) == (1, 1, 3)
    assert today['total_tokens'] == 12
    assert today['usage_missing_calls'] == 1


@pytest.mark.asyncio
async def test_async_attribution_frozen_across_switch_and_parallel_calls(sandbox, monkeypatch):
    import asyncio
    from core import character_loader
    active = ['first']
    monkeypatch.setattr(character_loader, '_active_character_id', lambda: active[0])

    @stats.attributed
    async def call(uid, char_id=None):
        await asyncio.sleep(0)
        active[0] = 'switched'
        stats.record('model_call', usage={'total_tokens': 7})
        stats.record('image_view')

    await asyncio.gather(call('owner'), call('owner', char_id='second'))
    for character in ('first', 'second'):
        day = stats.query(date.today(), date.today(), uid='owner', char_id=character)['days'][0]
        assert day['total_tokens'] == 7
        assert day['image_views'] == 1
    stats.record('model_call', usage={'total_tokens': 1000})  # no scope: excluded
    assert stats.query(date.today(), date.today(), uid='owner', char_id='switched')['days'][0]['model_calls'] == 0


def test_idempotent_concurrent_writes(sandbox):
    stats.record('chat_round', uid='owner', char_id='role', event_id='unique')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: stats.record('chat_round', uid='owner', char_id='role', event_id='unique'), range(12)))
    assert stats.query(date.today(), date.today(), uid='owner', char_id='role')['days'][0]['chat_rounds'] == 1


@pytest.fixture
def client(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(chat_log, '_resolve_char_id', lambda value: 'role')
    monkeypatch.setattr(chat_log, 'resolve_path', lambda scope, key: tmp_path)
    monkeypatch.setattr(chat_log, '_owner_qq', lambda: 'owner')
    app = FastAPI()
    app.include_router(chat_log.router, prefix='/chat-log')
    for route in app.routes:
        for dependency in getattr(getattr(route, 'dependant', None), 'dependencies', []):
            app.dependency_overrides[dependency.call] = lambda: None
    return TestClient(app)


def test_calendar_ranges_validation_and_legacy(client, tmp_path):
    (tmp_path/'2024-02-29.md').write_text('## 12:00\n**用户**：hello\n**角色**：hi\n---\n', encoding='utf-8')
    response = client.get('/chat-log/stats/calendar?period=year&date=2024-02-29')
    assert response.status_code == 200
    body = response.json()
    assert len(body['days']) == 366
    assert body['days'][59]['chat_rounds'] == 1
    assert body['days'][59]['total_tokens'] is None
    assert client.get('/chat-log/stats/calendar?period=month&date=2024-02-01').json()['end'] == '2024-02-29'
    assert client.get('/chat-log/stats/calendar?period=week&date=2024-01-01').json()['end'] == '2024-01-07'
    for query in ('date=2024-02-30', 'start=2024-01-01', 'start=2024-02-01&end=2024-01-01',
                  'start=2020-01-01&end=2024-01-01', 'period=century', 'period=day&date=9999-12-31'):
        assert client.get('/chat-log/stats/calendar?' + query).status_code == 422


def test_calendar_scopes_and_character_switch_over_http(sandbox, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from admin import auth
    monkeypatch.setattr(chat_log, '_resolve_char_id', lambda value: value or 'first')
    monkeypatch.setattr(chat_log, '_owner_qq', lambda: 'owner')
    monkeypatch.setattr(chat_log, 'resolve_path', lambda scope, key: tmp_path / scope.character_id)
    scopes = {'memory.read'}
    monkeypatch.setattr(auth, 'resolve_token', lambda raw: SimpleNamespace(scopes=scopes, label='fixture'))
    monkeypatch.setattr(auth, '_is_rate_blocked', lambda ip: False)
    app = FastAPI()
    app.include_router(chat_log.router, prefix='/chat-log')
    client = TestClient(app)
    url = '/chat-log/stats/calendar?period=day'
    assert client.get(url).status_code == 401
    assert client.get(url, headers={'Authorization': 'Bearer fixture'}).status_code == 403
    scopes.add('state.read')
    for character, count in [('first', 2), ('second', 7)]:
        for kind in ['chat_round', 'tool_call', 'image_view']:
            stats.record(kind, uid='owner', char_id=character, count=count)
        stats.record('model_call', uid='owner', char_id=character, usage={'total_tokens': count*10})
    for character, count in [('first', 2), ('second', 7)]:
        body = client.get(url+'&char_id='+character, headers={'Authorization': 'Bearer fixture'}).json()
        day = body['days'][0]
        assert (day['chat_rounds'], day['tool_calls'], day['image_views'], day['total_tokens']) == (count,count,count,count*10)
