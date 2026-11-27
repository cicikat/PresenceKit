"""C: exact live history withdrawal, fail-closed recovery and conflict-safe undo."""
import json
from datetime import datetime
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core import conversation_control as control, conversation_recovery as recovery
from core.memory.scope import MemoryScope
from core.memory.path_resolver import resolve_path
from core.safe_write import safe_write_json


@pytest.fixture
def seeded(sandbox):
    from core.memory import event_store,short_term,event_log
    from core.mobile_realtime import remember_final
    scope=MemoryScope.reality_scope('owner_fixture','character_fixture')
    now=time.time();turn='turn_fixture';other='other_fixture'
    entries=[]
    for tid in (turn,other):
        for actor in ('user','assistant'):
            content='same text' if actor=='user' else 'same answer'
            result=event_store.append_event(scope,event_store.EventRecord(event_id=tid+':'+actor,turn_id=tid,occurred_at=now,
                actor=actor,kind='user_message' if actor=='user' else 'assistant_message',raw_text=content,visible_text=content,memory_text=content))
            assert result.ok
            entries.append({'role':actor,'content':content,'_turn_id':tid})
        remember_final(uid=scope.uid,char_id=scope.character_id,turn_id=tid,request_id=tid+'_request',content='same answer',display_text='same answer')
    safe_write_json(short_term._history_write_path(scope.uid,char_id=scope.character_id),entries)
    root=event_log._event_log_read_dir(scope.uid,char_id=scope.character_id);root.mkdir(parents=True,exist_ok=True)
    day=datetime.fromtimestamp(now).strftime('%Y-%m-%d')
    log=root/(day+'.md')
    log.write_text('\n'.join(f'## 12:00\n**用户**：same text\n> speaker:user turn_id:{tid}\n**Fixture**：same answer\n> emotion:neutral speaker:assistant turn_id:{tid}\n---\n' for tid in (turn,other)),encoding='utf-8')
    safe_write_json(resolve_path(scope,'mid_term'),{'events':[{'mid_id':'mid_fixture','summary':'derived','source_turn_id':turn,'source_event_ids':[turn+':user']}]})
    safe_write_json(resolve_path(scope,'episodic'),[{'id':'ep_fixture','raw_facts':['derived'],'status':'open','source_event_ids':[turn+':user',other+':user']}])
    return scope,turn,other,day


@pytest.mark.asyncio
async def test_exact_delete_restore_keeps_other_identical_turn_and_new_memory(seeded):
    """IDs, not equal prose, select a round; restore cannot replace later data."""
    from core.memory import short_term,event_log,event_query
    from core.mobile_realtime import history
    scope,turn,other,day=seeded
    result=await recovery.delete(scope,turn_id=turn,operation_id='delete_fixture',label='mobile_fixture')
    assert result['state']=='completed'
    assert control.snapshot(scope)['withdrawn_requests']==[turn+'_request']
    assert {item['_turn_id'] for item in short_term.load(scope.uid,char_id=scope.character_id)}=={other}
    assert 'turn_id:'+turn not in event_log.get_recent_days(scope.uid,days=1,char_id=scope.character_id)
    assert event_query.get_event(scope,turn+':assistant',include_isolated=True)['tombstoned']
    assert [row['turn_id'] for row in history(scope)['messages']]==[other]
    assert recovery.blocked(scope,'ep_fixture')
    mid=resolve_path(scope,'mid_term');data=json.loads(mid.read_text(encoding='utf-8'))
    data['events'].append({'mid_id':'later_fixture','summary':'later independent'})
    safe_write_json(mid,data)
    restored=await recovery.restore(scope,deletion_id=result['deletion_id'],operation_id='restore_fixture',expected_revision=result['revision'])
    assert restored['state']=='restored'
    assert {item['_turn_id'] for item in short_term.load(scope.uid,char_id=scope.character_id)}=={turn,other}
    assert not event_query.get_event(scope,turn+':assistant',include_isolated=True)['tombstoned']
    assert {row['turn_id'] for row in history(scope)['messages']}=={turn,other}
    assert json.loads(mid.read_text(encoding='utf-8'))['events'][-1]['summary']=='later independent'
    assert 'invalidated' not in json.loads(mid.read_text(encoding='utf-8'))['events'][0]
    assert not recovery.blocked(scope,'ep_fixture')


@pytest.mark.asyncio
async def test_archive_failure_changes_nothing(seeded,monkeypatch):
    """A failed archive serialization must precede every destructive operation."""
    from core.memory import short_term,event_query
    scope,turn,other,day=seeded
    def unavailable(*args):
        raise OSError('archive unavailable')
    monkeypatch.setattr(recovery,'_dump',unavailable)
    with pytest.raises(OSError):
        await recovery.delete(scope,turn_id=turn,operation_id='delete_fail',label='mobile_fixture')
    assert not recovery.hidden(scope,turn)
    assert not event_query.get_event(scope,turn+':assistant',include_isolated=True)['tombstoned']
    assert {item['_turn_id'] for item in short_term.load(scope.uid,char_id=scope.character_id)}=={turn,other}


@pytest.mark.asyncio
async def test_partial_delete_is_hidden_and_retryable_not_false_success(seeded,monkeypatch):
    """Disk failure after the fence cannot expose raw history fallback; repair resumes."""
    from core.memory import short_term,event_query
    scope,turn,other,day=seeded
    with monkeypatch.context() as patch:
        patch.setattr(recovery,'_write',lambda *args:(_ for _ in ()).throw(OSError('disk failed')))
        result=await recovery.delete(scope,turn_id=turn,operation_id='delete_partial',label='mobile_fixture')
    assert result['state']=='repair_pending'
    assert {item['_turn_id'] for item in short_term.load(scope.uid,char_id=scope.character_id)}=={other}
    assert event_query.get_event(scope,turn+':assistant',include_isolated=True)['tombstoned']
    assert 'turn_id:'+turn not in recovery.filter_log(scope,f'## 12:00\n> turn_id:{turn}\nprivate\n')
    repaired=await recovery.repair(scope,result['deletion_id'])
    assert repaired['state']=='completed'


@pytest.mark.asyncio
async def test_restore_conflict_never_overwrites_edited_derived_record(seeded):
    scope,turn,other,day=seeded
    result=await recovery.delete(scope,turn_id=turn,operation_id='delete_fixture',label='mobile_fixture')
    path=resolve_path(scope,'mid_term');data=json.loads(path.read_text(encoding='utf-8'))
    data['events'][0]['summary']='independent edit'
    safe_write_json(path,data)
    with pytest.raises(control.ControlError,match='restore_conflict'):
        await recovery.restore(scope,deletion_id=result['deletion_id'],operation_id='restore_fixture',expected_revision=result['revision'])
    assert recovery.hidden(scope,turn)
    assert json.loads(path.read_text(encoding='utf-8'))['events'][0]['summary']=='independent edit'


@pytest.mark.asyncio
async def test_real_routes_mobile_can_delete_but_cannot_read_or_restore_archive(seeded,monkeypatch):
    """Recovery authorization is on the server, not a hidden phone menu."""
    import httpx
    from admin import auth
    from admin.routers import conversation
    from tests.fixtures.session_grants import issue
    scope,turn,other,day=seeded
    monkeypatch.setattr(auth,'resolve_token',lambda token:auth.TokenInfo(token,frozenset({'chat','chat.recovery'} if token=='desktop_fixture' else {'chat'})))
    mobile=issue(monkeypatch,owner=scope.uid,char=scope.character_id,label='mobile_fixture')
    desktop=issue(monkeypatch,owner=scope.uid,char=scope.character_id,label='desktop_fixture')
    app=FastAPI();app.include_router(conversation.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
        headers={'Authorization':'Bearer mobile_fixture','X-Presence-Session':mobile.session_id}
        result=(await client.post(f'/conversation/turns/{turn}/delete',json={'operation_id':'delete_fixture'},headers=headers)).json()
        assert result['state']=='completed'
        assert (await client.get('/conversation/deletions',headers=headers)).status_code==403
        assert (await client.post(f"/conversation/deletions/{result['deletion_id']}/restore",json={'operation_id':'restore_fixture','expected_revision':result['revision']},headers=headers)).status_code==403
        headers={'Authorization':'Bearer desktop_fixture','X-Presence-Session':desktop.session_id}
        assert (await client.get('/conversation/deletions',headers=headers)).json()['deletions'][0]['turn_id']==turn
        response=await client.post(f"/conversation/deletions/{result['deletion_id']}/restore",json={'operation_id':'restore_fixture','expected_revision':result['revision']},headers=headers)
        assert response.json()['state']=='restored'

@pytest.mark.asyncio
async def test_late_writers_cannot_resurrect_deleted_history_or_memory(seeded):
    """Slow summary and capture retries must respect the persistent tombstone."""
    from core.memory import event_store,short_term,mid_term
    scope,turn,other,day=seeded
    result=await recovery.delete(scope,turn_id=turn,operation_id='delete_fixture',label='mobile_fixture')
    assert result['state']=='completed'
    late=event_store.append_event(scope,event_store.EventRecord(event_id=turn+':late',turn_id=turn,actor='assistant',kind='assistant_message',memory_text='late'))
    assert not late.ok and late.error_code=='turn_deleted'
    mid_term.append(scope.uid,'late summary',source_turn_id=turn,mid_id='late_fixture',char_id=scope.character_id)
    assert not any(item.get('mid_id')=='late_fixture' for item in json.loads(resolve_path(scope,'mid_term').read_text(encoding='utf-8'))['events'])
    short_term._save(scope.uid,[{'role':'assistant','content':'late','_turn_id':turn}],char_id=scope.character_id)
    assert short_term.load(scope.uid,char_id=scope.character_id)==[]


@pytest.mark.asyncio
async def test_restore_failure_stays_hidden_and_same_operation_resumes(seeded,monkeypatch):
    """A partially restored file must not leak until all projections commit."""
    from core.memory import short_term,event_query
    scope,turn,other,day=seeded
    deleted=await recovery.delete(scope,turn_id=turn,operation_id='delete_fixture',label='mobile_fixture')
    original=recovery._apply_patches
    def fail(scope,patches,*,restore=False,dry=False):
        if restore and not dry:
            raise OSError('restore failed')
        return original(scope,patches,restore=restore,dry=dry)
    with monkeypatch.context() as patch:
        patch.setattr(recovery,'_apply_patches',fail)
        with pytest.raises(control.ControlError,match='restore_pending'):
            await recovery.restore(scope,deletion_id=deleted['deletion_id'],operation_id='restore_fixture',expected_revision=deleted['revision'])
    assert {item['_turn_id'] for item in short_term.load(scope.uid,char_id=scope.character_id)}=={other}
    assert event_query.get_event(scope,turn+':assistant',include_isolated=True)['tombstoned']
    result=await recovery.restore(scope,deletion_id=deleted['deletion_id'],operation_id='restore_fixture',expected_revision=deleted['revision'])
    assert result['state']=='restored'
    with pytest.raises(control.ControlError,match='operation_payload_conflict'):
        await recovery.restore(scope,deletion_id=deleted['deletion_id'],operation_id='restore_fixture',expected_revision=deleted['revision']+1)


@pytest.mark.asyncio
async def test_stopped_display_round_can_be_deleted_and_restored_without_learning(sandbox,monkeypatch):
    from core import conversation_execution as execution
    from tests.fixtures.session_grants import issue
    from core.memory import short_term
    scope=MemoryScope.reality_scope('owner_fixture','character_fixture')
    grant=issue(monkeypatch,owner=scope.uid,char=scope.character_id,label='mobile_fixture')
    execution.stop_request(scope,label=grant.token_label,request_id='req_fixture')
    async def forbidden():
        pytest.fail('cancelled model must not execute')
    stopped=await execution.execute(grant=grant,request_id='req_fixture',payload={'message':'question'},executor=forbidden)
    result=await recovery.delete(scope,turn_id=stopped['turn_id'],operation_id='delete_fixture',label='mobile_fixture')
    assert execution.stopped_turns(scope)==[]
    with pytest.raises(control.ControlError,match='turn_deleted'):
        await execution.execute(grant=grant,request_id='req_fixture',payload={'message':'question'},executor=forbidden)
    assert execution.stopped_turns(scope)==[]
    await recovery.restore(scope,deletion_id=result['deletion_id'],operation_id='restore_fixture',expected_revision=result['revision'])
    assert execution.stopped_turns(scope)[0]['user_text']=='question'
    assert short_term.load(scope.uid,char_id=scope.character_id)==[]


@pytest.mark.asyncio
async def test_partial_delete_fence_survives_fresh_process(seeded,sandbox,monkeypatch):
    """A fresh interpreter must still hide a failed physical projection."""
    import subprocess
    import sys
    scope,turn,other,day=seeded
    with monkeypatch.context() as patch:
        def fail(*args):
            raise OSError('fixture projection failure')
        patch.setattr(recovery,'_apply',fail)
        result=await recovery.delete(scope,turn_id=turn,operation_id='delete_restart',label='mobile_fixture')
    assert result['state']=='repair_pending'
    code="""
import sys,json
from pathlib import Path
import core.sandbox as sandbox
paths=sandbox.DataPaths(mode='test',test_session_id='restart_fixture')
paths._base=Path(sys.argv[1]);paths._project_root=paths._base
sandbox._instance=paths
from core.memory import short_term
from core.memory.scope import MemoryScope
from core.conversation_recovery import hidden
scope=MemoryScope.reality_scope('owner_fixture','character_fixture')
assert hidden(scope,'turn_fixture')
assert {item['_turn_id'] for item in short_term.load(scope.uid,char_id=scope.character_id)}=={'other_fixture'}
"""
    result=subprocess.run([sys.executable,'-c',code,str(sandbox._base)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert (await recovery.repair(scope,(recovery.list_deleted(scope)['deletions'][0]['deletion_id'])))['state']=='completed'
