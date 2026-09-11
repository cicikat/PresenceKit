from unittest.mock import AsyncMock

import httpx
import pytest

from core.model_discovery import catalogue_url, discover


@pytest.mark.parametrize('address,expected', [
    ('https://relay.invalid', 'https://relay.invalid/v1/models'),
    ('https://relay.invalid/v1/', 'https://relay.invalid/v1/models'),
    ('https://relay.invalid/api/v4/chat/completions', 'https://relay.invalid/api/v4/models'),
    ('https://relay.invalid/v1/responses', 'https://relay.invalid/v1/models'),
    ('https://relay.invalid/v1/messages', 'https://relay.invalid/v1/models'),
])
def test_catalogue_address(address, expected):
    assert catalogue_url(address) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('code,payload,status', [
    (200, {'data': [{'id': 'b'}, {'id': 'a'}, {'id': 'b'}, {}]}, 'ok'),
    (200, {'data': []}, 'empty'),
    (200, {'error': 'unsupported'}, 'invalid_response'),
    (404, {}, 'unsupported'), (401, {}, 'unauthorized'), (500, {}, 'http_error'),
])
async def test_catalogue_results(monkeypatch, code, payload, status):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(code, json=payload)
    monkeypatch.setattr('core.model_registry._make_http_client', lambda *a, **kw: client)
    result = await discover('https://relay.invalid', 'example-key', 'anthropic_messages', 'x_api_key')
    assert result['status'] == status
    assert result['models'] == (['a', 'b'] if status == 'ok' else [])
    assert client.get.call_args.kwargs['headers']['x-api-key'] == 'example-key'
    assert client.get.call_args.kwargs['follow_redirects'] is False


@pytest.mark.asyncio
async def test_saved_key_stays_on_saved_address(monkeypatch):
    from admin.routers import settings_llm as routes
    monkeypatch.setattr(routes, 'get_config', lambda: {'model_presets': {'presets': {
        'example': {'base_url': 'https://original.invalid/v1', 'api_key': 'saved-example-key'},
    }}})
    remote = AsyncMock(return_value={'status': 'ok', 'models': ['example-model']})
    monkeypatch.setattr('core.model_discovery.discover', remote)
    req = routes.ModelDiscoveryRequest(base_url='https://changed.invalid/v1', preset_name='example')
    assert (await routes.discover_models(req, auth=None))['status'] == 'key_required'
    remote.assert_not_called()
    req.base_url = 'https://original.invalid/v1/chat/completions'
    assert (await routes.discover_models(req, auth=None))['status'] == 'ok'
    assert remote.call_args.args[1] == 'saved-example-key'
    req.api_key = 'replacement-example-key'
    req.base_url = 'https://changed.invalid/v1'
    await routes.discover_models(req, auth=None)
    assert remote.call_args.args[1] == 'replacement-example-key'


def test_route_requires_admin(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin.routers.settings_llm import router
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).post('/model-presets/discover', json={'base_url': 'https://example.invalid'}).status_code == 401
