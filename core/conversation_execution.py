"""Real cancellation of the executor behind transport shields.

Only explicit stop requests are converted to stopped results. Disconnects and
runtime shutdown do not claim user-requested cancellation. No memory writes.
"""
from __future__ import annotations
import asyncio
import json
import time
from uuid import uuid4

from core import conversation_control as control
from core.session_scope import request_digest, validate_request_id

_TASKS: dict[tuple[str, str], asyncio.Task] = {}
_PREFIX: dict[tuple[str, str], str] = {}
_INPUT: dict[tuple[str, str], tuple[str, list]] = {}


def _key(scope, execution_id):
    return str(control._path(scope)), execution_id


def _row(scope, execution_id):
    with control.connection(scope) as db:
        row = db.execute('SELECT * FROM executions WHERE execution_id=?', (execution_id,)).fetchone()
        if not row:
            raise control.ControlError('execution_not_found', 404)
        return control.projection(row)


def _stopped_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS stopped_turns (
        turn_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL, user_text TEXT NOT NULL,
        assistant_text TEXT NOT NULL, created_at REAL NOT NULL,
        media_refs TEXT NOT NULL DEFAULT '[]')''')
    if 'media_refs' not in {row[1] for row in db.execute('PRAGMA table_info(stopped_turns)')}:
        db.execute("ALTER TABLE stopped_turns ADD COLUMN media_refs TEXT NOT NULL DEFAULT '[]'")


def stopped_turns(scope) -> list[dict]:
    with control.connection(scope) as db:
        _stopped_schema(db)
        return [dict(row) for row in db.execute('SELECT * FROM stopped_turns ORDER BY created_at')]


def _stopped_result(scope, row, message=''):
    with control.connection(scope) as db:
        _stopped_schema(db)
        turn_id = row['turn_id'] or 'stopped_' + row['execution_id']
        # Prefix was filtered before display; remove any incomplete markup and
        # scrub it again before durable display. Never run memory extraction.
        text = _PREFIX.get(_key(scope, row['execution_id']), '')
        from core.control_markers import strip_control_markers
        from core.reality_output_guard import clean_reality_reply_text
        from core.response_processor import strip_render_tags
        from core.thinking import strip_think_tags
        import re
        text = re.sub(r'<[^>]*$', '', text)
        text = strip_render_tags(strip_control_markers(strip_think_tags(text)))
        text = clean_reality_reply_text(text, '')
        message, refs = _INPUT.get(_key(scope, row['execution_id']), (message, []))
        db.execute('INSERT OR IGNORE INTO stopped_turns VALUES(?,?,?,?,?,?,?)',
                   (turn_id, row['execution_id'], row['request_id'], message, text, row['created_at'], json.dumps(refs, ensure_ascii=False)))
        saved = db.execute('SELECT * FROM stopped_turns WHERE turn_id=?', (turn_id,)).fetchone()
        db.execute("UPDATE executions SET turn_id=? WHERE execution_id=? AND state='stopped'", (turn_id, row['execution_id']))
    if row['state'] != 'stopped':
        control.transition(scope, row['execution_id'], 'stopped', turn_id=turn_id)
    return {'status': 'stopped', 'state': 'stopped', 'turn_id': turn_id, 'msg_id': turn_id,
            'request_id': row['request_id'], 'reply': saved['assistant_text'],
            'char_id': scope.character_id, 'domain': 'reality', 'emotion': 'neutral',
            'execution_id': row['execution_id']}


def input_message(text: str, media_refs=None):
    current = control.CURRENT.get()
    if current:
        _INPUT[_key(*current)] = (text, list(media_refs or []))


def started():
    current = control.CURRENT.get()
    if current:
        control.transition(*current, 'running')


def begin_commit():
    current = control.CURRENT.get()
    if current:
        control.transition(*current, 'committing')


def prefix(piece: str):
    current = control.CURRENT.get()
    if current:
        key = _key(*current)
        # Bound the retained display prefix, not the provider generation budget.
        _PREFIX[key] = (_PREFIX.get(key, '') + piece)[:1024 * 1024]


async def execute(*, grant, request_id, payload, executor, allow_retry=False):
    rid = validate_request_id(request_id)
    scope = grant.memory_scope
    with control.connection(scope) as db:
        existing = db.execute('SELECT 1 FROM executions WHERE credential=? AND request_id=?', (control.credential_id(grant.token_label), rid)).fetchone()
    row = control.admit(scope, label=grant.token_label, request_id=rid, digest=request_digest(payload))
    if existing and row['state'] == 'failed' and allow_retry:
        # The outer session receipt authorizes retries only before side effects.
        with control.connection(scope) as db:
            db.execute("UPDATE executions SET state='queued',boot=?,updated_at=? WHERE execution_id=? AND state='failed'", (control._BOOT, time.time(), row['execution_id']))
        existing = False
        row['state'] = 'queued'
    if existing:
        if row['state'] == 'stopped':
            return _stopped_result(scope, row, str((payload or {}).get('message') or ''))
        raise control.ControlError('execution_' + row['state'])
    key = _key(scope, row['execution_id'])
    async def run():
        token = control.CURRENT.set((scope, row['execution_id']))
        try:
            return await executor()
        finally:
            control.CURRENT.reset(token)
    task = asyncio.create_task(run())
    _TASKS[key] = task
    try:
        result = await task
        state = _row(scope, row['execution_id'])['state']
        if state == 'stop_requested':
            return _stopped_result(scope, _row(scope, row['execution_id']), str((payload or {}).get('message') or ''))
        # Non-owner-chat adapters/test seams may not have a critical commit.
        if state == 'queued':
            control.transition(scope, row['execution_id'], 'running')
            state = 'running'
        if state == 'running':
            control.transition(scope, row['execution_id'], 'committing')
        control.transition(scope, row['execution_id'], 'completed', turn_id=str(result.get('turn_id') or ''))
        return result
    except asyncio.CancelledError:
        if _row(scope, row['execution_id'])['state'] == 'stop_requested':
            return _stopped_result(scope, _row(scope, row['execution_id']), str((payload or {}).get('message') or ''))
        control.transition(scope, row['execution_id'], 'failed')
        raise
    except BaseException:
        state = _row(scope, row['execution_id'])['state']
        if state == 'stop_requested':
            return _stopped_result(scope, _row(scope, row['execution_id']), str((payload or {}).get('message') or ''))
        if state not in control.TERMINAL:
            control.transition(scope, row['execution_id'], 'failed')
        raise
    finally:
        _TASKS.pop(key, None)
        _PREFIX.pop(key, None)
        _INPUT.pop(key, None)


def stop_execution(scope, execution_id):
    row = _row(scope, execution_id)
    if row['state'] in control.TERMINAL or row['state'] == 'committing':
        return row
    if row['state'] != 'stop_requested':
        row = control.transition(scope, execution_id, 'stop_requested')
    task = _TASKS.get(_key(scope, execution_id))
    if task and not task.done():
        # Cancel only once: repeated stop must not interrupt finally/close.
        if not task.cancelling():
            task.cancel()
    return row


def stop_request(scope, *, label, request_id):
    rid = validate_request_id(request_id)
    row = control.lookup(scope, label=label, request_id=rid)
    if row['state'] != 'unknown':
        return stop_execution(scope, row['execution_id'])
    # Persist bounded pre-admission intent so a late original POST cannot run.
    with control.connection(scope) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT COUNT(*) FROM executions WHERE digest='' AND created_at>?", (time.time()-86400,)).fetchone()[0] >= 128:
            raise control.ControlError('cancel_intent_capacity', 429)
        now = time.time()
        execution_id = uuid4().hex
        db.execute("INSERT OR IGNORE INTO executions(execution_id,credential,request_id,digest,state,boot,created_at,updated_at) VALUES(?,?,?,'','stopped',?,?,?)",
                   (execution_id, control.credential_id(label), rid, control._BOOT, now, now))
        control._mutation(db, 'execution_state', {'execution_id': execution_id, 'request_id': rid, 'state': 'stopped'})
    return control.lookup(scope, label=label, request_id=rid)
