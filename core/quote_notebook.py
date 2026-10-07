"""Owner/character quote snapshots with immutable evidence and editable notes."""
from __future__ import annotations
import json
import sqlite3
import time
import hashlib
from contextlib import contextmanager
from core.sandbox import get_paths
from core.memory.scope import MemoryScope
from core.memory.event_query import get_event
from core.tools.message_context import get_message_event
from datetime import datetime
from core.tools.tool_result import ToolResult, sanitize_for_prompt


@contextmanager
def connection():
    path = get_paths().quote_notebook_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=5) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''CREATE TABLE IF NOT EXISTS quotes(
            uid TEXT, char_id TEXT, quote_id TEXT, message_id TEXT, author TEXT, message_ts REAL,
            saved_at REAL, text TEXT, related_json TEXT, note TEXT, note_updated_at REAL,
            PRIMARY KEY(uid,char_id,quote_id), UNIQUE(uid,char_id,message_id));
            CREATE TABLE IF NOT EXISTS note_changes(uid TEXT,char_id TEXT,quote_id TEXT,ts REAL,note TEXT);''')
        yield db


def save(uid, char_id, message_id, note='', related_event_ids=None):
    if not isinstance(message_id, str) or not 1 <= len(message_id) <= 256:
        raise ValueError('invalid_message_id')
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError('note_limit_2000')
    related_event_ids = related_event_ids or []
    if not isinstance(related_event_ids, list) or len(related_event_ids) > 3 or any(not isinstance(i,str) or not 1 <= len(i) <= 256 for i in related_event_ids):
        raise ValueError('related_event_limit_3')
    scope = MemoryScope.reality_scope(str(uid), char_id)
    event = get_message_event(scope, message_id)
    if not event or event.get('tombstoned') or event['kind'] not in {'user_message','assistant_message','trigger_assistant'}:
        raise ValueError('message_unavailable')
    text = str(event.get('visible_text') or '')
    if not text or len(text) > 20000 or 'visible_text' in event.get('truncated_fields',[]):
        raise ValueError('full_quote_unavailable')
    related=[]
    for identifier in dict.fromkeys(related_event_ids):
        other = get_event(scope,identifier)
        if not other or other.get('tombstoned'):
            raise ValueError('related_event_unavailable')
        related.append({'event_id':identifier,'occurred_at':other['occurred_at'],
                        'text':str(other.get('memory_text') or other.get('visible_text') or '')[:800]})
    quote_id=hashlib.sha256(message_id.encode()).hexdigest()[:24]
    now=time.time()
    with connection() as db:
        cursor=db.execute('INSERT OR IGNORE INTO quotes VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (str(uid),char_id,quote_id,message_id,event['actor'],event['occurred_at'],now,text,
             json.dumps(related,ensure_ascii=False),note,now))
        created=bool(cursor.rowcount)
    if created:
        provenance(uid,char_id,quote_id,'save',note)
    return {'quote_id':quote_id,'created':created,'note':'重复收藏不覆盖原话、笔记或关联；笔记请单独更新。'}


def provenance(uid,char_id,quote_id,action,note):
    from core.memory.provenance_log import append
    append(str(uid),char_id,artifact='quote_notebook',field=quote_id,
           after_gist=action+':'+note[:100],trigger_signal='explicit_quote_tool',origin={'source':'assistant_loop'})


def list_quotes(uid,char_id,query='',limit=20):
    if not isinstance(query,str) or len(query)>200 or isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=50:
        raise ValueError('invalid_query_or_limit')
    with connection() as db:
        rows=db.execute('SELECT quote_id,message_id,author,message_ts,saved_at,substr(text,1,160) preview,note,note_updated_at FROM quotes WHERE uid=? AND char_id=? AND (instr(text,?)>0 OR instr(note,?)>0) ORDER BY saved_at DESC,quote_id LIMIT ?',
            (str(uid),char_id,query,query,limit)).fetchall()
        total=db.execute('SELECT count(*) FROM quotes WHERE uid=? AND char_id=? AND (instr(text,?)>0 OR instr(note,?)>0)',(str(uid),char_id,query,query)).fetchone()[0]
    return {'items':[dict(row) for row in rows],'total':total}


def read(uid,char_id,quote_id):
    with connection() as db:
        row=db.execute('SELECT * FROM quotes WHERE uid=? AND char_id=? AND quote_id=?',(str(uid),char_id,quote_id)).fetchone()
        changes=db.execute('SELECT ts,note FROM note_changes WHERE uid=? AND char_id=? AND quote_id=? ORDER BY ts DESC LIMIT 10',(str(uid),char_id,quote_id)).fetchall()
    if row is None:
        raise ValueError('quote_unavailable')
    result=dict(row); result.pop('uid');result.pop('char_id')
    result['related_memories']=json.loads(result.pop('related_json'))
    for key in ('message_ts','saved_at','note_updated_at'):
        result[key+'_iso']=datetime.fromtimestamp(result[key]).astimezone().isoformat(timespec='seconds')
    for item in result['related_memories']:
        related=get_event(MemoryScope.reality_scope(str(uid),char_id),item['event_id'])
        item['source_available']=bool(related and not related.get('tombstoned'))
    result['note_changes']=[dict(item) for item in changes]
    event=get_message_event(MemoryScope.reality_scope(str(uid),char_id),result['message_id'])
    result['source_available']=bool(event and not event.get('tombstoned'))
    result['snapshot_note']='收藏当时的原话快照；来源后来不可用时仍保留收藏，删除请由用户管理。'
    return result


def update_note(uid,char_id,quote_id,note):
    if not isinstance(note,str) or len(note)>2000:
        raise ValueError('note_limit_2000')
    now=time.time()
    with connection() as db:
        old=db.execute('SELECT note FROM quotes WHERE uid=? AND char_id=? AND quote_id=?',(str(uid),char_id,quote_id)).fetchone()
        if old is None:
            raise ValueError('quote_unavailable')
        db.execute('INSERT INTO note_changes VALUES(?,?,?,?,?)',(str(uid),char_id,quote_id,now,old['note']))
        db.execute('UPDATE quotes SET note=?,note_updated_at=? WHERE uid=? AND char_id=? AND quote_id=?',(note,now,str(uid),char_id,quote_id))
    provenance(uid,char_id,quote_id,'note',note)
    return {'quote_id':quote_id,'note':note,'note_updated_at':now}


def delete(uid,char_id,quote_id):
    with connection() as db:
        count=db.execute('DELETE FROM quotes WHERE uid=? AND char_id=? AND quote_id=?',(str(uid),char_id,quote_id)).rowcount
        db.execute('DELETE FROM note_changes WHERE uid=? AND char_id=? AND quote_id=?',(str(uid),char_id,quote_id))
    return {'deleted':bool(count)}


def result(data):
    text=json.dumps(data,ensure_ascii=False)
    return ToolResult(raw_data=text,safe_summary=sanitize_for_prompt(text),meta={'generated_at':time.time(),'validity':'current_turn'})


async def save_quote(user_id,message_id,note='',related_event_ids=None,*,char_id):
    return result(save(user_id,char_id,message_id,note,related_event_ids))


async def search_quotes(user_id,query='',limit=20,*,char_id):
    return result(list_quotes(user_id,char_id,query,limit))


async def read_quote(user_id,quote_id,*,char_id):
    data=read(user_id,char_id,quote_id)
    if data['author']=='assistant':
        from core.memory.short_term import _sanitize_assistant_message
        data['text']=_sanitize_assistant_message(data['text'])
    return result(data)


async def write_quote_note(user_id,quote_id,note,*,char_id):
    return result(update_note(user_id,char_id,quote_id,note))
