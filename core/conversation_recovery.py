"""Exact-turn withdrawal with durable snapshots and conflict-safe restoration.

No fuzzy matching, no bulk restore of a memory file, no model calls. A durable
visibility fence remains in force if a projection write fails mid-operation.
"""
from __future__ import annotations
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
import json
import re
import sqlite3
import threading
import time
from uuid import uuid4

from core import conversation_control as control
from core.memory.scope import MemoryScope
from core.memory.path_resolver import resolve_path
from core.sandbox import get_paths
from core.safe_write import safe_write_json, safe_write_text

_LOCKS: dict[str, threading.RLock] = {}


def lock(scope):
    return _LOCKS.setdefault(str(control._path(scope)), threading.RLock())


def _schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS deletions (
      deletion_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE,
      turn_id TEXT NOT NULL, credential TEXT NOT NULL, state TEXT NOT NULL,
      snapshot TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
      error TEXT NOT NULL DEFAULT '')''')
    db.execute('''CREATE TABLE IF NOT EXISTS visibility (
      turn_id TEXT PRIMARY KEY, deletion_id TEXT NOT NULL, hidden INTEGER NOT NULL,
      revision INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS blocked_sources (
      source_id TEXT NOT NULL, deletion_id TEXT NOT NULL,
      PRIMARY KEY(source_id,deletion_id))''')


def hidden_ids(scope) -> set[str]:
    if scope.domain != 'reality':
        return set()
    path = control._path(scope)
    if not path.exists():
        return set()
    try:
        with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=2) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'visibility' not in tables:
                return set()
            return {row[0] for row in db.execute('SELECT turn_id FROM visibility WHERE hidden=1')}
    except sqlite3.Error as exc:
        raise control.ControlError('conversation_visibility_unavailable',503) from exc


def hidden(scope, turn_id) -> bool:
    return bool(turn_id) and str(turn_id) in hidden_ids(scope)


def blocked(scope, source_id) -> bool:
    source_id = str(source_id or '')
    turn_id = re.sub(r':(?:user|assistant)$', '', source_id)
    if hidden(scope, turn_id):
        return True
    withdrawn=hidden_ids(scope)
    if withdrawn:
        for episode in _read(resolve_path(scope,'episodic'),[]):
            if str(episode.get('id'))==source_id and any(re.sub(r':(?:user|assistant)$','',str(event)) in withdrawn for event in (episode.get('source_event_ids') or [])):
                return True
    path = control._path(scope)
    if not path.exists():
        return False
    with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro',uri=True,timeout=2) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='blocked_sources'").fetchone():
            return False
        return bool(db.execute('''SELECT 1 FROM blocked_sources b JOIN visibility v
            ON v.deletion_id=b.deletion_id WHERE v.hidden=1 AND b.source_id=? LIMIT 1''',(source_id,)).fetchone())


def _dump(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True)


def _read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else deepcopy(default)


def _write(path, value):
    if not safe_write_json(path,value,keep_bak=False):
        raise OSError('conversation_projection_write_failed')


def _meta(row):
    return {key: row[key] for key in ('deletion_id','operation_id','turn_id','state','created_at','updated_at','error')}


def _event_rows(scope, turn_id):
    path=resolve_path(scope,'event_store')
    if not path.exists():
        return [], []
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        events=[dict(row) for row in db.execute("SELECT * FROM events WHERE uid=? AND char_id=? AND realm='reality' AND turn_id=?",(scope.uid,scope.character_id,turn_id))]
        finals=[]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='mobile_finals'").fetchone():
            finals=[dict(row) for row in db.execute('SELECT * FROM mobile_finals WHERE msg_id=?',(turn_id,))]
        return events,finals


def _derivatives(scope, turn_id, deletion_id):
    """Prepare exact entity patches before changing anything; unrelated entities stay out."""
    ids={turn_id+':user',turn_id+':assistant'}
    patches=[]; vectors=[turn_id,'recent-safe-v2:'+scope.uid]; unlinked=0
    for artifact in ('mid_term','episodic','storyline'):
        data=_read(resolve_path(scope,artifact), [] if artifact=='episodic' else {})
        if artifact=='episodic':
            objects=[(None,item) for item in data]
        elif artifact=='mid_term':
            objects=[(None,item) for item in data.get('events',[])]
        else:
            objects=[(arc.get('arc_id'),node) for arc in data.get('arcs',[]) for node in arc.get('nodes',[])]
        for parent,item in objects:
            sources=set(item.get('source_event_ids') or item.get('source_ids') or [])
            linked=item.get('source_turn_id')==turn_id or bool(ids & sources)
            if not sources and not item.get('source_turn_id'):
                unlinked+=1
            if not linked:
                continue
            key='id' if artifact=='episodic' else 'mid_id' if artifact=='mid_term' else 'node_id'
            if not item.get(key):
                raise control.ControlError('derived_record_missing_identity')
            after=deepcopy(item)
            if artifact=='episodic':
                invalid=set(after.get('invalid_source_event_ids') or []) | (sources & ids)
                after['invalid_source_event_ids']=sorted(invalid)
                # A mixed-source synthesized conclusion cannot safely retain
                # withdrawn prose by merely lowering its weight. Suppress this
                # projection until recomputed; all other evidence stays intact.
                after['status']='invalidated'
                vectors.append(str(item['id']))
                if invalid < sources:
                    after['needs_recompute']=True
            else:
                after['invalidated']={'reason':'chat_turn_deleted','deletion_id':deletion_id,'event_ids':sorted(ids & sources)}
            patches.append({'artifact':artifact,'parent':parent,'key':key,'id':item[key],'before':item,'after':after})
    return patches,vectors,unlinked


def _snapshot(scope, turn_id, deletion_id):
    from core.memory import short_term,event_log
    from core.conversation_execution import stopped_turns
    history=_read(short_term._history_path(scope.uid,char_id=scope.character_id),[])
    selected=[{'index':i,'entry':item} for i,item in enumerate(history) if item.get('_turn_id')==turn_id]
    logs=[]
    roots=[event_log._event_log_read_dir(scope.uid,char_id=scope.character_id),event_log._legacy_read_dir_if_eligible(scope.uid,scope.character_id)]
    base=get_paths()._base.resolve()
    for root in {p for p in roots if p is not None}:
        for path in root.glob('????-??-??.md'):
            if not path.resolve().is_relative_to(base):
                raise control.ControlError('unsafe_log_path')
            blocks=event_log._split_blocks(path.read_text(encoding='utf-8'))
            matched=[block for block in blocks if event_log._block_key(block)=='turn_id:'+turn_id]
            if matched:
                logs.append({'relative':str(path.resolve().relative_to(base)),'blocks':matched})
    long_data=_read(short_term._long_messages_path(scope.uid,char_id=scope.character_id),{})
    long_messages={seq:item for seq,item in long_data.get('messages',{}).items() if item.get('turn_id')==turn_id}
    events,finals=_event_rows(scope,turn_id)
    stopped=[row for row in stopped_turns(scope) if row['turn_id']==turn_id]
    if not selected and not logs and not events and not stopped:
        raise control.ControlError('turn_not_found',404)
    if any(row['redaction_state']=='tombstoned' for row in events):
        raise control.ControlError('turn_already_withdrawn')
    patches,vectors,unlinked=_derivatives(scope,turn_id,deletion_id)
    return {'version':1,'history':selected,'long_messages':long_messages,'logs':logs,'events':events,'finals':finals,
            'stopped':stopped,'patches':patches,'vectors':vectors,'unlinked':unlinked,
            'before_turn':next((item.get('_turn_id') for item in reversed(history[:selected[0]['index']]) if item.get('_turn_id')),None) if selected else None,
            'after_turn':next((item.get('_turn_id') for item in history[selected[-1]['index']+1:] if item.get('_turn_id')),None) if selected else None}


def _objects(data, patch):
    if patch['artifact']=='episodic':
        return data
    if patch['artifact']=='mid_term':
        return data.get('events',[])
    return next((arc.get('nodes',[]) for arc in data.get('arcs',[]) if arc.get('arc_id')==patch['parent']),[])


def _apply_patches(scope, patches, *, restore=False, dry=False):
    grouped={}
    for patch in patches:
        artifact=patch['artifact']
        data=grouped.setdefault(artifact,_read(resolve_path(scope,artifact),[] if artifact=='episodic' else {}))
        objects=_objects(data,patch)
        found=next((i for i,item in enumerate(objects) if item.get(patch['key'])==patch['id']),None)
        expected=patch['after'] if restore else patch['before']
        desired=patch['before'] if restore else patch['after']
        if found is None or objects[found] not in (expected,desired):
            raise control.ControlError('restore_conflict' if restore else 'projection_conflict')
        objects[found]=deepcopy(desired)
    if not dry:
        for artifact,data in grouped.items():
            _write(resolve_path(scope,artifact),data)
            try:
                from core.memory.provenance_log import append
                for patch in patches:
                    if patch['artifact']==artifact:
                        append(scope.uid,scope.character_id,artifact=artifact,field=str(patch['id']),trigger_signal='chat_turn_restored' if restore else 'chat_turn_deleted')
            except Exception:
                pass
        if 'episodic' in grouped:
            from core.memory.episodic_memory import _rebuild_index
            _rebuild_index(scope.uid,grouped['episodic'],char_id=scope.character_id)


def _apply(scope, turn_id, snapshot):
    from core.memory import short_term,event_log,vector_store
    history=_read(short_term._history_path(scope.uid,char_id=scope.character_id),[])
    _write(short_term._history_write_path(scope.uid,char_id=scope.character_id),[item for item in history if item.get('_turn_id')!=turn_id])
    long_path=short_term._long_messages_path(scope.uid,char_id=scope.character_id)
    if snapshot.get('long_messages'):
        long_data=_read(long_path,{})
        for seq in snapshot['long_messages']:
            long_data.get('messages',{}).pop(seq,None)
        _write(long_path,long_data)
    base=get_paths()._base.resolve()
    for saved in snapshot['logs']:
        path=(base/saved['relative']).resolve()
        if not path.is_relative_to(base):
            raise control.ControlError('unsafe_log_path')
        blocks=event_log._split_blocks(path.read_text(encoding='utf-8')) if path.exists() else []
        text='\n'.join('\n'.join(block) for block in blocks if event_log._block_key(block)!='turn_id:'+turn_id)
        if not safe_write_text(path,text):
            raise OSError('chat_log_write_failed')
        if not text.strip():
            path.unlink(missing_ok=True)
    if snapshot['events']:
        with sqlite3.connect(resolve_path(scope,'event_store')) as db:
            db.execute("UPDATE events SET raw_payload_json='',raw_text='',visible_text='',memory_text='',media_refs_json='[]',redaction_state='tombstoned' WHERE uid=? AND char_id=? AND realm='reality' AND turn_id=?",(scope.uid,scope.character_id,turn_id))
    _apply_patches(scope,snapshot['patches'])
    with control.connection(scope) as state_db:
        if state_db.execute("SELECT 1 FROM sqlite_master WHERE name='stopped_turns'").fetchone():
            state_db.execute('DELETE FROM stopped_turns WHERE turn_id=?',(turn_id,))
    queue_path=get_paths().mobile_queue()
    if queue_path.exists():
        queue=_read(queue_path,[])
        _write(queue_path,[item for item in queue if not (str(item.get('user_id'))==scope.uid and item.get('char_id')==scope.character_id and (item.get('turn_id') or item.get('id'))==turn_id)])
    if vector_store._db_path(scope.uid,scope.character_id).exists():
        keys=vector_store.read_projection_keys(scope.uid,scope.character_id)
        for source_id in snapshot['vectors']:
            source='event_log' if source_id in {turn_id,'recent-safe-v2:'+scope.uid} else 'episodic'
            if (source,source_id) in keys and not vector_store.delete(scope.uid,scope.character_id,source,source_id):
                raise OSError('vector_cleanup_pending')
    # Dossier conclusions are suppressed through the existing exact evidence
    # contract. Restoration requires recomputation, never an old conclusion copy.
    from core.memory.dossiers import invalidate_source
    for event in snapshot['events']:
        invalidate_source(scope,event['event_id'],reason='chat_turn_deleted')
    try:
        from core.memory.provenance_log import append
        append(scope.uid,scope.character_id,artifact='chat_turn',field=turn_id,trigger_signal='chat_turn_deleted')
    except Exception:
        pass  # Existing provenance contract is explicitly fail-open.



async def delete(scope, *, turn_id, operation_id, label, expected_revision=None):
    from core.conversation_gate import conversation_lock
    from core.memory.locks import uid_lock
    if not operation_id or len(operation_id)>128:
        raise control.ControlError('invalid_operation_id',422)
    if conversation_lock(scope.uid).locked():
        raise control.ControlError('conversation_busy')
    async with conversation_lock(scope.uid),uid_lock(scope.uid):
        with lock(scope),control.connection(scope) as db:
            _schema(db)
            old=db.execute('SELECT * FROM deletions WHERE operation_id=?',(operation_id,)).fetchone()
            if old:
                if old['turn_id']!=turn_id:
                    raise control.ControlError('operation_payload_conflict')
                visibility=db.execute('SELECT revision FROM visibility WHERE deletion_id=?',(old['deletion_id'],)).fetchone()
                return {**_meta(old),'revision':visibility['revision'] if visibility else 0,
                        'manual_review':['user_identity','important_facts','user_facts','relationship_facts']}
            current=db.execute('SELECT * FROM visibility WHERE turn_id=?',(turn_id,)).fetchone()
            if current and current['hidden']:
                raise control.ControlError('turn_already_deleted')
            revision=current['revision'] if current else 0
            if expected_revision is not None and revision!=expected_revision:
                raise control.ControlError('revision_conflict')
            deletion_id=uuid4().hex
            snapshot=_snapshot(scope,turn_id,deletion_id)
            snapshot['previous_visibility_revision']=revision
            now=time.time()
            db.execute('INSERT INTO deletions VALUES(?,?,?,?,?,?,?,?,?)',(deletion_id,operation_id,turn_id,control.credential_id(label),'prepared',_dump(snapshot),now,now,''))
            db.commit()  # Archive must exist before the destructive visibility fence.
            rev=control._mutation(db,'turn_deleted',{'turn_id':turn_id,'deletion_id':deletion_id,'state':'applying'})
            db.execute('INSERT OR REPLACE INTO visibility VALUES(?,?,1,?)',(turn_id,deletion_id,rev))
            db.executemany('INSERT OR IGNORE INTO blocked_sources VALUES(?,?)',[(sid,deletion_id) for sid in snapshot['vectors']])
            db.execute("UPDATE deletions SET state='applying' WHERE deletion_id=?",(deletion_id,))
            db.commit()
            try:
                _apply(scope,turn_id,snapshot)
                state,error='completed',''
            except Exception:
                state,error='repair_pending','projection_write_failed'
            db.execute('UPDATE deletions SET state=?,error=?,updated_at=? WHERE deletion_id=?',(state,error,time.time(),deletion_id))
            control._mutation(db,'turn_deleted',{'turn_id':turn_id,'deletion_id':deletion_id,'state':state})
            row=db.execute('SELECT * FROM deletions WHERE deletion_id=?',(deletion_id,)).fetchone()
            return {**_meta(row),'revision':rev,'manual_review':['user_identity','important_facts','user_facts','relationship_facts'],'unlinked':snapshot['unlinked'],'index_state':'withdrawn'}


def list_deleted(scope, *, before=None, limit=30):
    with control.connection(scope) as db:
        _schema(db)
        rows=db.execute('SELECT * FROM deletions WHERE (? IS NULL OR created_at<?) ORDER BY created_at DESC LIMIT ?',(before,before,min(max(limit,1),100))).fetchall()
        items=[]
        for row in rows:
            visible=db.execute('SELECT revision FROM visibility WHERE deletion_id=?',(row['deletion_id'],)).fetchone()
            items.append({**_meta(row),'revision':visible['revision'] if visible else 0})
        return {'deletions':items,'next_before':rows[-1]['created_at'] if len(rows)==limit else None}


def detail(scope, deletion_id):
    with control.connection(scope) as db:
        _schema(db)
        row=db.execute('SELECT * FROM deletions WHERE deletion_id=?',(deletion_id,)).fetchone()
        if not row:
            raise control.ControlError('deletion_not_found',404)
        snapshot=json.loads(row['snapshot'])
        visible=db.execute('SELECT revision FROM visibility WHERE deletion_id=?',(deletion_id,)).fetchone()
        return {**_meta(row),'revision':visible['revision'] if visible else 0,'history':snapshot['history'],'events':snapshot['events'],
                'stopped':snapshot['stopped'],'manual_review':['user_identity','important_facts','user_facts','relationship_facts']}


async def restore(scope, *, deletion_id, operation_id, expected_revision):
    from core.conversation_gate import conversation_lock
    from core.memory.locks import uid_lock
    if not operation_id or len(operation_id)>128:
        raise control.ControlError('invalid_operation_id',422)
    if conversation_lock(scope.uid).locked():
        raise control.ControlError('conversation_busy')
    async with conversation_lock(scope.uid),uid_lock(scope.uid):
        with lock(scope),control.connection(scope) as db:
            _schema(db)
            db.execute('CREATE TABLE IF NOT EXISTS restore_operations (operation_id TEXT PRIMARY KEY,deletion_id TEXT NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL)')
            prior=db.execute('SELECT * FROM restore_operations WHERE operation_id=?',(operation_id,)).fetchone()
            if prior and (prior['deletion_id']!=deletion_id or prior['revision']!=expected_revision):
                raise control.ControlError('operation_payload_conflict')
            row=db.execute('SELECT * FROM deletions WHERE deletion_id=?',(deletion_id,)).fetchone()
            if not row:
                raise control.ControlError('deletion_not_found',404)
            if row['state']=='restored':
                return {**_meta(row),'index_state':'index_pending'}
            visibility=db.execute('SELECT * FROM visibility WHERE turn_id=?',(row['turn_id'],)).fetchone()
            if not visibility or visibility['deletion_id']!=deletion_id or visibility['revision']!=expected_revision:
                raise control.ControlError('revision_conflict')
            if row['state'] not in {'completed','restoring','restore_pending'}:
                raise control.ControlError('repair_required')
            snapshot=json.loads(row['snapshot']);turn_id=row['turn_id']
            _validate_restore(scope,turn_id,snapshot)
            db.execute("INSERT OR REPLACE INTO restore_operations VALUES(?,?,?,'restoring')",(operation_id,deletion_id,expected_revision))
            db.execute("UPDATE deletions SET state='restoring' WHERE deletion_id=?",(deletion_id,))
            db.commit()
            try:
                _restore_apply(scope,turn_id,snapshot)
            except Exception:
                db.execute("UPDATE deletions SET state='restore_pending',error='restore_projection_failed',updated_at=? WHERE deletion_id=?",(time.time(),deletion_id))
                db.commit()
                raise control.ControlError('restore_pending',503) from None
            rev=control._mutation(db,'turn_restored',{'turn_id':turn_id,'deletion_id':deletion_id,'state':'restored','index_state':'index_pending'})
            db.execute('UPDATE visibility SET hidden=0,revision=? WHERE turn_id=?',(rev,turn_id))
            db.execute("UPDATE deletions SET state='restored',error='',updated_at=? WHERE deletion_id=?",(time.time(),deletion_id))
            db.execute("UPDATE restore_operations SET state='restored' WHERE operation_id=?",(operation_id,))
            return {**_meta(db.execute('SELECT * FROM deletions WHERE deletion_id=?',(deletion_id,)).fetchone()),'revision':rev,'index_state':'index_pending','dossiers':'recompute_required'}


def _validate_restore(scope,turn_id,snapshot):
    from core.memory import short_term,event_log
    _apply_patches(scope,snapshot['patches'],restore=True,dry=True)
    long_data=_read(short_term._long_messages_path(scope.uid,char_id=scope.character_id),{})
    for seq,item in snapshot.get('long_messages',{}).items():
        if seq in long_data.get('messages',{}) and long_data['messages'][seq]!=item:
            raise control.ControlError('restore_conflict')
    history=_read(short_term._history_path(scope.uid,char_id=scope.character_id),[])
    present=[item for item in history if item.get('_turn_id')==turn_id]
    if present and present!=[item['entry'] for item in snapshot['history']]:
        raise control.ControlError('restore_conflict')
    if snapshot['events']:
        with sqlite3.connect(resolve_path(scope,'event_store')) as ledger:
            ledger.row_factory=sqlite3.Row
            for saved in snapshot['events']:
                current=ledger.execute('SELECT * FROM events WHERE event_id=?',(saved['event_id'],)).fetchone()
                tombstone={**saved,'redaction_state':'tombstoned','raw_payload_json':'','raw_text':'','visible_text':'','memory_text':'','media_refs_json':'[]'}
                if current is None or dict(current) not in (saved,tombstone):
                    raise control.ControlError('restore_conflict')
    base=get_paths()._base.resolve()
    for saved in snapshot['logs']:
        path=(base/saved['relative']).resolve()
        if not path.is_relative_to(base):
            raise control.ControlError('unsafe_log_path')
        blocks=event_log._split_blocks(path.read_text(encoding='utf-8')) if path.exists() else []
        present=[b for b in blocks if event_log._block_key(b)=='turn_id:'+turn_id]
        def canonical(blocks):
            return ['\n'.join(line for line in block if not re.fullmatch(r'# \d{4}-\d{2}-\d{2}',line)).strip() for block in blocks]
        if present and canonical(present)!=canonical(saved['blocks']):
            raise control.ControlError('restore_conflict')


def _restore_apply(scope,turn_id,snapshot):
    from core.memory import short_term,event_log
    history=_read(short_term._history_path(scope.uid,char_id=scope.character_id),[])
    if not any(item.get('_turn_id')==turn_id for item in history):
        before=snapshot.get('before_turn');after=snapshot.get('after_turn')
        next_index=next((i for i,item in enumerate(history) if after and item.get('_turn_id')==after),None)
        prev_indices=[i for i,item in enumerate(history) if before and item.get('_turn_id')==before]
        index=next_index if next_index is not None else prev_indices[-1]+1 if prev_indices else min(snapshot['history'][0]['index'],len(history)) if snapshot['history'] else 0
        history[index:index]=[saved['entry'] for saved in snapshot['history']]
    _write(short_term._history_write_path(scope.uid,char_id=scope.character_id),history)
    if snapshot.get('long_messages'):
        long_path=short_term._long_messages_path(scope.uid,char_id=scope.character_id)
        long_data=_read(long_path,{})
        long_data.setdefault('messages',{}).update(snapshot['long_messages'])
        _write(long_path,long_data)
    base=get_paths()._base.resolve()
    for saved in snapshot['logs']:
        path=(base/saved['relative']).resolve()
        if not path.is_relative_to(base):
            raise control.ControlError('unsafe_log_path')
        old=path.read_text(encoding='utf-8') if path.exists() else ''
        restored='\n'.join('\n'.join(block) for block in saved['blocks'])
        if not safe_write_text(path,event_log._merge_day_texts(old,restored)):
            raise OSError('chat_log_restore_failed')
    _apply_patches(scope,snapshot['patches'],restore=True)
    if snapshot['events']:
        with sqlite3.connect(resolve_path(scope,'event_store')) as ledger:
            for saved in snapshot['events']:
                columns=[key for key in saved if key!='event_id']
                ledger.execute('UPDATE events SET '+','.join('"'+key+'"=?' for key in columns)+' WHERE event_id=?',tuple(saved[key] for key in columns)+(saved['event_id'],))
            for final in snapshot['finals']:
                keys=list(final)
                existing=ledger.execute('SELECT envelope FROM mobile_finals WHERE msg_id=?',(final['msg_id'],)).fetchone()
                if existing and existing[0]!=final['envelope']:
                    raise control.ControlError('restore_conflict')
                if not existing:
                    ledger.execute('INSERT INTO mobile_finals('+','.join(keys)+') VALUES('+','.join('?' for key in keys)+')',tuple(final.values()))
    # Stopped display records are not learned turns and stay outside the ledger.
    if snapshot['stopped']:
        from core.conversation_execution import _stopped_schema
        with control.connection(scope) as db:
            _stopped_schema(db)
            for saved in snapshot['stopped']:
                keys=list(saved)
                db.execute('INSERT OR IGNORE INTO stopped_turns('+','.join(keys)+') VALUES('+','.join('?' for key in keys)+')',tuple(saved.values()))


async def repair(scope, deletion_id):
    from core.conversation_gate import conversation_lock
    from core.memory.locks import uid_lock
    if conversation_lock(scope.uid).locked():
        raise control.ControlError('conversation_busy')
    async with conversation_lock(scope.uid),uid_lock(scope.uid):
        with lock(scope),control.connection(scope) as db:
            _schema(db)
            row=db.execute('SELECT * FROM deletions WHERE deletion_id=?',(deletion_id,)).fetchone()
            if not row:
                raise control.ControlError('deletion_not_found',404)
            if row['state']=='completed':
                return _meta(row)
            if row['state'] not in {'prepared','applying','repair_pending'}:
                raise control.ControlError('operation_state_conflict')
            visibility=db.execute('SELECT * FROM visibility WHERE turn_id=?',(row['turn_id'],)).fetchone()
            snapshot=json.loads(row['snapshot'])
            if visibility and visibility['deletion_id']!=deletion_id and visibility['revision']!=snapshot.get('previous_visibility_revision',0):
                raise control.ControlError('revision_conflict')
            if not visibility or not visibility['hidden']:
                rev=control._mutation(db,'turn_deleted',{'turn_id':row['turn_id'],'deletion_id':deletion_id,'state':'applying'})
                db.execute('INSERT OR REPLACE INTO visibility VALUES(?,?,1,?)',(row['turn_id'],deletion_id,rev))
            db.executemany('INSERT OR IGNORE INTO blocked_sources VALUES(?,?)',[(sid,deletion_id) for sid in snapshot['vectors']])
            db.commit()
            try:
                _apply(scope,row['turn_id'],snapshot)
            except Exception:
                raise control.ControlError('repair_pending',503) from None
            db.execute("UPDATE deletions SET state='completed',error='',updated_at=? WHERE deletion_id=?",(time.time(),deletion_id))
            control._mutation(db,'turn_deleted',{'turn_id':row['turn_id'],'deletion_id':deletion_id,'state':'completed'})
            return _meta(db.execute('SELECT * FROM deletions WHERE deletion_id=?',(deletion_id,)).fetchone())



def filter_log(scope, text):
    from core.memory.event_log import _split_blocks,_block_key
    hidden = hidden_ids(scope)
    if not hidden:
        return text
    return '\n'.join('\n'.join(block) for block in _split_blocks(text)
                     if _block_key(block).removeprefix('turn_id:') not in hidden)


def vector_write_guard(fn):
    from functools import wraps
    @wraps(fn)
    def guarded(uid, char_id, source, source_id, *args, **kwargs):
        scope=MemoryScope.reality_scope(str(uid),char_id)
        with lock(scope):
            if blocked(scope,source_id):
                return
            return fn(uid,char_id,source,source_id,*args,**kwargs)
    return guarded


def mask_derivatives(scope, objects, kind):
    """Late computations may save old evidence: enforce visibility on every read."""
    withdrawn=hidden_ids(scope)
    if not withdrawn:
        return objects
    objects=deepcopy(objects)
    for item in objects:
        source_turn=item.get('source_turn_id')
        sources=item.get('source_event_ids') or item.get('source_ids') or []
        hit=source_turn in withdrawn or any(re.sub(r':(?:user|assistant)$','',str(source)) in withdrawn for source in sources)
        if hit:
            if kind=='episodic':
                item['status']='invalidated'
            else:
                item.setdefault('invalidated',{'reason':'chat_turn_deleted'})
    return objects
