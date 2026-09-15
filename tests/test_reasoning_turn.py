import asyncio
from types import SimpleNamespace

import pytest

from core import llm_reasoning_store as store


async def capture(text):
    item = store.Capture(SimpleNamespace(name='example', model='example', api_protocol='chat_completions'))
    item.add('thinking', text)
    await item.save()


async def test_monologue_and_probe_reasoning_are_not_bound_to_owner_turns():
    @store.associate_owner_turn
    async def run(message, provenance_channel):
        await capture(message)
        token = store.set_capture_purpose("monologue")
        try:
            await capture("monologue-private")
            await store.archive_text("monologue", "inner voice")
        finally:
            store.reset_capture_purpose(token)
        token = store.set_capture_purpose("probe")
        try:
            await capture("probe-private")
        finally:
            store.reset_capture_purpose(token)
        store.finish_turn_capture()
        return {"turn_id": message}

    await run("owner", "desktop")
    bound = store.query_turn("owner")
    assert [entry["parts"][0]["text"] for entry in bound] == ["inner voice", "owner"]
    assert {entry.get("purpose") or "chat" for entry in bound} <= {"chat", "monologue"}
    archived = [row["call_id"] for row in store.query()]
    assert len(archived) == 4


async def test_legacy_rows_without_purpose_still_join_owner_turns(sandbox):
    import sqlite3

    path = sandbox.llm_reasoning_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE reasoning ("
            "seq INTEGER PRIMARY KEY, call_id TEXT, created_at REAL, preset TEXT, "
            "model TEXT, protocol TEXT, status TEXT, reasoning_chars INTEGER, "
            "parts TEXT, turn_id TEXT DEFAULT '')"
        )
        db.execute(
            "INSERT INTO reasoning "
            "(call_id, created_at, preset, model, protocol, status, reasoning_chars, parts, turn_id) "
            "VALUES ('legacy', 1, 'p', 'm', 'chat_completions', 'completed', 4, ?, 'legacy-turn')",
            ('[{"source":"thinking","text":"old"}]',),
        )
    rows = store.query_turn("legacy-turn")
    assert rows[0]["parts"][0]["text"] == "old"


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


@pytest.mark.asyncio
async def test_chat_turn_reasoning_prefers_monologue_by_display_setting(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin import auth
    from admin.routers.observability import router
    from core import thinking

    @store.associate_owner_turn
    async def run(message, provenance_channel):
        native = store.Capture(SimpleNamespace(name='example', model='example', api_protocol='chat_completions'))
        native.add('thinking', 'native later')
        await native.save()
        await store.archive_text('monologue', 'inner voice')
        return {'turn_id': 'owner'}

    await run('hi', 'desktop')
    monkeypatch.setattr(auth, 'resolve_token', lambda token: auth.TokenInfo('fixture', frozenset({token})))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    monkeypatch.setattr(thinking, 'display_prefer_monologue', lambda: True)
    preferred = client.get('/chat/turns/owner/reasoning', headers={'Authorization': 'Bearer memory.read'})
    assert [entry['parts'][0]['text'] for entry in preferred.json()['entries']] == ['inner voice', 'native later']
    monkeypatch.setattr(thinking, 'display_prefer_monologue', lambda: False)
    native_first = client.get('/chat/turns/owner/reasoning', headers={'Authorization': 'Bearer memory.read'})
    assert [entry['parts'][0]['text'] for entry in native_first.json()['entries']] == ['native later', 'inner voice']
