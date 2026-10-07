import json
from unittest.mock import AsyncMock
import pytest
from core.tools import xiaohongshu as reader
from core.tools import xiaohongshu_continuation as continuation

NOTE='a'*24
AUTHOR='b'*24


def payload(ids=(1,2),images=5):
    return {'success':True,'data':{'data':{'note':{'noteId':NOTE,'user':{'userId':AUTHOR},
        'interactInfo':{'commentCount':'100','likedCount':'1.2万','collectedCount':'20'},
        'imageList':[{'urlDefault':f'https://example.xhscdn.com/{i}.jpg'} for i in range(images)]},
        'comments':{'list':[{'id':str(i),'content':f'评论{i}'} for i in ids],'hasMore':True}}}}


@pytest.fixture(autouse=True)
def policy(monkeypatch):
    continuation._sessions.clear()
    monkeypatch.setattr(reader,'_read_busy',False);monkeypatch.setattr(reader,'_next_read_at',0)
    monkeypatch.setattr(reader,'settings',lambda:{'effective':True,'reader_url':'http://fixture','blocking_reason':''})
    monkeypatch.setattr('core.no_outbound.assert_outbound_allowed',lambda *a:None)


def handle(data=None):
    return continuation.cache('u','c',reader.normalize(data or payload(),NOTE,30),'PRIVATE_TOKEN','http://fixture')


def test_master_switch_disables_all_continuations(monkeypatch):
    from core import tool_dispatcher
    from admin.routers.settings_tools import _static_tool_enabled
    config = {'tools': {'read_xiaohongshu': {'enabled': False}}}
    monkeypatch.setattr(tool_dispatcher, 'get_config', lambda: config)
    monkeypatch.setattr(reader, 'settings', lambda cfg=None: {'effective': False})
    for name in ('continue_xiaohongshu_comments', 'read_xiaohongshu_images', 'read_xiaohongshu_author_posts'):
        assert not tool_dispatcher._is_tool_enabled(name)
        assert not _static_tool_enabled(name, config['tools'])


@pytest.mark.asyncio
async def test_scrolling_dedup_and_real_totals(monkeypatch):
    identifier=handle()
    call=AsyncMock(return_value=payload((1,2,3,3,4)))
    monkeypatch.setattr(continuation,'request',call)
    result=await continuation.continue_xiaohongshu_comments('u',identifier,2,char_id='c')
    data=json.loads(result.raw_data)
    assert [item['id'] for item in data['comments']]==['3','4']
    assert data['read_comments']==4 and data['total_comments']==100
    assert call.call_args.args[2]['load_all_comments'] is True
    assert call.call_args.args[2]['comment_config']['max_comment_items']==4
    assert 'PRIVATE_TOKEN' not in result.safe_summary and '1.2万' in result.safe_summary
    assert reader.normalize(payload(),NOTE,30)['likes'] is None


@pytest.mark.asyncio
async def test_later_images_keep_original_index_and_fail_explicitly(monkeypatch):
    identifier=handle()
    monkeypatch.setattr('core.image_recognition.view',lambda *a:{'effective':True})
    vision=AsyncMock(side_effect=['图三内容',RuntimeError('unavailable')])
    monkeypatch.setattr('core.media_processor.process_image',vision)
    monkeypatch.setattr(continuation.asyncio,'sleep',AsyncMock())
    result=await continuation.read_xiaohongshu_images('u',identifier,3,2,char_id='c')
    data=json.loads(result.raw_data)
    assert [i['index'] for i in data['images']]==[3,4]
    assert [i['status'] for i in data['images']]==['analyzed','unavailable']
    assert data['image_count']==5 and data['has_more']


@pytest.mark.asyncio
async def test_public_profile_sample_not_fake_remote_pagination(monkeypatch):
    identifier=handle()
    monkeypatch.setattr(continuation,'request',AsyncMock(return_value={'success':True,'data':{'feeds':[
        {'id':str(i)*24,'modelType':'note','noteCard':{'displayTitle':'标题'+str(i)}} for i in (1,2,3)]}}))
    data=json.loads((await continuation.read_xiaohongshu_author_posts('u',identifier,2,char_id='c')).raw_data)
    assert data['next_cursor']==2 and data['remote_pagination']=='unsupported' and data['has_more_remote'] is None
    monkeypatch.setattr(reader,'_next_read_at',0)
    data=json.loads((await continuation.read_xiaohongshu_author_posts('u',identifier,2,2,char_id='c')).raw_data)
    assert len(data['posts'])==1 and data['next_cursor'] is None


@pytest.mark.asyncio
async def test_scope_expiry_limits_and_repeated_first_screen(monkeypatch):
    identifier=handle()
    assert (await continuation.continue_xiaohongshu_comments('other',identifier,char_id='c')).meta['failure_reason']=='read_id_unavailable'
    assert (await continuation.read_xiaohongshu_images('u',identifier,1,5,char_id='c')).meta['failure_reason']=='invalid_image_range'
    monkeypatch.setattr(continuation,'request',AsyncMock(return_value=payload()))
    assert (await continuation.continue_xiaohongshu_comments('u',identifier,char_id='c')).meta['failure_reason']=='no_new_comments'
    assert continuation.reference('u','c',identifier)['target']==2
    continuation._sessions[identifier]['expires']=0
    assert (await continuation.read_xiaohongshu_author_posts('u',identifier,char_id='c')).meta['failure_reason']=='read_id_unavailable'


def test_invalid_cdn_preserves_original_image_count():
    value=payload();value['data']['data']['note']['imageList'][1]={'urlDefault':'http://127.0.0.1/private'}
    data=reader.normalize(value,NOTE,30)
    assert data['image_count']==5 and data['images'][1]['index']==2 and data['images'][1]['status']=='unavailable'
