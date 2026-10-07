import pytest
from core import quote_notebook as store
from core.memory import event_store
from core.memory.scope import MemoryScope


def seed():
    scope=MemoryScope.reality_scope('u','c')
    for actor in ('user','assistant'):
        event_store.append_event(scope,dict(event_id='t:'+actor,turn_id='t',actor=actor,kind=actor+'_message',
            occurred_at=1700000000,visible_text='原话'+actor,source='user_chat'))
    return scope


def test_both_quotes_and_immutable_snapshot_notes(sandbox):
    seed()
    first=store.save('u','c','t:user','初始笔记',['t:assistant'])
    assert first['created']
    assert not store.save('u','c','t:user','不得覆盖')['created']
    assert store.save('u','c','t:assistant')['created']
    store.update_note('u','c',first['quote_id'],'新笔记')
    detail=store.read('u','c',first['quote_id'])
    assert detail['text']=='原话user' and detail['message_ts']==1700000000
    assert detail['note']=='新笔记' and detail['note_changes'][0]['note']=='初始笔记'
    assert detail['related_memories'][0]['event_id']=='t:assistant'
    assert store.list_quotes('u','c','新笔记')['total']==1
    assert store.list_quotes('u','other')['total']==0


def test_source_loss_preserves_collection_and_user_can_delete(sandbox):
    scope=seed()
    quote_id=store.save('u','c','t:assistant')['quote_id']
    event_store.tombstone_event(scope,'t:assistant')
    assert store.read('u','c',quote_id)['source_available'] is False
    with pytest.raises(ValueError): store.save('u','c','t:assistant')
    assert store.delete('u','c',quote_id)['deleted']
    with pytest.raises(ValueError): store.read('u','c',quote_id)


def test_scope_and_limits(sandbox):
    seed()
    with pytest.raises(ValueError): store.save('other','c','t:user')
    with pytest.raises(ValueError): store.save('u','c','t:user',related_event_ids=['missing'])
    with pytest.raises(ValueError): store.save('u','c','t:user',note='x'*2001)
    with pytest.raises(ValueError): store.list_quotes('u','c',limit=51)


def test_admin_read_and_edit_scopes(sandbox,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin import auth
    from admin.routers import quote_notebook as routes
    seed()
    quote_id=store.save('u','c','t:user')['quote_id']
    monkeypatch.setattr(routes,'scope',lambda char_id:('u',char_id))
    monkeypatch.setattr(auth,'resolve_token',lambda token:auth.TokenInfo('fixture',frozenset({token})))
    app=FastAPI();app.include_router(routes.router);client=TestClient(app)
    assert client.get('/observability/quotes?char_id=c',headers={'Authorization':'Bearer state.read'}).status_code==403
    assert client.get('/observability/quotes?char_id=c',headers={'Authorization':'Bearer memory.read'}).json()['total']==1
    assert client.put(f'/settings/quotes/{quote_id}/note?char_id=c',json={'note':'new'},headers={'Authorization':'Bearer memory.read'}).status_code==403
    assert client.put(f'/settings/quotes/{quote_id}/note?char_id=c',json={'note':'new'},headers={'Authorization':'Bearer admin'}).status_code==200
    assert client.get(f'/observability/quotes/{quote_id}?char_id=other',headers={'Authorization':'Bearer memory.read'}).status_code==404
