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
