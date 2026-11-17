"""Append-only projection intents in the existing bounded memory recovery DB.

An intent is never recalled as evidence. Recovery only finishes bytes proven to
be a prefix of the admitted append; external edits fail closed, never truncate.
"""
from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import time

from core.post_process.memory_outbox import _connect
from core.sandbox import get_paths


def _db():
    db = _connect()
    db.execute("""CREATE TABLE IF NOT EXISTS event_log_intents (
        path TEXT PRIMARY KEY, offset INTEGER NOT NULL, prefix_hash BLOB NOT NULL,
        chunk BLOB NOT NULL, created_at REAL NOT NULL)""")
    return db


def _validate(path):
    path = Path(path).resolve()
    path.relative_to(get_paths()._base.resolve())
    if path.suffix != ".md" or path.parent.name != "event_log":
        raise ValueError("invalid event log projection")
    return path


def _prefix(path, offset):
    if not path.exists():
        if offset:
            raise OSError("event_log_projection_missing")
        return sha256(b"").digest()
    with path.open("rb") as stream:
        if stream.seek(0, 2) < offset:
            raise OSError("event_log_projection_truncated")
        stream.seek(max(0, offset - 128))
        return sha256(stream.read(min(offset, 128))).digest()


def recover_file(path):
    path = _validate(path)
    with closing(_db()) as db:
        row = db.execute("SELECT offset,prefix_hash,chunk FROM event_log_intents WHERE path=?", (str(path),)).fetchone()
        if row is None:
            return
        offset, digest, chunk = row
        if _prefix(path, offset) != digest:
            raise OSError("event_log_projection_prefix_changed")
        tail = b""
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(offset)
                tail = stream.read(len(chunk) + 1)
        if not tail.startswith(chunk):
            if not chunk.startswith(tail):
                raise OSError("event_log_projection_tail_conflict")
            with open(path, "ab") as stream:
                stream.write(chunk[len(tail):])
                stream.flush()
                os.fsync(stream.fileno())
        with db:
            db.execute("DELETE FROM event_log_intents WHERE path=?", (str(path),))


def append(path, chunk):
    path = _validate(path)
    recover_file(path)
    offset = path.stat().st_size if path.exists() else 0
    digest = _prefix(path, offset)
    with closing(_db()) as db, db:
        if db.execute("SELECT count(*) FROM event_log_intents").fetchone()[0] >= 1000:
            raise OSError("event_log_projection_capacity")
        db.execute("INSERT INTO event_log_intents VALUES (?,?,?,?,?)",
            (str(path), offset, digest, chunk.encode("utf-8"), time.time()))
    recover_file(path)


def recover_pending(limit=32):
    """Startup budget; unresolved conflicts remain visible and block that file."""
    if not get_paths().memory_task_outbox().exists():
        return
    from core.memory.event_log_index import lock
    with lock, closing(_db()) as db:
        rows = db.execute("SELECT path FROM event_log_intents ORDER BY created_at LIMIT ?", (limit,)).fetchall()
        for row in rows:
            try:
                recover_file(row[0])
            except (OSError, ValueError):
                # Do not expose paths or message bytes to logs/observability.
                continue
