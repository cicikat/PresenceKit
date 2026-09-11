"""Default-on, independent archive of API-returned reasoning, never prompt memory."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
import threading
import uuid
from contextlib import closing
from contextvars import ContextVar
from functools import wraps

from core.sandbox import get_paths

logger = logging.getLogger(__name__)
_INLINE = re.compile(r"<(think|thinking)>(.*?)(?:</\1>|$)", re.I | re.S)
_META = "seq, call_id, created_at, preset, model, protocol, status, reasoning_chars"
_DB_LOCK = threading.RLock()
_TURN_CAPTURE = ContextVar("reasoning_turn_capture", default=None)


def associate_owner_turn(function):
    """Correlate completed HTTP owner turns without inheriting background calls."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        channel = args[1] if len(args) > 1 else kwargs.get("provenance_channel")
        if channel not in {"desktop", "mobile"} or kwargs.get("turn_source", "user_chat") != "user_chat":
            return await function(*args, **kwargs)
        scope = {"active": True, "calls": []}
        token = _TURN_CAPTURE.set(scope)
        try:
            result = await function(*args, **kwargs)
            turn_id = result.get("turn_id") if isinstance(result, dict) else None
            if turn_id and scope["calls"]:
                try:
                    await asyncio.to_thread(_bind_turn, scope["calls"], turn_id)
                except Exception as exc:
                    logger.warning("[reasoning_archive] bind_failed error_type=%s", type(exc).__name__)
            return result
        finally:
            scope["active"] = False
            _TURN_CAPTURE.reset(token)
    return wrapped


def finish_turn_capture():
    """Stop correlation before post-processing can spawn unrelated LLM tasks."""
    scope = _TURN_CAPTURE.get()
    if scope is not None:
        scope["active"] = False


def _bind_turn(calls, turn_id):
    with _DB_LOCK:
        for path in {path for path, _ in calls}:
            if not path.exists():
                continue
            with closing(sqlite3.connect(path, timeout=0.25)) as db, db:
                db.executemany("UPDATE reasoning SET turn_id=? WHERE call_id=?",
                               [(turn_id, call_id) for p, call_id in calls if p == path])


def query_turn(turn_id: str):
    """Only linked owner calls; historical/unlinked global calls stay admin-only."""
    with _DB_LOCK:
        path = get_paths().llm_reasoning_db()
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
            db.row_factory = sqlite3.Row
            columns = {row[1] for row in db.execute("PRAGMA table_info(reasoning)")}
            if "turn_id" not in columns:
                return []
            result = []
            for row in db.execute(f"SELECT {_META}, parts FROM reasoning WHERE turn_id=? ORDER BY seq", (turn_id,)):
                entry = dict(row)
                entry["parts"] = json.loads(entry["parts"])
                result.append(entry)
            return result


def _field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


class Capture:
    """One API attempt, including partial streams and normalization failures."""

    def __init__(self, mc):
        self.call_id = uuid.uuid4().hex
        self.created_at = time.time()
        self.preset = mc.name
        self.model = mc.model
        self.protocol = getattr(mc, "api_protocol", "chat_completions")
        self.parts = []
        self.text = []
        self.status = "interrupted"
        self.paths = get_paths()

    def add(self, source, text):
        if isinstance(text, str) and text:
            self.parts.append({"source": source, "text": text})

    def response(self, response):
        try:
            self._response(response)
        except Exception as exc:
            logger.warning("[reasoning_archive] capture_failed error_type=%s", type(exc).__name__)

    def _response(self, response):
        if self.protocol == "chat_completions":
            choices = _field(response, "choices", []) or []
            if choices:
                message = _field(choices[0], "message")
                for key in ("reasoning_content", "reasoning"):
                    self.add(key, _field(message, key))
                text = _field(message, "content")
                if isinstance(text, str):
                    self.text.append(text)
        elif self.protocol == "anthropic_messages":
            for block in _field(response, "content", []) or []:
                if _field(block, "type") == "thinking":
                    self.add("thinking", _field(block, "thinking"))
                elif _field(block, "type") == "text":
                    text = _field(block, "text")
                    if isinstance(text, str):
                        self.text.append(text)
        elif self.protocol == "responses":
            for item in _field(response, "output", []) or []:
                if _field(item, "type") == "reasoning":
                    for key in ("summary", "content"):
                        for part in _field(item, key, []) or []:
                            self.add("reasoning_" + key, _field(part, "text"))
                elif _field(item, "type") == "message":
                    for part in _field(item, "content", []) or []:
                        text = _field(part, "text")
                        if isinstance(text, str):
                            self.text.append(text)

    async def save(self):
        try:
            for match in _INLINE.finditer("".join(self.text)):
                self.add("inline_" + match.group(1).lower(), match.group(2))
            if self.parts:
                await asyncio.to_thread(_append, self.paths, self)
                scope = _TURN_CAPTURE.get()
                if scope is not None and scope["active"]:
                    scope["calls"].append((self.paths.llm_reasoning_db(), self.call_id))
        except Exception as exc:
            # Never put response contents or filesystem paths in ordinary logs.
            logger.warning("[reasoning_archive] write_failed error_type=%s", type(exc).__name__)


def _append(paths, capture):
    # Resolve and create under one lock: Windows resolve() can change its
    # canonical prefix while another thread creates the previously missing root.
    with _DB_LOCK:
        _append_locked(paths.llm_reasoning_db(), capture)


def _append_locked(path, capture):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=0.25)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS reasoning (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT UNIQUE NOT NULL,
            created_at REAL NOT NULL, preset TEXT NOT NULL, model TEXT NOT NULL,
            protocol TEXT NOT NULL, status TEXT NOT NULL, reasoning_chars INTEGER NOT NULL,
            parts TEXT NOT NULL)""")
        if "turn_id" not in {row[1] for row in db.execute("PRAGMA table_info(reasoning)")}:
            db.execute("ALTER TABLE reasoning ADD COLUMN turn_id TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE INDEX IF NOT EXISTS reasoning_turn ON reasoning(turn_id)")
        db.execute("""INSERT INTO reasoning
            (call_id, created_at, preset, model, protocol, status, reasoning_chars, parts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
                capture.call_id, capture.created_at, capture.preset, capture.model,
                capture.protocol, capture.status, sum(len(p["text"]) for p in capture.parts),
                json.dumps(capture.parts, ensure_ascii=False),
            ))


def query(*, limit=50, before=None, model="", call_id=None):
    """Read-only connections; listing does not include reasoning text."""
    with _DB_LOCK:
        return _query(limit=limit, before=before, model=model, call_id=call_id)


def _query(*, limit, before, model, call_id):
    path = get_paths().llm_reasoning_db()
    if not path.exists():
        return None if call_id is not None else []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
        db.row_factory = sqlite3.Row
        if call_id is not None:
            row = db.execute(f"SELECT {_META}, parts FROM reasoning WHERE call_id=?", (call_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["parts"] = json.loads(result["parts"])
            return result
        clauses, args = [], []
        if before is not None:
            clauses.append("seq < ?")
            args.append(before)
        if model:
            clauses.append("model = ?")
            args.append(model)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return [dict(row) for row in db.execute(
            f"SELECT {_META} FROM reasoning{where} ORDER BY seq DESC LIMIT ?",
            (*args, min(100, max(1, limit))),
        )]
