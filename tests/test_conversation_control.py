"""A: API isolation, durable receipts and stop/commit arbitration regressions."""
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core import conversation_control as control
from core.memory.scope import MemoryScope


@pytest.fixture
def scope(sandbox):
    return MemoryScope.reality_scope('owner_fixture', 'character_fixture')


def test_stop_wins_before_commit_and_duplicate_admission_cannot_restart(scope):
    """A stopped request cannot reach commit even when a duplicate POST arrives."""
    row = control.admit(scope, label='mobile_fixture', request_id='req_fixture', digest='digest')
    control.transition(scope, row['execution_id'], 'running')
    control.transition(scope, row['execution_id'], 'stop_requested')
    with pytest.raises(control.ControlError, match='execution_state_conflict'):
        control.transition(scope, row['execution_id'], 'committing')
    control.transition(scope, row['execution_id'], 'stopped', turn_id='stopped_fixture')
    replay = control.admit(scope, label='mobile_fixture', request_id='req_fixture', digest='digest')
    assert replay['state'] == 'stopped'
    assert replay['turn_id'] == 'stopped_fixture'
    assert control.active(scope) == []
    with pytest.raises(control.ControlError, match='request_payload_conflict'):
        control.admit(scope, label='mobile_fixture', request_id='req_fixture', digest='other')


def test_commit_wins_stop_does_not_rewrite_a_completed_turn(scope):
    """Stopping a finalizing turn must never turn a durable completion into stopped."""
    row = control.admit(scope, label='desktop_fixture', request_id='req_fixture', digest='digest')
    control.transition(scope, row['execution_id'], 'running')
    control.transition(scope, row['execution_id'], 'committing')
    with pytest.raises(control.ControlError):
        control.transition(scope, row['execution_id'], 'stop_requested')
    control.transition(scope, row['execution_id'], 'completed', turn_id='turn_fixture')
    assert control.lookup(scope, label='desktop_fixture', request_id='req_fixture')['turn_id'] == 'turn_fixture'


def test_restarted_process_reports_interrupted_without_reexecution(scope):
    """Real second-process reads recover orphan metadata, not an eternal spinner."""
    control.admit(scope, label='desktop_fixture', request_id='req_fixture', digest='digest')
    script = '''
import json, sys
from pathlib import Path
from core.sandbox import get_paths
from core.memory.scope import MemoryScope
from core.conversation_control import lookup
get_paths()._base = Path(sys.argv[1])
print(json.dumps(lookup(MemoryScope.reality_scope('owner_fixture','character_fixture'),label='desktop_fixture',request_id='req_fixture')))
'''
    from core.sandbox import get_paths
    result = subprocess.run([sys.executable, '-c', script, str(get_paths()._base)], cwd=Path(__file__).resolve().parents[1],
                            env=os.environ.copy(), text=True, capture_output=True, timeout=20, check=True)
    assert json.loads(result.stdout.strip().splitlines()[-1])['state'] == 'interrupted'


def test_mounted_status_api_is_scoped_and_mutations_are_metadata_only(scope, monkeypatch):
    """Real session/auth rejects cross-character and exposes no request payload."""
    from admin import auth
    from admin.routers import conversation
    from core import session_scope
    from tests.fixtures.session_grants import issue
    session_scope.reset_for_tests()
    monkeypatch.setattr(auth, 'resolve_token', lambda token: auth.TokenInfo(token, frozenset({'chat'})))
    desktop = issue(monkeypatch, owner=scope.uid, char=scope.character_id, label='desktop_fixture')
    other = issue(monkeypatch, owner=scope.uid, char='other_fixture', label='desktop_fixture')
    app = FastAPI()
    app.include_router(conversation.router)
    row = control.admit(scope, label='desktop_fixture', request_id='req_fixture', digest='secret-digest')
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer desktop_fixture', 'X-Presence-Session': desktop.session_id}
        response = client.get('/conversation/requests/req_fixture', headers=headers)
        assert response.status_code == 200
        assert response.json()['execution_id'] == row['execution_id']
        assert response.headers['Cache-Control'] == 'no-store'
        assert client.get('/conversation/executions', headers={'Authorization': 'Bearer desktop_fixture'}).status_code == 422
        cross = {**headers, 'X-Presence-Session': other.session_id}
        assert client.get('/conversation/requests/req_fixture', headers=cross).json()['state'] == 'unknown'
        denied = {**headers, 'Authorization': 'Bearer mobile_fixture'}
        assert client.get('/conversation/executions', headers=denied).status_code == 403
        page = client.get('/conversation/mutations?limit=1', headers=headers).json()
        assert page['mutations'][0]['state'] == 'queued'
        assert 'secret-digest' not in json.dumps(page)
        assert 'credential' not in json.dumps(page)
        assert client.get(f"/conversation/mutations?after={page['cursor']}", headers=headers).json()['mutations'] == []
    session_scope.reset_for_tests()

@pytest.mark.asyncio
async def test_stop_cancels_executor_behind_shield_and_keeps_display_only(scope, monkeypatch):
    """Explicit stop reaches the real shielded task, closes its source and never learns."""
    import asyncio
    from admin.session_requests import run_session_request
    from core import conversation_execution as execution, session_scope
    from tests.fixtures.session_grants import issue
    session_scope.reset_for_tests()
    grant = issue(monkeypatch, owner=scope.uid, char=scope.character_id, label='mobile_fixture')
    entered, closed = asyncio.Event(), asyncio.Event()
    writes = []
    async def provider():
        try:
            execution.started()
            execution.prefix('safe prefix<unfinished')
            entered.set()
            await asyncio.Event().wait()
            execution.begin_commit()
            writes.append('learned')
            return {'turn_id': 'unexpected', 'reply': 'unexpected'}
        finally:
            closed.set()
    waiter = asyncio.create_task(run_session_request(grant=grant, request_id='req_stop',
                               payload={'kind': 'mobile_chat', 'message': 'fixture question'}, executor=provider))
    await asyncio.wait_for(entered.wait(), 2)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not closed.is_set()  # Existing transport-disconnect contract stays intact.
    active = control.active(scope)
    execution.stop_execution(scope, active[0]['execution_id'])
    await asyncio.wait_for(closed.wait(), 2)
    task = next(iter(session_scope._RUNNING.values()), None)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 2)
    assert control.lookup(scope, label='mobile_fixture', request_id='req_stop')['state'] == 'stopped'
    assert writes == []
    saved = execution.stopped_turns(scope)
    assert saved[0]['user_text'] == 'fixture question'
    assert saved[0]['assistant_text'] == 'safe prefix'
    assert not control.active(scope)
    _, replay = await session_scope.execute_request(grant=grant, request_id='req_stop',
        payload={'kind':'mobile_chat','message':'fixture question'}, executor=provider)
    assert replay['status'] == 'stopped'
    session_scope.reset_for_tests()


@pytest.mark.asyncio
async def test_stop_before_admission_prevents_late_original_post(scope, monkeypatch):
    """An early stop persists across the POST/stop network reordering race."""
    from core import conversation_execution as execution
    from tests.fixtures.session_grants import issue
    grant = issue(monkeypatch, owner=scope.uid, char=scope.character_id, label='mobile_fixture')
    stopped = execution.stop_request(scope, label='mobile_fixture', request_id='req_late')
    async def forbidden():
        pytest.fail('late stopped POST executed model/tools')
    result = await execution.execute(grant=grant, request_id='req_late', payload={'message':'question'}, executor=forbidden)
    assert result['status'] == 'stopped'
    assert execution.stop_request(scope, label='mobile_fixture', request_id='req_late')['execution_id'] == stopped['execution_id']
    assert control.lookup(scope, label='mobile_fixture', request_id='req_late')['turn_id'] == result['turn_id']


@pytest.mark.asyncio
async def test_stopping_one_credential_from_other_client_uses_exact_execution(scope, monkeypatch):
    """Same-role cross-client stopping is allowed but another character is not."""
    import asyncio
    import httpx
    from admin import auth
    from admin.routers import conversation
    from core import conversation_execution as execution
    from tests.fixtures.session_grants import issue
    monkeypatch.setattr(auth, 'resolve_token', lambda token: auth.TokenInfo(token, frozenset({'chat'})))
    desktop = issue(monkeypatch, owner=scope.uid, char=scope.character_id, label='desktop_fixture')
    mobile = issue(monkeypatch, owner=scope.uid, char=scope.character_id, label='mobile_fixture')
    wrong = issue(monkeypatch, owner=scope.uid, char='other_fixture', label='mobile_fixture')
    entered = asyncio.Event()
    async def provider():
        execution.started()
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(execution.execute(grant=desktop, request_id='req_cross',payload={'message':'hello'},executor=provider))
    await asyncio.wait_for(entered.wait(), 2)
    app=FastAPI();app.include_router(conversation.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
        headers={'Authorization':'Bearer mobile_fixture','X-Presence-Session':wrong.session_id}
        eid=control.active(scope)[0]['execution_id']
        assert (await client.post(f'/conversation/executions/{eid}/stop',headers=headers)).status_code==404
        headers['X-Presence-Session']=mobile.session_id
        assert (await client.post(f'/conversation/executions/{eid}/stop',headers=headers)).json()['state']=='stop_requested'
    result = await asyncio.wait_for(task, 2)
    assert result['status']=='stopped'
    assert control.active(scope)==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('channel', ['desktop', 'mobile'])
async def test_real_owner_pipeline_stops_at_model_and_releases_gate(scope, monkeypatch, channel):
    """Both model branches leave no learned turn and free the owner gate on stop."""
    import asyncio
    from types import SimpleNamespace
    from core import conversation_execution as execution
    from core.owner_chat.service import run_owner_chat_turn
    from core.conversation_gate import conversation_lock
    from tests.fixtures.session_grants import issue
    grant = issue(monkeypatch, owner=scope.uid, char=scope.character_id, label=channel+'_fixture')
    entered, closed = asyncio.Event(), asyncio.Event()
    class Pipeline:
        character = SimpleNamespace(name='Fixture')
        context = property(lambda self:self)
        generation = property(lambda self:self)
        async def fetch(self,*args,**kwargs):
            return {'_scoped_character':SimpleNamespace(name='Fixture')}
        def build_prompt(self,*args,**kwargs):
            return [], {}
        async def run_llm(self,*args,**kwargs):
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                closed.set()
        async def run_llm_stream(self,*args,**kwargs):
            try:
                yield 'safe prefix'
                entered.set()
                await asyncio.Event().wait()
            finally:
                closed.set()
    monkeypatch.setattr('core.conversation_activity.mark_user_active', lambda:None)
    monkeypatch.setattr('core.scheduler.state_machine.notify_owner_turn', lambda uid:None)
    monkeypatch.setattr('core.scheduler.proactive_ledger.record_user_message', lambda uid:None)
    monkeypatch.setattr('core.tool_dispatcher.tool_loop_active', lambda uid:False)
    monkeypatch.setattr('core.owner_chat.prepare._probe_and_execute_tools', lambda *args,**kwargs:asyncio.sleep(0,result=None))
    monkeypatch.setattr('channels.ui_push.any_connected', lambda:channel=='desktop')
    monkeypatch.setattr('channels.desktop_ws._send_json', lambda payload:asyncio.sleep(0,result=True))
    async def run():
        return await run_owner_chat_turn('fixture question', channel, frozen_scope=scope,
            fixed_user_id=scope.uid, pipeline_override=Pipeline(),request_id='req_real_'+channel)
    task=asyncio.create_task(execution.execute(grant=grant,request_id='req_real_'+channel,payload={'message':'fixture question'},executor=run))
    await asyncio.wait_for(entered.wait(),2)
    execution.stop_execution(scope,control.active(scope)[0]['execution_id'])
    result=await asyncio.wait_for(task,2)
    assert result['state']=='stopped'
    assert closed.is_set()
    assert not conversation_lock(scope.uid).locked()
    from core.memory import short_term
    assert short_term.load(scope.uid,char_id=scope.character_id)==[]
    if channel=='desktop':
        assert result['reply']=='safe prefix'
