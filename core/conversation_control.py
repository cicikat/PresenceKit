"""Durable, scoped Reality execution metadata. Never an LLM or memory store.

SQLite transactions arbitrate stop versus commit; no conversation lock is held
by the control API. A boot identity makes orphaned work explicitly interrupted.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
import hashlib
import json
import sqlite3
import time
from uuid import uuid4

from core.memory.scope import MemoryScope
from core.sandbox import get_paths

_BOOT = uuid4().hex
ACTIVE = frozenset({'queued', 'running', 'stop_requested', 'committing'})
TERMINAL = frozenset({'completed', 'stopped', 'failed', 'interrupted'})
CURRENT: ContextVar[tuple[MemoryScope, str] | None] = ContextVar('conversation_execution', default=None)


class ControlError(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def _path(scope: MemoryScope):
    if scope.domain != 'reality' or not scope.uid or not scope.character_id:
        raise ControlError('invalid_conversation_scope', 422)
    return get_paths().conversation_control(scope.uid, char_id=scope.character_id)


@contextmanager
def connection(scope: MemoryScope):
    path = _path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('''CREATE TABLE IF NOT EXISTS executions (
            execution_id TEXT PRIMARY KEY, credential TEXT NOT NULL,
            request_id TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL,
            boot TEXT NOT NULL, turn_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(credential, request_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS mutations (
            revision INTEGER PRIMARY KEY AUTOINCREMENT,
            mutation_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
            payload TEXT NOT NULL, created_at REAL NOT NULL)''')
        db.execute('PRAGMA user_version=1')
        initial_revision=db.execute('SELECT COALESCE(MAX(revision),0) FROM mutations').fetchone()[0]
        # A runtime owns one private-state directory. Foreign boots are never
        # resumed or re-executed from retained metadata.
        orphaned = db.execute("SELECT execution_id FROM executions WHERE boot!=? AND state IN ('queued','running','stop_requested','committing')", (_BOOT,)).fetchall()
        for row in orphaned:
            db.execute("UPDATE executions SET state='interrupted',updated_at=? WHERE execution_id=?", (time.time(), row['execution_id']))
            _mutation(db, 'execution_state', {'execution_id': row['execution_id'], 'state': 'interrupted'})
        db.commit()
        yield db
        db.commit()
        revision=db.execute('SELECT COALESCE(MAX(revision),0) FROM mutations').fetchone()[0]
        if revision>initial_revision:
            try:
                asyncio.get_running_loop().create_task(_notify(scope,revision))
            except RuntimeError:
                pass  # Sync readers recover through the durable state snapshot.

    except sqlite3.Error as exc:
        db.rollback()
        raise ControlError('conversation_store_unavailable', 503) from exc
    finally:
        db.close()


def credential_id(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _mutation(db, kind: str, payload: dict) -> int:
    cur = db.execute('INSERT INTO mutations(mutation_id,kind,payload,created_at) VALUES(?,?,?,?)',
                     (uuid4().hex, kind, json.dumps(payload, ensure_ascii=False), time.time()))
    return cur.lastrowid


def projection(row) -> dict:
    return {k: row[k] for k in ('execution_id', 'request_id', 'state', 'turn_id', 'created_at', 'updated_at')}


def admit(scope: MemoryScope, *, label: str, request_id: str, digest: str) -> dict:
    """Persist before any model/tool work; repeat IDs cannot execute twice."""
    credential = credential_id(label)
    with connection(scope) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM executions WHERE credential=? AND request_id=?', (credential, request_id)).fetchone()
        if row:
            if row['digest'] and row['digest'] != digest:
                raise ControlError('request_payload_conflict')
            if not row['digest']:
                db.execute('UPDATE executions SET digest=? WHERE execution_id=?', (digest, row['execution_id']))
            return projection(row)
        count = db.execute("SELECT COUNT(*) FROM executions WHERE state IN ('queued','running','stop_requested','committing')").fetchone()[0]
        if count >= 128:
            raise ControlError('conversation_execution_capacity', 429)
        now = time.time()
        execution_id = uuid4().hex
        db.execute('INSERT INTO executions(execution_id,credential,request_id,digest,state,boot,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                   (execution_id, credential, request_id, digest, 'queued', _BOOT, now, now))
        _mutation(db, 'execution_state', {'execution_id': execution_id, 'request_id': request_id, 'state': 'queued'})
        return projection(db.execute('SELECT * FROM executions WHERE execution_id=?', (execution_id,)).fetchone())


def transition(scope: MemoryScope, execution_id: str, state: str, *, turn_id: str = '') -> dict:
    allowed = {'running': {'queued'}, 'committing': {'running'},
               'completed': {'committing'}, 'stop_requested': {'queued', 'running'},
               'stopped': {'queued', 'running', 'stop_requested'}, 'failed': {'queued', 'running', 'committing'}}
    if state not in allowed:
        raise ControlError('invalid_execution_transition')
    with connection(scope) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM executions WHERE execution_id=?', (execution_id,)).fetchone()
        if not row:
            raise ControlError('execution_not_found', 404)
        if row['state'] != state:
            if row['state'] not in allowed[state]:
                raise ControlError('execution_state_conflict')
            db.execute('UPDATE executions SET state=?,turn_id=?,updated_at=? WHERE execution_id=?',
                       (state, turn_id or row['turn_id'], time.time(), execution_id))
            _mutation(db, 'execution_state', {'execution_id': execution_id, 'request_id': row['request_id'], 'turn_id': turn_id or row['turn_id'], 'state': state})
        return projection(db.execute('SELECT * FROM executions WHERE execution_id=?', (execution_id,)).fetchone())


def lookup(scope: MemoryScope, *, label: str, request_id: str) -> dict:
    with connection(scope) as db:
        row = db.execute('SELECT * FROM executions WHERE credential=? AND request_id=?', (credential_id(label), request_id)).fetchone()
        return projection(row) if row else {'request_id': request_id, 'state': 'unknown'}


def active(scope: MemoryScope) -> list[dict]:
    with connection(scope) as db:
        rows = db.execute("SELECT * FROM executions WHERE state IN ('queued','running','stop_requested','committing') ORDER BY created_at LIMIT 128").fetchall()
        return [projection(row) for row in rows]


def mutations(scope: MemoryScope, *, after: int = 0, limit: int = 100) -> dict:
    with connection(scope) as db:
        rows = db.execute('SELECT * FROM mutations WHERE revision>? ORDER BY revision LIMIT ?', (after, min(max(limit, 1), 200))).fetchall()
        return {'mutations': [{'revision': row['revision'], 'mutation_id': row['mutation_id'], 'kind': row['kind'], **json.loads(row['payload'])} for row in rows],
                'cursor': rows[-1]['revision'] if rows else after,
                'has_more': bool(rows and db.execute('SELECT 1 FROM mutations WHERE revision>? LIMIT 1', (rows[-1]['revision'],)).fetchone())}


async def _notify(scope,revision):
    """Body-free hint only; clients always re-read authoritative scoped state."""
    try:
        from core.mobile_realtime import publish
        from channels.desktop_ws import _send_json
        publish({'user_id':scope.uid,'char_id':scope.character_id,'domain':'reality',
                 '_conversation_control':True,'revision':revision})
        await _send_json({'type':'conversation_changed','char_id':scope.character_id,
                          'domain':'reality','revision':revision})
    except Exception:
        pass  # The independent durable cursor and state recover dropped hints.


def snapshot(scope):
    with connection(scope) as db:
        db.execute('BEGIN')
        revision=db.execute('SELECT COALESCE(MAX(revision),0) FROM mutations').fetchone()[0]
        active_rows=db.execute("SELECT * FROM executions WHERE state IN ('queued','running','stop_requested','committing') ORDER BY created_at LIMIT 128").fetchall()
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        visibility=[dict(row) for row in db.execute('SELECT turn_id,hidden,revision FROM visibility')] if 'visibility' in tables else []
        withdrawn=[row[0] for row in db.execute('SELECT e.request_id FROM executions e JOIN visibility v ON v.turn_id=e.turn_id WHERE v.hidden=1')] if 'visibility' in tables else []
        if 'turn_request_links' in tables:
            withdrawn=sorted(set(withdrawn)|{row[0] for row in db.execute('SELECT r.request_id FROM turn_request_links r JOIN visibility v ON v.turn_id=r.turn_id WHERE v.hidden=1')})
        terminal=db.execute("SELECT * FROM executions WHERE state IN ('stopped','interrupted') ORDER BY updated_at DESC LIMIT 128").fetchall()
        return {'revision':revision,'executions':[projection(row) for row in active_rows],'visibility':visibility,'recent_terminal':[projection(row) for row in terminal],'withdrawn_requests':withdrawn}
