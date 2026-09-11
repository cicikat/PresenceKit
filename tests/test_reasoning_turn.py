import asyncio
from types import SimpleNamespace

import pytest

from core import llm_reasoning_store as store


async def capture(text):
    item = store.Capture(SimpleNamespace(name='example', model='example', api_protocol='chat_completions'))
    item.add('thinking', text)
    await item.save()


async def test_turn_correlation_is_concurrent_and_background_safe():
    @store.associate_owner_turn
    async def run(message, provenance_channel):
        await capture(message)
        await asyncio.wait_for(capture(message + '-retry'), timeout=5)
        store.finish_turn_capture()
        await asyncio.create_task(capture('background'))
        return {'turn_id': message}
    await asyncio.gather(run('first', 'desktop'), run('second', 'mobile'))
    for turn in ('first', 'second'):
        assert [entry['parts'][0]['text'] for entry in store.query_turn(turn)] == [turn, turn + '-retry']
    assert store.query_turn('unknown') == []
    assert len(store.query()) == 6


async def test_failed_and_integration_turns_are_not_exposed():
    @store.associate_owner_turn
    async def run(message, provenance_channel):
        await capture(message)
        if message == 'failed':
            raise ValueError('failure')
        return {'turn_id': message}
    with pytest.raises(ValueError):
        await run('failed', 'mobile')
    await run('external', 'integration')
    assert store.query_turn('failed') == []
    assert store.query_turn('external') == []


def test_old_archive_reads_without_migration(sandbox):
    import sqlite3
    path = sandbox.llm_reasoning_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE reasoning (seq INTEGER, call_id TEXT)')
    assert store.query_turn('old') == []


def test_client_reader_scope(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin import auth
    from admin.routers.observability import router
    monkeypatch.setattr(auth, 'resolve_token', lambda token: auth.TokenInfo('fixture', frozenset({token})))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get('/chat/turns/example/reasoning', headers={'Authorization': 'Bearer sensor.write'}).status_code == 403
    response = client.get('/chat/turns/example/reasoning', headers={'Authorization': 'Bearer memory.read'})
    assert response.status_code == 200
    assert response.json()['available'] is False
    assert client.get('/observability/llm-reasoning', headers={'Authorization': 'Bearer memory.read'}).status_code == 403
