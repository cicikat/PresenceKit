"""Scoped, ephemeral continuation handles for the existing read-only service."""
import asyncio
import json
import time
import uuid
import random
from collections import OrderedDict
import httpx
from core.tools import xiaohongshu as reader
from core.tools.tool_result import ToolResult,sanitize_for_prompt

_sessions=OrderedDict()
TTL=1800


def prune():
    now=time.monotonic()
    for key in list(_sessions):
        if _sessions[key]['expires']<=now:
            _sessions.pop(key,None)
    while len(_sessions)>8:
        _sessions.popitem(last=False)


def cache(uid,char_id,result,token,address):
    prune();identifier=uuid.uuid4().hex
    _sessions[identifier]={'uid':str(uid),'char_id':char_id,'expires':time.monotonic()+TTL,
        'note_id':result['note_id'],'token':token,'address':address,'result':result,
        'seen':{c['id'] for c in result['comments'] if c.get('id')},'target':len(result['comments']), 'posts':None}
    prune();return identifier


def reference(uid,char_id,identifier):
    prune();row=_sessions.get(identifier)
    if not row or row['uid']!=str(uid) or row['char_id']!=char_id or row['address']!=reader.settings()['reader_url']:
        raise ValueError('read_id_unavailable')
    return row


def status_header(result):
    display=result.get('counts_display',{})
    def value(key,raw):return result[key] if result.get(key) is not None else display.get(raw) or '未知'
    return f"帖子状态：评论总数={value('total_comments','commentCount')}，点赞={value('likes','likedCount')}，收藏={value('favorites','collectedCount')}，图片总数={result.get('image_count') if result.get('image_count') is not None else '未知'}。统计为页面所示，缩写数量非精确值。"


def result(data,lines):
    text='\n'.join(lines)
    return ToolResult(raw_data=json.dumps(data,ensure_ascii=False),safe_summary=sanitize_for_prompt(text),
        meta={'generated_at':time.time(),'validity':'current_turn','truncated':len(text)>2000})


async def run(uid,char_id,read_id,purpose,operation):
    cfg=reader.settings()
    if not cfg['effective']:return reader._failure(cfg['blocking_reason'])
    try:row=reference(uid,char_id,read_id)
    except ValueError:return reader._failure('read_id_unavailable')
    if reader._read_busy or time.monotonic()<reader._next_read_at:return reader._failure('cooldown')
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed('read_xiaohongshu')
    reader._read_busy=True;started=time.monotonic();error=''
    try:
        return await asyncio.wait_for(operation(row,cfg),timeout=90)
    except asyncio.CancelledError:
        error='cancelled';raise
    except (TimeoutError,httpx.TimeoutException):error='timeout'
    except ValueError as exc:
        error=str(exc) if str(exc) in {'login_or_rate_limit','reader_http_error','reader_rejected','read_id_unavailable','comment_ids_unavailable','comment_limit_reached','profile_unsupported','author_unavailable','no_new_comments'} else 'invalid_response'
    except Exception:error='reader_unavailable'
    finally:
        reader._read_busy=False
        reader._next_read_at=time.monotonic()+(300 if error in {'login_or_rate_limit','reader_rejected'} else random.uniform(15,25))
        from core.api_call_log import append
        append(caller='read_xiaohongshu',purpose=purpose,provider='xiaohongshu-mcp',model=purpose,
            duration_ms=int((time.monotonic()-started)*1000),ok=not error,error_category=error)
    return reader._failure(error)


async def request(row,endpoint,body):
    async with httpx.AsyncClient(timeout=55,trust_env=False) as client:
        response=await client.post(row['address']+endpoint,json=body,follow_redirects=False)
    if response.status_code in (401,403,429):raise ValueError('login_or_rate_limit')
    if response.status_code==404:raise ValueError('profile_unsupported')
    if response.status_code!=200:raise ValueError('reader_http_error')
    payload=response.json()
    if not isinstance(payload,dict) or payload.get('success') is not True:raise ValueError('reader_rejected')
    return payload


async def continue_xiaohongshu_comments(user_id,read_id,count=10,*,char_id):
    if isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=30:return reader._failure('invalid_count')
    async def fetch(row,cfg):
        if any(not c.get('id') for c in row['result']['comments']):raise ValueError('comment_ids_unavailable')
        if row['target']>=300:raise ValueError('comment_limit_reached')
        target=min(300,row['target']+count)
        payload=await request(row,'/api/v1/feeds/detail',{'feed_id':row['note_id'],'xsec_token':row['token'],
            'load_all_comments':True,'comment_config':{'max_comment_items':target,'click_more_replies':False,'scroll_speed':'normal'}})
        fresh=reader.normalize(payload,row['note_id'],target)
        if any(not c.get('id') for c in fresh['comments']):raise ValueError('comment_ids_unavailable')
        page=[];seen=set(row['seen'])
        for comment in fresh['comments']:
            if comment['id'] not in seen:
                page.append(comment);seen.add(comment['id'])
                if len(page)>=count:break
        if not page and fresh['has_more_comments'] is not False:raise ValueError('no_new_comments')
        row['seen'].update(c['id'] for c in page);row['target']=target
        for key in ('total_comments','likes','favorites','counts_display'):row['result'][key]=fresh[key]
        data={'read_id':read_id,'comments':page,'read_comments':len(row['seen']),
              'has_more':fresh['has_more_comments'],'method':'bounded_scroll_reload_with_id_dedup','requested':count,
              'total_comments':fresh['total_comments'],'fetched_at':time.time(),'remote_cursor_supported':False}
        budget=max(8,(1500-len(page)*32)//max(1,len(page)))
        return result(data,[status_header(fresh),f"read_id={read_id}；本次新增{len(page)}条，累计读过{len(row['seen'])}条；has_more={data['has_more']}。向下滚动扩大加载并按ID去重，不是游标分页。"]+[f"评论 {c['id']}: {c['content'][:budget]}" for c in page])
    return await run(user_id,char_id,read_id,'continue_comments',fetch)


async def read_xiaohongshu_images(user_id,read_id,start,count=2,*,char_id):
    if isinstance(start,bool) or not isinstance(start,int) or start<1 or isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=4:return reader._failure('invalid_image_range')
    async def fetch(row,cfg):
        images=row['result']['images']
        selected=[dict(image) for image in images if start<=image['index']<start+count]
        if not selected:raise ValueError('read_id_unavailable')
        from core.image_recognition import view
        from core.media_processor import process_image
        effective=view(reader.get_config())['effective']
        for image in selected:
            if image['status']=='analyzed':continue
            if not effective or not image['url']:
                image['status']='unavailable';continue
            try:
                description=await asyncio.wait_for(process_image(image['url']),timeout=12)
                image.update(description=str(description or '')[:3000],status='analyzed' if description else 'unavailable')
            except Exception:image['status']='unavailable'
            await asyncio.sleep(random.uniform(1,2))
        for image in selected:images[image['index']-1]=image
        data={'read_id':read_id,'images':[{'index':i['index'],'description':i['description'],'status':i['status']} for i in selected],
            'image_count':row['result']['image_count'],'next_start':selected[-1]['index']+1 if selected[-1]['index']<len(images) else None,
            'has_more':selected[-1]['index']<len(images),'cached_image_limit':60,
            'cache_truncated':(row['result']['image_count'] or 0)>len(images),'fetched_at':time.time()}
        return result(data,[status_header(row['result']),f"read_id={read_id}；next_start={data['next_start']}；has_more={data['has_more']}"]+[f"图{i['index']} [{i['status']}]: {i['description'][:350] if i['status']=='analyzed' else '未成功识别，不能推断内容'}" for i in selected])
    return await run(user_id,char_id,read_id,'read_images',fetch)


async def read_xiaohongshu_author_posts(user_id,read_id,count=10,cursor=0,*,char_id):
    if isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=20 or isinstance(cursor,bool) or not isinstance(cursor,int) or not 0<=cursor<=100:return reader._failure('invalid_count')
    async def fetch(row,cfg):
        author=row['result'].get('author_id')
        if not author:raise ValueError('author_unavailable')
        if row['posts'] is None:
            payload=await request(row,'/api/v1/user/profile',{'user_id':author,'xsec_token':row['token'],'tab':'note'})
            data=payload.get('data',{});feeds=data.get('feeds')
            if not isinstance(feeds,list):raise ValueError('profile_unsupported')
            posts=[];ids=set()
            for feed in feeds[:100]:
                if not isinstance(feed,dict):continue
                identifier=feed.get('id');card=feed.get('noteCard') or {}
                if not isinstance(identifier,str) or not reader.re.fullmatch(r'[a-fA-F0-9]{24}',identifier) or identifier in ids or feed.get('modelType') not in (None,'note'):continue
                ids.add(identifier)
                posts.append({'post_id':identifier,'title':str(card.get('displayTitle') or '')[:200],
                    'source_url':'https://www.xiaohongshu.com/explore/'+identifier})
            row['posts']=posts
        page=row['posts'][cursor:cursor+count]
        data={'read_id':read_id,'posts':page,'loaded_profile_titles':len(row['posts']),
              'next_cursor':cursor+len(page) if cursor+len(page)<len(row['posts']) else None,
              'has_more_in_sample':cursor+len(page)<len(row['posts']),'has_more_remote':None,
              'remote_pagination':'unsupported','fetched_at':time.time()}
        budget=max(10,1400//max(1,len(page)))
        return result(data,[f"帖主公开主页标题样本；read_id={read_id}；已加载{len(row['posts'])}个标题，next_cursor={data['next_cursor']}。仅首屏公开笔记，远端继续翻页不支持，不能推断私有内容；长标题为摘录。"]+[p['title'][:budget] for p in page])
    return await run(user_id,char_id,read_id,'read_author_posts',fetch)
