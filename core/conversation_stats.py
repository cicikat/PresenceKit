"""Durable numeric activity counters. No messages, images, arguments or credentials."""
from __future__ import annotations

import logging
import sqlite3
import time
import uuid
import inspect
from contextvars import ContextVar
from functools import wraps
from contextlib import closing
from datetime import datetime, timedelta

from core.sandbox import get_paths

logger = logging.getLogger(__name__)
_SCOPE = ContextVar("conversation_statistics_scope", default=None)


def attributed(function):
    """Freeze owner/character before the first await, including streamed calls."""
    signature = inspect.signature(function)
    def enter(args, kwargs):
        arguments = signature.bind_partial(*args, **kwargs).arguments
        inherited = _SCOPE.get()
        char_id = arguments.get("char_id") or (inherited or {}).get("char_id")
        uid = arguments.get("uid") or arguments.get("user_id") or (inherited or {}).get("uid")
        try:
            if not char_id:
                from core.character_loader import _active_character_id
                char_id = _active_character_id()
            if not uid:
                from core.config_loader import get_config
                uid = str(get_config().get("scheduler", {}).get("owner_id") or "")
        except Exception:
            pass
        return _SCOPE.set({"uid": uid or "", "char_id": char_id or ""})
    if inspect.isasyncgenfunction(function):
        @wraps(function)
        async def streamed(*args, **kwargs):
            token = enter(args, kwargs)
            source = function(*args, **kwargs)
            try:
                async for item in source:
                    yield item
            finally:
                try:
                    await source.aclose()
                finally:
                    _SCOPE.reset(token)
        return streamed
    @wraps(function)
    async def wrapped(*args, **kwargs):
        token = enter(args, kwargs)
        try:
            return await function(*args, **kwargs)
        finally:
            _SCOPE.reset(token)
    return wrapped


def record(kind, *, uid="", char_id="", event_id=None, count=1, usage=None, ts=None):
    """Fail-open, idempotent for caller-supplied IDs; bounded SQLite lock wait."""
    try:
        scope = _SCOPE.get() or {}
        uid, char_id = uid or scope.get("uid", ""), char_id or scope.get("char_id", "")
        if not uid or not char_id:
            return
        path = get_paths().conversation_stats_db()
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time() if ts is None else ts
        tokens = normalize_usage(usage)
        with closing(sqlite3.connect(path, timeout=0.025)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS activity (
                id TEXT PRIMARY KEY, ts REAL NOT NULL, uid TEXT NOT NULL,
                char_id TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER)""")
            db.execute("CREATE INDEX IF NOT EXISTS activity_day ON activity(ts, uid, char_id)")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value REAL)")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('tracking_since', ?)", (time.time(),))
            db.execute("INSERT OR IGNORE INTO activity VALUES (?,?,?,?,?,?,?,?,?)",
                       (event_id or uuid.uuid4().hex, now, str(uid), str(char_id), kind,
                        count, *tokens))
    except Exception as exc:
        logger.warning("[conversation_stats] write_failed error_type=%s", type(exc).__name__)


def normalize_usage(usage):
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    elif hasattr(usage, "__dict__"):
        usage = vars(usage)
    if not isinstance(usage, dict):
        return None, None, None
    def number(key, alternate=""):
        value = usage.get(key, usage.get(alternate))
        return value if type(value) is int and value >= 0 else None
    incoming = number("input_tokens", "prompt_tokens")
    outgoing = number("output_tokens", "completion_tokens")
    # Anthropic cache reads/writes are additional input; OpenAI cached_tokens
    # is already included in prompt_tokens and must not be counted twice.
    if incoming is not None:
        incoming += (number("cache_read_input_tokens") or 0) + (number("cache_creation_input_tokens") or 0)
    total = number("total_tokens")
    if total is None and incoming is not None and outgoing is not None:
        total = incoming + outgoing
    return incoming, outgoing, total


def query(start, end, *, uid, char_id):
    """Inclusive server-local calendar dates; SQL range excludes the next midnight."""
    days = {}
    day = start
    while day <= end:
        days[day.isoformat()] = dict(date=day.isoformat(), chat_rounds=0, tool_calls=0,
            image_views=0, input_tokens=0, output_tokens=0, total_tokens=0,
            model_calls=0, usage_missing_calls=0)
        day += timedelta(days=1)
    path = get_paths().conversation_stats_db()
    since = None
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
            row = db.execute("SELECT value FROM metadata WHERE key='tracking_since'").fetchone()
            since = row[0] if row else None
            rows = db.execute("""SELECT ts, kind, count, input_tokens, output_tokens, total_tokens
                FROM activity WHERE ts>=? AND ts<? AND
                uid=? AND char_id=?""",
                (datetime.combine(start, datetime.min.time()).timestamp(),
                 datetime.combine(end + timedelta(days=1), datetime.min.time()).timestamp(), uid, char_id))
            for ts, kind, count, incoming, outgoing, total in rows:
                item = days[datetime.fromtimestamp(ts).date().isoformat()]
                if kind == "model_call":
                    item["model_calls"] += count
                    item["usage_missing_calls"] += int(total is None)
                    for key, value in (("input_tokens", incoming), ("output_tokens", outgoing), ("total_tokens", total)):
                        item[key] += value or 0
                elif kind in {"chat_round", "tool_call", "image_view"}:
                    item[{"chat_round": "chat_rounds", "tool_call": "tool_calls", "image_view": "image_views"}[kind]] += count
    for item in days.values():
        item["coverage"] = ("complete" if since is not None and
            datetime.fromtimestamp(since).date().isoformat() < item["date"] else "partial" if since is not None and
            datetime.fromtimestamp(since).date().isoformat() == item["date"] else "unavailable")
        if item["coverage"] == "unavailable":
            for key in ("chat_rounds", "tool_calls", "image_views", "input_tokens", "output_tokens", "total_tokens"):
                item[key] = None
        elif item["usage_missing_calls"] and not item["total_tokens"]:
            item["total_tokens"] = None
    ledger = get_paths().event_store(uid, char_id=char_id)
    if ledger.exists():
        with closing(sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
            rows = db.execute("""SELECT a.occurred_at, a.turn_id FROM events a
                WHERE a.uid=? AND a.char_id=? AND a.realm='reality'
                AND a.kind='assistant_message' AND a.turn_id<>''
                AND a.occurred_at>=? AND a.occurred_at<?
                AND EXISTS (SELECT 1 FROM events u WHERE u.uid=a.uid AND u.char_id=a.char_id
                    AND u.realm=a.realm AND u.turn_id=a.turn_id AND u.kind='user_message')""",
                (uid, char_id, datetime.combine(start, datetime.min.time()).timestamp(),
                 datetime.combine(end + timedelta(days=1), datetime.min.time()).timestamp()))
            historical = {}
            for ts, turn_id in rows:
                historical.setdefault(datetime.fromtimestamp(ts).date().isoformat(), set()).add(turn_id)
            for day, ids in historical.items():
                if days[day]["coverage"] != "complete":
                    days[day]["chat_rounds"] = max(days[day]["chat_rounds"] or 0, len(ids))
                    days[day]["chat_rounds_source"] = "retained_event_ledger_partial"
    return {"days": list(days.values()), "tracking_since": since,
            "timezone": "server_local", "retention": "indefinite",
            "scopes": {"chat_rounds": "owner_character_reality", "tool_calls": "owner_character",
                       "image_views": "owner_character", "tokens": "owner_character_model_calls"}}
