import json
import pytest
from core.memory import event_store
from core.memory.scope import MemoryScope
from core.reply_context import build_reply_prefix
from core.tools.message_context import read_message_context


def seed(uid='u', char_id='c'):
    scope = MemoryScope.reality_scope(uid, char_id)
    for i in range(15):
        role = 'user' if i % 2 == 0 else 'assistant'
        event_store.append_event(scope, dict(event_id=f't{i}:{role}', turn_id=f't{i}', seq=i,
            occurred_at=1700000000+i, actor=role, kind=role+'_message', channel='desktop',
            source='user_chat', visible_text='同文'))
    event_store.append_event(scope, dict(event_id='external', occurred_at=1700000004.5,
        actor='assistant', kind='assistant_message', source='web', visible_text='隔离资料'))
    event_store.append_event(scope, dict(event_id='tool', occurred_at=1700000004.6,
        actor='assistant', kind='tool_result', source='user_chat', visible_text='工具记录'))


@pytest.mark.asyncio
async def test_exact_anchor_and_bounded_direction(sandbox):
    seed()
    result = json.loads((await read_message_context('u', 't12:user', 'before', 10, char_id='c')).safe_summary)
    assert len(result['messages']) == 10
    assert result['messages'][0]['message_id'] == 't2:user'
    assert result['messages'][-1]['message_id'] == 't11:assistant'
    assert all('datetime' in item for item in result['messages'])
    after = json.loads((await read_message_context('u', 't12:user', 'after', 10, char_id='c')).safe_summary)
    assert [item['message_id'] for item in after['messages']] == ['t13:assistant', 't14:user']


@pytest.mark.asyncio
async def test_unavailable_scope_limit_tombstone(sandbox):
    seed()
    for uid, cid, anchor, count in [('other','c','t2:user',5), ('u','other','t2:user',5),
                                   ('u','c','external',5), ('u','c','t2:user',11)]:
        result = json.loads((await read_message_context(uid, anchor, count=count, char_id=cid)).safe_summary)
        assert result['status'] == 'outcome_unknown'
    event_store.tombstone_event(MemoryScope.reality_scope('u','c'), 't2:user')
    assert 'message_unavailable' in (await read_message_context('u','t2:user',char_id='c')).safe_summary


def test_quote_uses_server_author_time_and_text(sandbox):
    seed()
    prefix = build_reply_prefix({'message_id':'t2:user','text':'伪造','ts':1}, user_id='u', char_id='c')
    assert '作者=用户' in prefix and 'message_id=t2:user' in prefix and '同文' in prefix and '伪造' not in prefix
    assert '不可核验' in build_reply_prefix({'message_id':'t2:user'}, user_id='other', char_id='c')
    assert build_reply_prefix({'text':'hi','ts':float('nan')}) is None
