"""Read-only adapter for xpzouying/xiaohongshu-mcp's HTTP detail API."""
import asyncio
import json
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from core.config_loader import get_config
from core.tools.tool_result import ToolResult, sanitize_for_prompt

_HOSTS = {'xhslink.com', 'www.xhslink.com', 'xiaohongshu.com', 'www.xiaohongshu.com'}


def settings(cfg=None):
    cfg = get_config() if cfg is None else cfg
    raw = cfg.get('xiaohongshu', {})
    enabled = cfg.get('tools', {}).get('read_xiaohongshu', False)
    if isinstance(enabled, dict):
        enabled = enabled.get('enabled', False)
    address = str(raw.get('reader_url') or '').rstrip('/')
    return {'enabled': bool(enabled), 'reader_url': address,
            'max_comments': max(1, min(30, int(raw.get('max_comments', 10)))),
            'max_images': max(0, min(4, int(raw.get('max_images', 2)))),
            'configured': bool(address), 'effective': bool(enabled and address),
            'blocking_reason': 'disabled' if not enabled else '' if address else 'reader_not_configured',
            'remote_status': 'not_checked', 'observation_url': '/observability/api-calls?caller=read_xiaohongshu'}


def share_url(text: str):
    for value in re.findall(r'https?://[^\s<>"\u3000]+', text):
        value = value.rstrip('，。！；、）)]}')
        parsed = urlsplit(value)
        if parsed.hostname in _HOSTS and not parsed.username and not parsed.password and parsed.port in (None, 80, 443):
            return value
    raise ValueError('invalid_share')


async def resolve_share(client, text):
    url = share_url(text)
    for _ in range(6):
        parsed = urlsplit(url)
        match = re.search(r'/(?:explore|discovery/item)/([a-fA-F0-9]{24})(?:/|$)', parsed.path)
        token = parse_qs(parsed.query).get('xsec_token', [''])[0]
        if match:
            if not token:
                raise ValueError('missing_share_token')
            return match.group(1), token
        response = await client.get(url, follow_redirects=False)
        if response.is_redirect and response.headers.get('location'):
            url = share_url(urljoin(url, response.headers['location']))
            continue
        raise ValueError('share_unavailable')
    raise ValueError('too_many_redirects')


def normalize(payload, note_id, max_comments):
    if not isinstance(payload, dict) or payload.get('success') is not True:
        raise ValueError('reader_rejected')
    data = payload.get('data', {})
    detail = data.get('data', data)
    note = detail.get('note')
    if not isinstance(note, dict) or note.get('noteId') != note_id:
        raise ValueError('invalid_note')
    comments = detail.get('comments')
    available = isinstance(comments, dict) and isinstance(comments.get('list'), list)
    items = comments['list'] if available else []
    images = []
    for item in note.get('imageList') or []:
        url = item.get('urlDefault') or item.get('urlPre') or ''
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if parsed.scheme in {'http', 'https'} and (host == 'xhscdn.com' or host.endswith('.xhscdn.com')):
            images.append({'url': url, 'description': '', 'status': 'not_analyzed'})
    return {'note_id': note_id, 'source_url': f'https://www.xiaohongshu.com/explore/{note_id}',
            'title': str(note.get('title') or ''), 'content': str(note.get('desc') or ''),
            'images': images, 'comments': [
                {'content': str(item.get('content') or ''),
                 'replies': [str(reply.get('content') or '') for reply in (item.get('subComments') or [])[:3]]}
                for item in items[:max_comments] if isinstance(item, dict)],
            'comments_status': 'sample' if available else 'unavailable',
            'has_more_comments': comments.get('hasMore') if available else None}


async def read_post(share: str) -> ToolResult:
    cfg = settings()
    if not cfg['effective']:
        return _failure(cfg['blocking_reason'])
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed('read_xiaohongshu')
    started = time.monotonic()
    error = ''
    try:
        async def fetch():
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                note_id, token = await resolve_share(client, share)
                response = await client.post(cfg['reader_url'] + '/api/v1/feeds/detail', json={
                    'feed_id': note_id, 'xsec_token': token, 'load_all_comments': True,
                    'comment_config': {'max_comment_items': cfg['max_comments'],
                                       'click_more_replies': False, 'scroll_speed': 'normal'},
                }, timeout=55, follow_redirects=False)
                if response.status_code != 200:
                    raise ValueError('reader_http_error')
                result = normalize(response.json(), note_id, cfg['max_comments'])
            from core.media_processor import process_image
            from core.image_recognition import view as recognition_view
            image_limit = cfg['max_images'] if recognition_view(get_config())['effective'] else 0
            for image in result['images'][:image_limit]:
                try:
                    description = await asyncio.wait_for(process_image(image['url']), timeout=12)
                    image['description'] = str(description or '')
                    image['status'] = 'analyzed' if description else 'unavailable'
                except Exception:
                    image['status'] = 'unavailable'
            # Balanced sections keep image/comment evidence when a post is long.
            lines = [result['source_url'], '正文摘录：' + result['title'][:60] + '\n' + result['content'][:600],
                     f"图片共{len(result['images'])}张："]
            for i, image in enumerate(result['images'][:4], 1):
                lines.append(f"图{i}: " + (image['description'][:100] if image['status'] == 'analyzed' else '尚未成功识别，不能推断图中内容'))
            lines.append('评论：仅已加载样本（含部分楼中楼），不代表全部评论。' if result['comments_status'] == 'sample' else '评论未取得，不能说没有评论。')
            budget = max(15, 600 // max(1, len(result['comments'])))
            for item in result['comments']:
                lines.append((item['content'] + (' / 回复：' + '；'.join(item['replies']) if item['replies'] else ''))[:budget])
            summary = '\n'.join(lines)
            return ToolResult(raw_data=json.dumps(result, ensure_ascii=False), safe_summary=sanitize_for_prompt(summary),
                              meta={'generated_at': time.time(), 'validity': 'current_turn', 'truncated': True})
        return await asyncio.wait_for(fetch(), timeout=90)
    except (TimeoutError, httpx.TimeoutException):
        error = 'timeout'
    except ValueError as exc:
        error = str(exc) if str(exc) in {'invalid_share', 'missing_share_token', 'share_unavailable', 'too_many_redirects', 'reader_http_error', 'reader_rejected', 'invalid_note'} else 'invalid_response'
    except Exception:
        error = 'reader_unavailable'
    finally:
        from core.api_call_log import append
        append(caller='read_xiaohongshu', purpose='read_post', provider='xiaohongshu-mcp', model='feed_detail',
               duration_ms=int((time.monotonic() - started) * 1000), ok=not error, error_category=error)
    return _failure(error)


def _failure(reason):
    hints = {'disabled': '小红书读取未开启。', 'reader_not_configured': '尚未配置小红书读取服务。',
             'missing_share_token': '链接缺少访问参数，请重新复制完整分享链接。',
             'invalid_share': '没有找到有效的小红书分享链接。'}
    text = hints.get(reason, '未能读取帖子，请检查读取服务及其登录状态，或重新复制分享链接。')
    return ToolResult(raw_data='', safe_summary=text,
                      meta={'execution_status': 'outcome_unknown', 'validity': 'execution_failed', 'failure_reason': reason})
