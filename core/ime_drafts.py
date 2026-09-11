"""Passive JadeBoard v2 inbox. No model, memory or scheduler consumers."""
from contextlib import closing
import sqlite3
import threading
import time

from core.sandbox import get_paths

_LOCK = threading.RLock()
RETENTION_MS = 3 * 60 * 60 * 1000
TEST_PACKAGE = 'com.chacha.jadeime.sync_test'


def summary(*, device_id: str = '', now_ms: int | None = None):
    """Retained counts independent of pagination; never read content."""
    result = {'draft_count': 0, 'test_count': 0, 'latest_draft_updated_at': None}
    cutoff = (now_ms if now_ms is not None else int(time.time() * 1000)) - RETENTION_MS
    with _LOCK:
        path = get_paths().ime_drafts_db()
        if not path.exists():
            return result
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            where = 'updated_at > ?' + (' AND device_id = ?' if device_id else '')
            args = (cutoff, device_id) if device_id else (cutoff,)
            for package, count, latest in db.execute(
                'SELECT app_package, COUNT(*), MAX(updated_at) FROM drafts WHERE ' + where + ' GROUP BY app_package', args
            ):
                if package == TEST_PACKAGE:
                    result['test_count'] += count
                else:
                    result['draft_count'] += count
                    result['latest_draft_updated_at'] = max(result['latest_draft_updated_at'] or 0, latest)
    return result


def receive(device_id: str, rows: list[dict], *, now_ms: int | None = None):
    cutoff = (now_ms if now_ms is not None else int(time.time() * 1000)) - RETENTION_MS
    with _LOCK:
        path = get_paths().ime_drafts_db()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=2)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS drafts (
                device_id TEXT NOT NULL, id INTEGER NOT NULL, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, revision INTEGER NOT NULL, app_package TEXT NOT NULL,
                source TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY(device_id, id))''')
            db.execute('DELETE FROM drafts WHERE updated_at <= ?', (cutoff,))
            for row in rows:
                if row['updated_at'] <= cutoff:
                    continue
                db.execute('''INSERT INTO drafts VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(device_id, id) DO UPDATE SET
                    created_at=excluded.created_at, updated_at=excluded.updated_at,
                    revision=excluded.revision, app_package=excluded.app_package,
                    source=excluded.source, content=excluded.content
                    WHERE excluded.revision > drafts.revision''',
                    (device_id, *(row[k] for k in ('id', 'created_at', 'updated_at', 'revision', 'app_package', 'source', 'content'))))


def query(*, device_id: str = '', limit: int = 50, before: int | None = None, now_ms: int | None = None):
    cutoff = (now_ms if now_ms is not None else int(time.time() * 1000)) - RETENTION_MS
    with _LOCK:
        path = get_paths().ime_drafts_db()
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            clauses, args = ['updated_at > ?'], [cutoff]
            if device_id:
                clauses.append('device_id = ?')
                args.append(device_id)
            if before is not None:
                clauses.append('rowid < ?')
                args.append(before)
            return [dict(row) for row in db.execute(
                'SELECT rowid AS seq, * FROM drafts WHERE ' + ' AND '.join(clauses) + ' ORDER BY rowid DESC LIMIT ?',
                (*args, min(200, max(1, limit))))]
