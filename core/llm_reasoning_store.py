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
from types import SimpleNamespace

from core.sandbox import get_paths

logger = logging.getLogger(__name__)
_INLINE = re.compile(r"<(think|thinking)>(.*?)(?:</\1>|$)", re.I | re.S)
_META = "seq, call_id, created_at, preset, model, protocol, status, reasoning_chars"
_DB_LOCK = threading.RLock()
_TURN_CAPTURE = ContextVar("reasoning_turn_capture", default=None)
_CAPTURE_PURPOSE = ContextVar("reasoning_capture_purpose", default="chat")
_OWNER_TURN_PURPOSES = frozenset({"chat", "monologue"})


def set_capture_purpose(purpose: str | None):
    """Tag the current LLM attempt so owner-turn binding can keep chat/monologue rows."""
    value = purpose if isinstance(purpose, str) and purpose.strip() else "chat"
    return _CAPTURE_PURPOSE.set(value.strip())


def reset_capture_purpose(token) -> None:
    _CAPTURE_PURPOSE.reset(token)


def _owner_turn_purpose(purpose: str | None) -> bool:
    value = (purpose or "").strip()
    return not value or value in _OWNER_TURN_PURPOSES


def _entry_is_monologue(parts) -> bool:
    return any(isinstance(part, dict) and part.get("source") == "monologue" for part in (parts or []))


def _bindable_owner_capture(capture) -> bool:
    """Bind chat reasoning plus prefixed-monologue text; skip helper-call CoT."""
    if not _owner_turn_purpose(capture.purpose):
        return False
    if (capture.purpose or "").strip() == "monologue":
        return _entry_is_monologue(capture.parts)
    return True


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
        for path in {path for path, _, _purpose in calls}:
            if not path.exists():
                continue
            with closing(sqlite3.connect(path, timeout=0.25)) as db, db:
                db.executemany(
                    "UPDATE reasoning SET turn_id=? WHERE call_id=?",
                    [
                        (turn_id, call_id)
                        for p, call_id, purpose in calls
                        if p == path and _owner_turn_purpose(purpose)
                    ],
                )


def query_turn(turn_id: str, *, prefer_monologue: bool = True):
    """Only linked owner calls; historical/unlinked global calls stay admin-only.

    Prefixed monologue (source=monologue) is included. Helper-call native CoT
    stored under purpose=monologue without that source stays admin-only.
    Display order is a presentation policy: monologue first by default, native
    first when prefer_monologue is false.
    """
    with _DB_LOCK:
        path = get_paths().llm_reasoning_db()
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
            db.row_factory = sqlite3.Row
            columns = {row[1] for row in db.execute("PRAGMA table_info(reasoning)")}
            if "turn_id" not in columns:
                return []
            has_purpose = "purpose" in columns
            select = f"SELECT {_META}, parts FROM reasoning WHERE turn_id=? ORDER BY seq"
            if has_purpose:
                select = (
                    f"SELECT {_META}, parts FROM reasoning WHERE turn_id=? "
                    "AND (purpose IS NULL OR purpose='' OR purpose='chat' "
                    "OR purpose='monologue') ORDER BY seq"
                )
            result = []
            for row in db.execute(select, (turn_id,)):
                entry = dict(row)
                entry["parts"] = json.loads(entry["parts"])
                purpose = (entry.get("purpose") or "").strip()
                if purpose == "monologue" and not _entry_is_monologue(entry["parts"]):
                    continue
                result.append(entry)
            result.sort(
                key=lambda entry: (
                    0 if _entry_is_monologue(entry.get("parts")) == bool(prefer_monologue) else 1,
                    entry.get("seq") or 0,
                )
            )
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
        self.purpose = _CAPTURE_PURPOSE.get() or "chat"
        self.parts = []
        self.text = []
        self.status = "interrupted"
        self.paths = get_paths()

    def add(self, source, text):
        if isinstance(text, str) and text:
            self.parts.append({"source": source, "text": text})

    def response(self, response):
        self.usage = _field(response, "usage")
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
                if scope is not None and scope["active"] and _bindable_owner_capture(self):
                    scope["calls"].append((self.paths.llm_reasoning_db(), self.call_id, self.purpose))
        except Exception as exc:
            # Never put response contents or filesystem paths in ordinary logs.
            logger.warning("[reasoning_archive] write_failed error_type=%s", type(exc).__name__)


async def archive_text(
    source: str,
    text: str,
    *,
    purpose: str = "monologue",
    preset: str = "monologue",
    model: str = "",
    protocol: str = "internal",
):
    """Persist a display-only reasoning part (prefixed monologue). Empty text is a no-op."""
    if not isinstance(text, str) or not text.strip():
        return
    capture = Capture(
        SimpleNamespace(name=preset, model=model or preset, api_protocol=protocol)
    )
    capture.purpose = purpose
    capture.add(source, text.strip())
    capture.status = "completed"
    await capture.save()


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
        columns = {row[1] for row in db.execute("PRAGMA table_info(reasoning)")}
        if "turn_id" not in columns:
            db.execute("ALTER TABLE reasoning ADD COLUMN turn_id TEXT NOT NULL DEFAULT ''")
        if "purpose" not in columns:
            db.execute("ALTER TABLE reasoning ADD COLUMN purpose TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE INDEX IF NOT EXISTS reasoning_turn ON reasoning(turn_id)")
        db.execute("""INSERT INTO reasoning
            (call_id, created_at, preset, model, protocol, status, reasoning_chars, parts, purpose)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                capture.call_id, capture.created_at, capture.preset, capture.model,
                capture.protocol, capture.status, sum(len(p["text"]) for p in capture.parts),
                json.dumps(capture.parts, ensure_ascii=False),
                capture.purpose or "chat",
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
