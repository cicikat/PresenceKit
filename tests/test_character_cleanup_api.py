from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.auth import TokenInfo
from admin.routers.character_cleanup import router


def test_cleanup_is_admin_only(monkeypatch):
    app = FastAPI(); app.include_router(router)
    monkeypatch.setattr('admin.auth.resolve_token', lambda token: TokenInfo(
        'fixture', frozenset({'admin'} if token == 'qa-admin' else {'persona'})))
    monkeypatch.setattr('core.character_cleanup.candidates', lambda: [])
    monkeypatch.setattr('core.character_cleanup.history', lambda: [])
    with TestClient(app) as client:
        assert client.get('/character-cleanup').status_code == 401
        assert client.get('/character-cleanup', headers={'Authorization':'Bearer qa-persona'}).status_code == 403
        assert client.get('/character-cleanup', headers={'Authorization':'Bearer qa-admin'}).status_code == 200


def test_delete_requires_matching_confirmation_and_explicit_valid_boolean(monkeypatch):
    app = FastAPI(); app.include_router(router)
    monkeypatch.setattr('admin.auth.resolve_token', lambda token: TokenInfo('fixture', frozenset({'admin'})))
    calls = []
    monkeypatch.setattr('core.character_cleanup.remove', lambda *args: calls.append(args) or {'status':'deleted'})
    with TestClient(app) as client:
        headers = {'Authorization':'Bearer qa-admin'}
        assert client.request('DELETE', '/character-cleanup/role-a', headers=headers, json={}).status_code == 422
        assert client.request('DELETE', '/character-cleanup/role-a', headers=headers, json={
            'fingerprint':'fixture', 'confirm_id':'role-b'}).status_code == 422
        response = client.request('DELETE', '/character-cleanup/role-a', headers=headers, json={
            'fingerprint':'fixture', 'confirm_id':'role-a'})
        assert response.status_code == 200 and calls == [('role-a', True, 'fixture')]
