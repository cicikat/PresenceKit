"""User-owned bookkeeping ledger. No character database or memory side effects."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import threading
import uuid

from core.sandbox import get_paths
from core.config_loader import get_config


_connection_lock = threading.Lock()


def settings():
    cfg = get_config().get('finance_ingest', {})
    enabled = cfg.get('enabled') is True
    return {'schema_version': 1, 'enabled': enabled, 'effective': enabled,
            'character_readable': cfg.get('character_readable') is True,
            'blocking_reason': '' if enabled else 'disabled'}


@contextmanager
def connection():
    # Windows path resolution can race the first directory/file creation.
    # Serialize connection setup only; SQLite owns transaction concurrency.
    with _connection_lock:
        path = get_paths().finance_db()
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS candidates(owner TEXT NOT NULL,id TEXT NOT NULL,event TEXT NOT NULL,
          revision INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(owner,id),UNIQUE(owner,event));
        CREATE TABLE IF NOT EXISTS operations(owner TEXT NOT NULL,id TEXT NOT NULL,digest TEXT NOT NULL,
          receipt TEXT NOT NULL,PRIMARY KEY(owner,id));
        CREATE TABLE IF NOT EXISTS versions(owner TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
          data TEXT NOT NULL,PRIMARY KEY(owner,id,revision));
        PRAGMA user_version=1;
        ''')
        yield db
    finally:
        db.close()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class Conflict(Exception):
    def __init__(self, record=None):
        self.record = record


def sync(owner, body):
    payload = encode(body)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    cid, opid = body['candidate_id'], body['operation_id']
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            previous = db.execute('SELECT digest,receipt FROM operations WHERE owner=? AND id=?', (owner, opid)).fetchone()
            if previous:
                if previous['digest'] != digest:
                    raise Conflict()
                result = json.loads(previous['receipt'])
                db.commit()
                return result
            row = db.execute('SELECT revision,data,event FROM candidates WHERE owner=? AND id=?', (owner, cid)).fetchone()
            current = json.loads(row['data']) if row else None
            if body['base_revision'] != (row['revision'] if row else 0):
                raise Conflict(current)
            data = dict(body['record'])
            if row and (row['event'] != data['source_event_key'] or any(data[k] != current[k] for k in ('source_app', 'fingerprint', 'occurred_at', 'observed_at', 'timezone_offset_minutes'))):
                raise Conflict(current)
            collision = db.execute('SELECT data FROM candidates WHERE owner=? AND event=? AND id<>?', (owner, data['source_event_key'], cid)).fetchone()
            if collision:
                raise Conflict(json.loads(collision['data']))
            # The server owns transaction identity; edits, revocation and deletion keep it.
            transaction_id = current.get('linked_transaction_id') if current else None
            if data['status'] == 'confirmed' and not transaction_id:
                transaction_id = uuid.uuid4().hex
            revision = body['base_revision'] + 1
            data.update(candidate_id=cid, revision=revision, linked_transaction_id=transaction_id)
            db.execute('INSERT INTO candidates VALUES(?,?,?,?,?) ON CONFLICT(owner,id) DO UPDATE SET revision=excluded.revision,data=excluded.data',
                       (owner, cid, data['source_event_key'], revision, encode(data)))
            db.execute('INSERT INTO versions VALUES(?,?,?,?)', (owner, cid, revision, encode(data)))
            receipt = {'operation_id': opid, 'candidate_id': cid, 'revision': revision, 'record': data}
            db.execute('INSERT INTO operations VALUES(?,?,?,?)', (owner, opid, digest, encode(receipt)))
            db.commit()
            return receipt
        except BaseException:
            db.rollback()
            raise


def get(owner, cid):
    with connection() as db:
        row = db.execute('SELECT data FROM candidates WHERE owner=? AND id=?', (owner, cid)).fetchone()
        return json.loads(row['data']) if row else None


def listing(owner, *, cursor='', limit=50, month='', category='', confirmed_only=False):
    with connection() as db:
        rows = db.execute('SELECT id,data FROM candidates WHERE owner=? AND id>? ORDER BY id LIMIT ?', (owner, cursor, limit + 1)).fetchall()
    page = rows[:limit]
    data = [json.loads(row['data']) for row in page]
    if confirmed_only:
        data = [r for r in data if r['status'] == 'confirmed']
    if month:
        from datetime import datetime, timedelta, timezone
        data = [r for r in data if datetime.fromtimestamp(r['occurred_at'] / 1000, timezone(timedelta(minutes=r['timezone_offset_minutes']))).strftime('%Y-%m') == month]
    if category:
        data = [r for r in data if r['category'] == category]
    return {'records': data, 'next_cursor': page[-1]['id'] if len(rows) > limit else ''}


def history(owner, cid):
    with connection() as db:
        return [json.loads(row[0]) for row in db.execute('SELECT data FROM versions WHERE owner=? AND id=? ORDER BY revision DESC LIMIT 100', (owner, cid))]


def observe(owner):
    with connection() as db:
        counts = dict(db.execute("SELECT json_extract(data,'$.status'),count(*) FROM candidates WHERE owner=? GROUP BY json_extract(data,'$.status')", (owner,)))
        operations = db.execute('SELECT count(*) FROM operations WHERE owner=?', (owner,)).fetchone()[0]
    return {**settings(), 'counts': counts, 'operation_count': operations}
