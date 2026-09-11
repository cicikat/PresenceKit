import json
import time
from unittest.mock import AsyncMock

import pytest

from core import ime_awareness as awareness, ime_drafts as store


def draft(**kw):
    now = int(time.time()*1000)
    return dict(id=1, created_at=now-1000, updated_at=now, revision=1,
                app_package='example.diary', source='keyboard', content='她在写今天的感受', edit_events=[]) | kw


@pytest.fixture
def model(monkeypatch):
    from core import llm_client
    monkeypatch.setattr(awareness, 'effective_state', lambda *a: {'effective': True})
    mock = AsyncMock(return_value=json.dumps(dict(activity='work', summary='她正在写日记，提到了难过。',
        evidence='日记中直接描述难过', uncertainty='不能判断是否引用', topic='今天的感受', confidence=.8, worth_contact=True)))
    monkeypatch.setattr(llm_client, 'chat', mock)
    return mock


@pytest.mark.asyncio
async def test_revision_to_signal_once_and_work_does_not_suppress(model):
    from core.autonomy import store as signals
    store.receive('fixture', [draft()])
    await awareness.tick('owner-fixture', 'character-fixture')
    assert model.await_count == 1
    assert model.call_args.kwargs['call_category'] == 'ime_judge'
    queued = signals.drain_pending_signals('owner-fixture', 'character-fixture')
    assert len(queued) == 1 and queued[0].source == 'ime'
    assert queued[0].evidence[0]['sent_status'] == 'unknown'
    await awareness.tick('owner-fixture', 'character-fixture')
    assert model.await_count == 1
    assert store.analysis_query()[0]['status'] == 'queued'


@pytest.mark.asyncio
async def test_test_upload_and_stale_and_ordinary_own_chat_excluded(model):
    now=int(time.time()*1000)
    store.receive('fixture', [draft(app_package=store.TEST_PACKAGE), draft(id=2, updated_at=now-400000, created_at=now-500000),
                              draft(id=3, app_package='com.presencekit.mobile')])
    await awareness.tick('owner-fixture', 'character-fixture')
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_own_chat_eligible_and_failed_model_observable(model):
    now=int(time.time()*1000)
    store.receive('fixture', [draft(app_package='com.presencekit.mobile', edit_events=[
        dict(seq=1, at_ms=now, kind='delete_backward', text='好', outcome='requested')])])
    model.return_value='invalid'
    await awareness.tick('owner-fixture', 'character-fixture')
    assert store.analysis_query()[0]['status'] == 'failed'
    await awareness.tick('owner-fixture', 'character-fixture')
    assert model.await_count == 1


@pytest.mark.asyncio
async def test_new_revision_during_model_wait_cannot_publish_old_observation(model):
    from core.autonomy import store as signals
    result=model.return_value
    async def update(*a, **kw):
        store.receive('fixture', [draft(revision=2, content='new revision')])
        return result
    model.side_effect=update
    store.receive('fixture', [draft()])
    await awareness.tick('owner-fixture', 'character-fixture')
    assert not signals.drain_pending_signals('owner-fixture', 'character-fixture')
    assert store.analysis_query()[0]['status'] == 'stale_or_disabled'


def test_edit_event_wire_validation_and_isolated_retention():
    from admin.routers.ime_drafts import Draft
    now=int(time.time()*1000)
    row=draft(edit_events=[dict(seq=1,at_ms=now,kind='delete_backward',text='x',outcome='requested')])
    row['updated_at']=now
    Draft.model_validate(row)
    row['edit_events'][0]['seq']=2
    with pytest.raises(ValueError): Draft.model_validate(row)
    assert store.analysis_query() == []


def test_old_database_migrates_and_current_edits_are_not_hidden_by_row_order(sandbox):
    import sqlite3
    path=sandbox.ime_drafts_db()
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE drafts (device_id TEXT, id INTEGER,created_at INTEGER,
            updated_at INTEGER,revision INTEGER,app_package TEXT,source TEXT,content TEXT,
            PRIMARY KEY(device_id,id))''')
    now=int(time.time()*1000)
    store.receive('fixture',[draft(id=i,updated_at=now-500,created_at=now-1000) for i in range(1,202)])
    event=dict(seq=2,at_ms=now,kind='delete_backward',text='x',outcome='requested')
    store.receive('fixture',[draft(updated_at=now,revision=2,edit_events=[event])])
    latest=store.query(limit=1,latest_updates=True)[0]
    assert latest['id']==1 and latest['edit_events']==[event]


def test_ime_route_lightweight_fallback(monkeypatch):
    from core import model_registry as registry
    monkeypatch.setattr(registry, '_get_preset_config', lambda: {
        'active_routing':'fixture', 'routing_profiles':{'fixture':{'chat':'large','intent':'small'}},
        'presets':{'large':{},'small':{}}})
    monkeypatch.setattr(registry, '_active_char_model_routing', lambda: '')
    assert registry._resolve_preset_name('ime_judge') == 'small'
    assert registry.resolve_category_info('ime_judge')['source'] == 'intent_fallback'


@pytest.mark.asyncio
async def test_character_receives_policy_and_uses_existing_talk_gate(monkeypatch):
    from types import SimpleNamespace
    from core.autonomy import runner, store as signals
    from core.autonomy.models import Job, Run
    from core import llm_client
    state=signals.load('owner-fixture','character-fixture')
    state['config'].update(enabled=True, talk_enabled=True)
    monkeypatch.setattr(awareness,'effective_state',lambda *a: {'effective':True})
    monkeypatch.setattr(runner,'_user_became_active',lambda *a:False)
    monkeypatch.setattr(runner,'_runtime_tools',lambda *a:([],None))
    monkeypatch.setattr(runner,'_context_messages',lambda *a,**k:[])
    monkeypatch.setattr(runner.talk_gate,'check',lambda *a,**k:('allow','ok'))
    monkeypatch.setattr('core.autonomy.effective_state.autonomy_talk_enabled',lambda *a:True)
    sent=AsyncMock(return_value=(True,'sent'))
    monkeypatch.setattr(runner.talk_gate,'send',sent)
    turn=SimpleNamespace(tool_calls=[{'id':'fixture','name':'talk_owner','arguments':{'text':'想陪你聊聊。','reason':'current observation'}}], continuation_items=[],assistant_message={})
    model=AsyncMock(return_value=turn)
    monkeypatch.setattr(llm_client,'chat_turn',model)
    job=Job(uid='owner-fixture',char_id='character-fixture',source='autonomy',opportunity={
        'signals':[{'source':'ime','expiry':time.time()+300}]})
    run=await runner._run_locked(job,state,Run(uid=job.uid,char_id=job.char_id,source=job.source,job_id=job.id))
    assert run.talk_sent
    sent.assert_awaited_once()
    assert any(m.get('_layer')=='ime_awareness_policy' for m in model.call_args.args[0])


def test_only_observed_restlessness_is_relaxed(monkeypatch):
    from core.autonomy import policy, store as signals
    from core.scheduler.state_machine import TriggerState
    from core.dream.dream_state import DreamGuardStatus
    from types import SimpleNamespace
    state=signals.load('owner-fixture','character-fixture')
    state['config'].update(enabled=True,min_interval_seconds=0)
    monkeypatch.setattr('core.autonomy.effective_state.autonomy_enabled',lambda *a:True)
    monkeypatch.setattr('core.character_loader.is_proactive_disabled',lambda:False)
    monkeypatch.setattr('core.dream.dream_state.get_reality_guard_status',lambda _:DreamGuardStatus.ALLOW)
    monkeypatch.setattr('core.scheduler.state_machine.get_state',lambda _:TriggerState.RESTLESS)
    monkeypatch.setattr('core.conversation_gate.conversation_lock',lambda _:SimpleNamespace(locked=lambda:False))
    monkeypatch.setattr('core.message_queue.active_sessions',lambda:set())
    monkeypatch.setattr('core.message_queue.queue_size',lambda _:0)
    monkeypatch.setattr('core.activity.store.find_active_session',lambda *a:None)
    monkeypatch.setattr('core.coplay.session.is_active',lambda *a,**kw:False)
    assert policy.admission('owner-fixture','character-fixture',state)=='blocked_user_active'
    assert policy.admission('owner-fixture','character-fixture',state,allow_observed_activity=True) is None
    monkeypatch.setattr('core.scheduler.state_machine.get_state',lambda _:TriggerState.CHATTING)
    assert policy.admission('owner-fixture','character-fixture',state,allow_observed_activity=True)=='blocked_user_active'
