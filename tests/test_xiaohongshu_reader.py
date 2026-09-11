from unittest.mock import AsyncMock

import httpx
import pytest

from core.tools import xiaohongshu as xhs

NOTE = 'a' * 24
URL = f'https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=example'


def payload(comments=True):
    detail = {'note': {'noteId': NOTE, 'title': '标题', 'desc': '正文',
                       'imageList': [{'urlDefault': 'https://example.xhscdn.com/example.jpg'}]}}
    if comments:
        detail['comments'] = {'list': [{'content': '评论', 'subComments': [{'content': '回复'}]}], 'hasMore': True}
    return {'success': True, 'data': {'feed_id': NOTE, 'data': detail}}


def test_parse_share_and_comment_completeness():
    assert xhs.share_url('分享一下 ' + URL + '。') == URL
    with pytest.raises(ValueError):
        xhs.share_url('https://xiaohongshu.com.attacker.invalid/explore/' + NOTE)
    data = xhs.normalize(payload(), NOTE, 5)
    assert data['comments'][0]['replies'] == ['回复']
    assert data['comments_status'] == 'sample'
    assert data['has_more_comments'] is True
    assert xhs.normalize(payload(False), NOTE, 5)['comments_status'] == 'unavailable'
    assert 'xsec_token' not in data['source_url']


async def test_short_link_and_redirect_boundary():
    client = AsyncMock()
    client.get.return_value = httpx.Response(302, headers={'location': URL})
    assert await xhs.resolve_share(client, 'https://xhslink.com/example') == (NOTE, 'example')
    client.get.return_value = httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
    with pytest.raises(ValueError):
        await xhs.resolve_share(client, 'https://xhslink.com/example')
    with pytest.raises(ValueError, match='missing_share_token'):
        await xhs.resolve_share(client, URL.split('?')[0])


async def test_reader_content_images_and_comments(monkeypatch):
    cfg = {'tools': {'read_xiaohongshu': {'enabled': True}},
           'xiaohongshu': {'reader_url': 'http://127.0.0.1:18060'}}
    monkeypatch.setattr(xhs, 'get_config', lambda: cfg)
    monkeypatch.setattr('core.no_outbound.assert_outbound_allowed', lambda *a: None)
    monkeypatch.setattr('core.image_recognition.view', lambda *a: {'effective': True})
    monkeypatch.setattr('core.media_processor.process_image', AsyncMock(return_value='图片内容'))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = httpx.Response(200, json=payload())
    monkeypatch.setattr(xhs.httpx, 'AsyncClient', lambda **kw: client)
    result = await xhs.read_post(URL)
    assert all(text in result.safe_summary for text in ('正文', '图片内容', '评论', '回复', '不代表全部'))
    assert 'xsec_token' not in result.safe_summary
    assert client.post.call_args.args[0].endswith('/api/v1/feeds/detail')
    assert client.post.call_args.kwargs['json']['comment_config']['max_comment_items'] == 10


async def test_disabled_and_missing_service_are_explicit(monkeypatch):
    monkeypatch.setattr(xhs, 'get_config', lambda: {})
    assert (await xhs.read_post(URL)).meta['failure_reason'] == 'disabled'
    monkeypatch.setattr(xhs, 'get_config', lambda: {'tools': {'read_xiaohongshu': True}})
    assert (await xhs.read_post(URL)).meta['failure_reason'] == 'reader_not_configured'


def test_dispatcher_exposure_gate(monkeypatch):
    from core import tool_dispatcher as dispatcher
    monkeypatch.setattr(dispatcher, 'get_config', lambda: {})
    assert not dispatcher._is_tool_enabled('read_xiaohongshu')
    monkeypatch.setattr(dispatcher, 'get_config', lambda: {
        'tools': {'read_xiaohongshu': {'enabled': True}},
        'xiaohongshu': {'reader_url': 'http://127.0.0.1:18060'},
    })
    assert dispatcher._is_tool_enabled('read_xiaohongshu')
    assert dispatcher._TOOL_REGISTRY['read_xiaohongshu']['examples']
    assert dispatcher._TOOL_REGISTRY['read_xiaohongshu']['keywords']


def test_settings_roundtrip(tmp_path, monkeypatch):
    import yaml
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin import auth
    from admin.routers import settings_tools as routes
    path = tmp_path / 'config.yaml'
    path.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(routes, 'CONFIG_FILE', path)
    monkeypatch.setattr(routes, 'get_config', lambda: yaml.safe_load(path.read_text(encoding='utf-8')))
    monkeypatch.setattr('core.config_loader.reload_config', lambda: None)
    monkeypatch.setattr(auth, 'resolve_token', lambda token: auth.TokenInfo('fixture', frozenset({token})))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    body = {'enabled': True, 'reader_url': 'http://127.0.0.1:18060', 'max_comments': 12, 'max_images': 1}
    assert client.put('/settings/xiaohongshu', headers={'Authorization': 'Bearer sensor.write'}, json=body).status_code == 403
    response = client.put('/settings/xiaohongshu', headers={'Authorization': 'Bearer admin'}, json=body)
    assert response.status_code == 200
    assert response.json()['effective'] is True
    assert client.get('/settings/xiaohongshu', headers={'Authorization': 'Bearer admin'}).json()['max_comments'] == 12
