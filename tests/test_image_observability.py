import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import image_observability as ledger


def rows(sandbox):
    return [json.loads(line) for line in sandbox.visual_trace_log().read_text(encoding='utf-8').splitlines()]


def test_bounded_results_sensitive_failures_and_fail_open(sandbox, monkeypatch):
    ledger.append(source='chat_upload', result='x' * 13000)
    ledger.append(source='camera', result='private', sensitive=True)
    ledger.append(source='ocr', result='private upstream response', error='auth_error')
    ledger.append(source='camera', result='{"sensitive": true, "caption": "private"}')
    ledger.append(source='vision', result='data:image/png;base64,private-image-payload')
    records = rows(sandbox)
    assert len(records[0]['caption']) == 12000 and records[0]['truncated']
    assert all('caption' not in row for row in records[1:4])
    assert records[4]['caption'] == '[image omitted]'
    monkeypatch.setattr(ledger, 'get_paths', lambda: (_ for _ in ()).throw(OSError()))
    assert ledger.append(source='ocr', result='still returned to caller') is False


def test_attribution_and_nested_shadow_suppression(sandbox):
    with ledger.attributed('xiaohongshu'):
        ledger.append(source='chat_upload', result='description', cached=True)
    with ledger.suppress_nested_trace():
        ledger.append(source='screen', result='do not duplicate ingress')
    assert len(rows(sandbox)) == 1
    assert rows(sandbox)[0]['source'] == 'xiaohongshu' and rows(sandbox)[0]['cached']


@pytest.mark.asyncio
@pytest.mark.parametrize('purpose', ['chat_upload', 'video_call', 'life_diet', 'life_bill'])
async def test_real_llm_vision_exit_records_result(sandbox, monkeypatch, purpose):
    from core import llm_client
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='recognized scene'))], usage=None)))))
    monkeypatch.setattr(llm_client, '_resolve_vision_config', lambda _: {'model': 'fixture-vl', 'provider': 'fixture'})
    monkeypatch.setattr(llm_client, '_get_vision_client', lambda _: client)
    monkeypatch.setattr(llm_client, '_record_debug_request', lambda **_: None)
    output = await llm_client.chat([{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,fixture'}}]}], use_vision=True, vision_purpose=purpose)
    assert output == 'recognized scene'
    assert rows(sandbox)[0]['caption'] == output
    assert rows(sandbox)[0]['source'] == purpose and rows(sandbox)[0]['image_count'] == 1
    assert 'data:image' not in sandbox.visual_trace_log().read_text(encoding='utf-8')


@pytest.mark.asyncio
async def test_ocr_transport_and_cache_reuse_are_recorded(sandbox, monkeypatch):
    from tests.test_image_recognition import fake_transport, png
    from core import image_recognition, media_processor
    fake_transport(monkeypatch, {'md_results':'visible text'})
    cfg = {'image_recognition': {'mode':'ocr','api_key':'fixture-key'}}
    monkeypatch.setattr('core.config_loader.get_config', lambda: cfg)
    image = [(png('red'), 'sample.png')]
    assert await media_processor.ingest_image_bytes(image) == ['visible text']
    assert await media_processor.ingest_image_bytes(image) == ['visible text']
    records = rows(sandbox)
    assert [r['source'] for r in records] == ['chat_upload', 'chat_upload']
    assert records[1]['cached'] and 'fixture-key' not in str(records)


def test_endpoint_source_cursor_retention_and_auth(sandbox, monkeypatch):
    from fastapi.testclient import TestClient
    from admin.admin_server import app
    monkeypatch.setattr('admin.auth.get_admin_secret', lambda: 'fixture-secret')
    monkeypatch.setattr(ledger.time, 'time', lambda: 1000000000)
    for i, source in enumerate(['ocr', 'screen', 'ocr', 'ocr']):
        monkeypatch.setattr(ledger.time, 'time', lambda i=i: 1000000000 + i)
        ledger.append(source=source, result='result')
    client = TestClient(app)
    assert client.get('/perception/visual-trace').status_code == 401
    headers={'Authorization':'Bearer fixture-secret'}
    first=client.get('/perception/visual-trace?source=ocr&limit=2',headers=headers).json()
    assert len(first['entries']) == 2 and first['sources'] == ['ocr','screen']
    second=client.get('/perception/visual-trace?source=ocr&limit=2&before='+str(first['next_before']),headers=headers).json()
    assert len(second['entries']) == 1 and second['next_before'] is None
    monkeypatch.setattr(ledger.time, 'time', lambda: 1000000000 + 31 * 86400)
    assert client.get('/perception/visual-trace',headers=headers).json()['entries'] == []
    assert ledger.prune() == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['done', 'need_confirmation'])
async def test_phone_recognition_preserves_sensitive_stop(sandbox, monkeypatch, status):
    from tests.test_image_recognition import fake_transport
    from core.phone_control import vision_client
    payload={'status':status,'action':None,'reasoning':'recognized private page','message':'ask owner'}
    fake_transport(monkeypatch, {'choices':[{'message':{'content':json.dumps(payload)}}]})
    outcome, reason, category=await vision_client._decide_once(
        {'base_url':'http://fixture/v1','model':'fixture-vl'}, [])
    assert outcome.status == status and reason is None and not category
    record=rows(sandbox)[0]
    assert record['source'] == 'phone_control'
    if status == 'need_confirmation':
        assert record['dropped'] == 'sensitive' and 'caption' not in record
    else:
        assert 'recognized private page' in record['caption']


def test_capacity_compaction_keeps_recent_results(sandbox, monkeypatch):
    monkeypatch.setattr(ledger, 'MAX_LOG_BYTES', 1000)
    for i in range(20):
        ledger.append(source='ocr', result='x'*150 + str(i))
    records=rows(sandbox)
    assert len(records) < 20 and records[-1]['caption'].endswith('19')
