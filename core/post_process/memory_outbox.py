"""Bounded recovery of explicitly idempotent memory jobs, never tool actions."""
from contextlib import closing
from hashlib import sha256
import json
import sqlite3
import time

from core.sandbox import get_paths
from core.memory.scope import MemoryScope

DURABLE_TYPES = frozenset({"capture_turn_retry", "summarize_to_midterm", "consolidate_to_identity"})


def _connect():
    path = get_paths().memory_task_outbox()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=0.25)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, task_type TEXT NOT NULL, payload TEXT NOT NULL,
        identity TEXT NOT NULL,
        status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created_at)")
    return db


def put(task_type, payload):
    if task_type not in DURABLE_TYPES:
        raise ValueError("task is not replay safe")
    scope = (MemoryScope.from_payload(payload["scope"]) if payload.get("scope") is not None
             else MemoryScope.reality_scope(payload["uid"], payload["char_id"]))
    if scope.domain != "reality":
        raise ValueError("durable memory task requires reality scope")
    payload = dict(payload)
    if task_type == "consolidate_to_identity":
        if "episode_ids" not in payload:
            from core.memory.episodic_memory import load_unconsolidated
            payload["episode_ids"] = sorted(str(ep["id"]) for ep in load_unconsolidated(scope.uid, char_id=scope.character_id) if ep.get("id"))
        unit = [task_type, sorted(payload["episode_ids"])]
    else:
        unit = payload.get("turn_id")
    if scope.domain != "reality" or not unit:
        raise ValueError("durable memory task requires reality scope and turn_id")
    key = sha256(json.dumps([task_type, scope.to_payload(), unit], sort_keys=True).encode()).hexdigest()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    identity_fields = {field: payload.get(field) or "" for field in ("user_content", "reply", "source")}
    identity_fields.update({field: bool(payload.get(field)) for field in ("web_echo", "dream_echo", "coplay_echo")})
    identity = sha256(json.dumps(identity_fields, sort_keys=True).encode()).hexdigest()
    now = time.time()
    with closing(_connect()) as db, db:
        existing = db.execute("SELECT identity FROM tasks WHERE id=?", (key,)).fetchone()
        if existing is not None:
            if existing[0] != identity:
                raise ValueError("durable memory task identity conflict")
            return key
        if db.execute("SELECT count(*) FROM tasks WHERE status IN ('pending','running')").fetchone()[0] >= 1000:
            raise RuntimeError("memory outbox capacity reached")
        db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)", (key, task_type, encoded, identity, now, now))
    return key


def payload_for(key):
    with closing(_connect()) as db:
        row = db.execute("SELECT payload FROM tasks WHERE id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else {}


def checkpoint(key, field, value):
    """Persist a derived execution plan before a canonical write, never recall it."""
    with closing(_connect()) as db, db:
        row = db.execute("SELECT payload,status FROM tasks WHERE id=?", (key,)).fetchone()
        if row is None or row[1] not in {"pending", "running"}:
            raise RuntimeError("memory recovery checkpoint unavailable")
        payload = json.loads(row[0]); payload[field] = value
        db.execute("UPDATE tasks SET payload=?,updated_at=? WHERE id=?", (json.dumps(payload,ensure_ascii=False),time.time(),key))


def recover():
    if not get_paths().memory_task_outbox().exists():
        return
    from core.memory.event_log_writer import recover_pending
    recover_pending()
    with closing(_connect()) as db, db:
        db.execute("UPDATE tasks SET status='pending' WHERE status='running' AND attempts<3")
        db.execute("UPDATE tasks SET status='dlq' WHERE status IN ('pending','running') AND attempts>=3")


def claim():
    if not get_paths().memory_task_outbox().exists():
        return None
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM tasks WHERE status='pending' ORDER BY created_at LIMIT 1").fetchone()
        if row is None:
            return None
        db.execute("UPDATE tasks SET status='running', updated_at=? WHERE id=?", (time.time(), row["id"]))
        return {"task_type": row["task_type"], "payload": json.loads(row["payload"]), "outbox_id": row["id"]}


def attempt(key):
    with closing(_connect()) as db, db:
        return db.execute("UPDATE tasks SET attempts=attempts+1, updated_at=? WHERE id=? AND attempts<3 AND status='running'",
                          (time.time(), key)).rowcount == 1


def finish(key, *, succeeded):
    with closing(_connect()) as db, db:
        db.execute("UPDATE tasks SET status=?, payload=CASE WHEN ? THEN '{}' ELSE payload END, updated_at=? WHERE id=?",
                   ("done" if succeeded else "dlq", succeeded, time.time(), key))
        db.execute("DELETE FROM tasks WHERE status='done' AND id NOT IN (SELECT id FROM tasks WHERE status='done' ORDER BY updated_at DESC LIMIT 1000)")


def snapshot():
    path = get_paths().memory_task_outbox()
    if not path.exists():
        return {"counts": {}, "oldest_pending_at": None, "retry_limit": 3, "capacity": 1000,
            "projection_repair":{"pending":0,"oldest_at":None,"startup_batch_limit":32,"capacity":1000}}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
        counts = dict(db.execute("SELECT status, count(*) FROM tasks GROUP BY status"))
        oldest = db.execute("SELECT min(created_at) FROM tasks WHERE status IN ('pending','running')").fetchone()[0]
        projections = db.execute("SELECT count(*),min(created_at) FROM event_log_intents").fetchone() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='event_log_intents'").fetchone() else (0, None)
    return {"counts": counts, "oldest_pending_at": oldest, "retry_limit": 3, "capacity": 1000,
        "projection_repair":{"pending":projections[0],"oldest_at":projections[1],"startup_batch_limit":32,"capacity":1000}}
