"""Image connection probes use saved settings and synthetic images only."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from admin.routers import settings_llm as router


@pytest.mark.asyncio
async def test_ocr_probe_has_no_user_media_or_response_echo(monkeypatch):
    from core import image_recognition
    fake = AsyncMock(return_value='TEST 123')
    monkeypatch.setattr(image_recognition, 'recognize_ocr', fake)
    monkeypatch.setattr(router, 'get_config', lambda: {'image_recognition': {'api_key': 'fixture'}})
    result = await router.test_image_connection('ocr', auth=None)
    assert result['ok'] is True
    assert fake.call_args.args[0].startswith('data:image/png;base64,')
    assert 'TEST 123' not in str(result)


@pytest.mark.asyncio
async def test_probe_does_not_pollute_character_stats(sandbox, monkeypatch):
    from core import conversation_stats, image_recognition
    from datetime import date
    async def probe(*args, **kwargs):
        conversation_stats.record('image_view', uid='owner', char_id='role')
        return 'TEST 123'
    monkeypatch.setattr(image_recognition, 'recognize_ocr', probe)
    monkeypatch.setattr(router, 'get_config', lambda: {'image_recognition': {'api_key': 'fixture'}})
    await router.test_image_connection('ocr', auth=None)
    assert conversation_stats.query(date.today(), date.today(), uid='owner', char_id='role')['days'][0]['image_views'] is None


@pytest.mark.asyncio
async def test_disabled_vision_does_not_call_remote(monkeypatch):
    monkeypatch.setattr(router, 'get_config', lambda: {'vision': {'enabled': False}})
    with pytest.raises(HTTPException) as error:
        await router.test_image_connection('general', auth=None)
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_probe_failure_does_not_expose_credentials(monkeypatch):
    from core import image_recognition
    monkeypatch.setattr(router, 'get_config', lambda: {'image_recognition': {'api_key': 'fixture'}})
    monkeypatch.setattr(image_recognition, 'recognize_ocr', AsyncMock(side_effect=ValueError('secret=private')))
    result = await router.test_image_connection('ocr', auth=None)
    assert result['ok'] is False
    assert result['error_category'] == 'ValueError'
    assert 'private' not in str(result)


@pytest.mark.asyncio
async def test_phone_probe_inherits_general_and_closes_client(monkeypatch):
    import openai
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=MagicMock(choices=[MagicMock(message=MagicMock(content='TEST 123'))]))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock()
    factory = MagicMock(return_value=context)
    monkeypatch.setattr(openai, 'AsyncOpenAI', factory)
    from core import llm_client
    monkeypatch.setattr(llm_client, '_make_http_client', lambda _: None)
    monkeypatch.setattr(router, 'get_config', lambda: {'vision': {'enabled': True, 'model': 'general', 'base_url': 'https://example.test/v1'}, 'phone_control_vision': {'model': 'phone'}})
    assert (await router.test_image_connection('phone', auth=None))['ok']
    assert client.chat.completions.create.call_args.kwargs['model'] == 'phone'
    assert factory.call_args.kwargs['max_retries'] == 0
    context.__aexit__.assert_awaited_once()


def test_probe_rejects_non_admin_before_calling_provider(sandbox, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin import auth
    from core import image_recognition
    probe = AsyncMock()
    monkeypatch.setattr(image_recognition, 'recognize_ocr', probe)
    monkeypatch.setattr(auth, '_is_rate_blocked', lambda ip: False)
    monkeypatch.setattr(auth, 'resolve_token', lambda raw: SimpleNamespace(scopes={'memory.read','state.read'},label='fixture'))
    app = FastAPI()
    app.include_router(router.router)
    client = TestClient(app)
    assert client.post('/image-recognition/test/ocr').status_code == 401
    assert client.post('/image-recognition/test/ocr', headers={'Authorization':'Bearer fixture'}).status_code == 403
    probe.assert_not_awaited()
