"""Mobile final-only transport and recoverable display projections (R2).

No model deltas enter this module. History shares the existing evidence DB,
but is not memory evidence, a delivery queue, or an ACK cursor.
"""
from __future__ import annotations

import asyncio
from collections import Counter, deque
from contextlib import closing
from dataclasses import dataclass, field
import json
import sqlite3
import time
from uuid import uuid4

from core.memory.scope import MemoryScope

_COUNTS = Counter()
_CONNECTIONS: dict[str, "Subscription"] = {}
_CLIENTS: dict[tuple[str, str], str] = {}
_METRICS: dict[str, deque] = {}
_SCHEMA = """CREATE TABLE IF NOT EXISTS mobile_finals (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id TEXT NOT NULL UNIQUE, assistant_event_id TEXT NOT NULL,
    envelope TEXT NOT NULL, created_at REAL NOT NULL)"""


def enabled() -> bool:
    from core.config_loader import get_config
    from core.client_channels import enabled as channel_enabled
    return channel_enabled("mobile") and bool(get_config().get("mobile_realtime", {}).get("enabled", True))


def remember_final(*, uid: str, char_id: str, turn_id: str, content: str,
                   display_text: str, request_id: str = "", artifacts=None) -> dict | None:
    """Only a retained assistant ledger row can authorize a recoverable final.

    INSERT OR IGNORE keeps the first persisted projection immutable. Failure
    disables WS for this message; existing HTTP/queue delivery still proceeds.
    """
    from core.memory import event_store
    import re
    # Fail closed on internal frames/unfinished markup. Final-only means no
    # prefix can escape before whole-output validation has completed.
    unstyled = re.sub(r"</?(?:hl|big|sm)>", "", display_text)
    if re.search(r"<\s*/?\s*[A-Za-z_|]|\[/?INST\]", unstyled, re.I) or unstyled.rstrip().endswith(("<", "</")):
        _COUNTS["unsafe_final_rejected"] += 1
        return None
    if not turn_id or not char_id:
        return None
    scope = MemoryScope.reality_scope(uid, char_id)
    path = event_store.resolve_path(scope, "event_store")
    if not path.exists():
        _COUNTS["history_missing_ledger"] += 1
        return None
    try:
        with event_store._lock_for(path), closing(sqlite3.connect(path, timeout=0.25)) as db:
            row = db.execute(
                "SELECT occurred_at FROM events WHERE event_id=? AND uid=? AND char_id=? "
                "AND realm='reality' AND actor='assistant' AND kind IN ('assistant_message','trigger_assistant') "
                "AND redaction_state!='tombstoned'", (turn_id + ":assistant", uid, char_id),
            ).fetchone()
            if row is None:
                _COUNTS["history_missing_ledger"] += 1
                return None
            envelope = {"id": turn_id, "msg_id": turn_id, "turn_id": turn_id,
                        "user_id": uid, "char_id": char_id, "domain": "reality",
                        "request_id": request_id, "content": content,
                        "display_text": display_text, "artifacts": list(artifacts or []),
                        "timestamp": row[0], "critical_written": True}
            db.execute(_SCHEMA)
            db.execute("""CREATE TRIGGER IF NOT EXISTS mobile_finals_tombstone
                AFTER UPDATE OF redaction_state ON events WHEN NEW.redaction_state='tombstoned'
                BEGIN DELETE FROM mobile_finals WHERE assistant_event_id=NEW.event_id; END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS mobile_finals_delete
                AFTER DELETE ON events BEGIN DELETE FROM mobile_finals WHERE assistant_event_id=OLD.event_id; END""")
            db.execute("INSERT OR IGNORE INTO mobile_finals (msg_id,assistant_event_id,envelope,created_at) VALUES (?,?,?,?)",
                       (turn_id, turn_id + ":assistant", json.dumps(envelope, ensure_ascii=False), time.time()))
            stored = db.execute("SELECT envelope FROM mobile_finals WHERE msg_id=?", (turn_id,)).fetchone()
            db.commit()
        _COUNTS["persisted_final"] += 1
        return json.loads(stored[0])
    except (sqlite3.Error, ValueError, TypeError):
        _COUNTS["history_write_failed"] += 1
        return None


def history(scope: MemoryScope, *, before: int | None = None, limit: int = 50) -> dict:
    """Descending immutable rowid pages; page tokens never acknowledge delivery."""
    from core.memory import event_store
    limit = max(1, min(int(limit), 50))
    path = event_store.resolve_path(scope, "event_store")
    empty = {"messages": [], "next_before": None}
    if not path.exists():
        return empty
    with event_store._lock_for(path), closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mobile_finals'").fetchone():
            return empty
        rows = db.execute(
            "SELECT f.rowid,f.envelope FROM mobile_finals f JOIN events e ON e.event_id=f.assistant_event_id "
            "WHERE e.uid=? AND e.char_id=? AND e.realm='reality' AND e.redaction_state!='tombstoned' "
            "AND (? IS NULL OR f.rowid < ?) ORDER BY f.rowid DESC LIMIT ?",
            (scope.uid, scope.character_id, before, before, limit + 1),
        ).fetchall()
    more = len(rows) > limit
    page = rows[:limit]
    _COUNTS["history_pages"] += 1
    return {"messages": [{**json.loads(row[1]), "history_index": row[0]} for row in page],
            "next_before": page[-1][0] if more else None}


def finals_for_turns(scope: MemoryScope, turn_ids: list[str]) -> dict[str, dict]:
    """Enrich the existing date-based HTTP history without scanning all days."""
    from core.memory import event_store
    path = event_store.resolve_path(scope, "event_store")
    if not turn_ids or not path.exists():
        return {}
    with event_store._lock_for(path), closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mobile_finals'").fetchone():
            return {}
        result = {}
        for offset in range(0, len(turn_ids), 200):
            selected = turn_ids[offset:offset + 200]
            rows = db.execute(
                "SELECT f.msg_id,f.envelope FROM mobile_finals f JOIN events e ON e.event_id=f.assistant_event_id "
                "WHERE e.uid=? AND e.char_id=? AND e.realm='reality' AND e.redaction_state!='tombstoned' "
                f"AND f.msg_id IN ({','.join('?' for _ in selected)})",
                (scope.uid, scope.character_id, *selected),
            ).fetchall()
            result.update((row[0], json.loads(row[1])) for row in rows)
        return result


@dataclass
class Subscription:
    websocket: object
    grant: object
    token_label: str
    client_id: str = ""
    generation: str = field(default_factory=lambda: uuid4().hex)
    pending: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=64))

    def authorized(self) -> bool:
        from admin.auth import authenticate_ws
        from core.session_scope import resolve_session, SessionScopeError
        if not enabled():
            return False
        if _CLIENTS.get((self.token_label, self.client_id)) != self.generation:
            return False
        info = authenticate_ws(self.websocket, "chat")
        if info is None or info.label != self.token_label:
            return False
        try:
            grant = resolve_session(self.grant.session_id, token_label=info.label)
            return grant == self.grant
        except SessionScopeError:
            return False


def publish(envelope: dict) -> None:
    """Nonblocking fanout; slow receivers recover through HTTP, never token replay."""
    for subscription in tuple(_CONNECTIONS.values()):
        grant = subscription.grant
        if (envelope.get("user_id"), envelope.get("char_id"), envelope.get("domain")) != (
            grant.owner_id, grant.char_id, grant.domain,
        ):
            continue
        if not subscription.authorized():
            _COUNTS["dispatch_denied"] += 1
            while not subscription.pending.empty():
                subscription.pending.get_nowait()
            subscription.pending.put_nowait(None)
            continue
        if subscription.pending.full():
            _COUNTS["overflow"] += 1
            # Drop all queued bodies and ask the writer to close for HTTP recovery.
            while not subscription.pending.empty():
                subscription.pending.get_nowait()
            subscription.pending.put_nowait(None)
        else:
            subscription.pending.put_nowait((dict(envelope), time.monotonic()))


async def serve(websocket) -> None:
    from admin.auth import authenticate_ws
    from core.session_scope import resolve_session, SessionScopeError
    import re
    info = authenticate_ws(websocket, "chat")
    client_id = websocket.headers.get("x-presence-client", "")
    if info is None or not enabled() or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id):
        _COUNTS["handshake_denied"] += 1
        await websocket.close(code=1008)
        return
    try:
        grant = resolve_session(websocket.headers.get("x-presence-session"), token_label=info.label)
    except SessionScopeError:
        _COUNTS["handshake_denied"] += 1
        await websocket.close(code=1008)
        return
    if len(_CONNECTIONS) >= 16:
        await websocket.close(code=1013)
        return
    sub = Subscription(websocket, grant, info.label, client_id)
    await websocket.accept()
    key = (info.label, client_id)
    previous = _CONNECTIONS.get(_CLIENTS.get(key, ""))
    _CLIENTS[key] = sub.generation  # New authorized bind immediately retires old frames.
    if previous is not None:
        while not previous.pending.empty():
            previous.pending.get_nowait()
        previous.pending.put_nowait(None)
    _CONNECTIONS[sub.generation] = sub
    _COUNTS["connected"] += 1

    async def send(payload):
        # Evaluate immediately before EVERY frame, including queued final and pong.
        if not sub.authorized():
            _COUNTS["revoked"] += 1
            raise PermissionError("mobile subscription revoked")
        await asyncio.wait_for(websocket.send_json({**payload, "generation": sub.generation,
            "char_id": grant.char_id, "domain": grant.domain, "client_type": "mobile"}), 3)

    async def writer():
        await send({"type": "ready", "mode": "final_only", "session_id": grant.session_id})
        while True:
            try:
                item = await asyncio.wait_for(sub.pending.get(), 20)
            except asyncio.TimeoutError:
                await send({"type": "ping"})
                continue
            if item is None:
                raise ConnectionError("mobile subscriber overflow")
            envelope, queued_at = item
            await send({"type": "final", "message": envelope})
            _COUNTS["sent_final"] += 1
            _COUNTS["enqueue_to_send_ms_total"] += round((time.monotonic() - queued_at) * 1000)

    async def reader():
        while True:
            frame = await asyncio.wait_for(websocket.receive_json(), 45)
            if not sub.authorized():
                raise PermissionError("mobile subscription revoked")
            if not isinstance(frame, dict) or frame.get("type") != "pong" or frame.get("generation") != sub.generation:
                raise ValueError("invalid mobile frame")
            # No ACK or input/execute protocol exists on this connection.

    tasks = [asyncio.create_task(writer()), asyncio.create_task(reader())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except Exception:
        _COUNTS["disconnected"] += 1
    finally:
        can_receive = sub.authorized()
        _CONNECTIONS.pop(sub.generation, None)
        if _CLIENTS.get(key) == sub.generation:
            _CLIENTS.pop(key, None)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await websocket.close(code=1008 if not can_receive else 1000)
        except Exception:
            pass


def snapshot() -> dict:
    return {"enabled": enabled(), "mode": "final_only", "active_connections": len(_CONNECTIONS),
            "counts": dict(_COUNTS), "client_metrics": {
                key: {"samples": len(values), "p50": sorted(values)[(len(values)-1)//2],
                      "p95": sorted(values)[min(len(values)-1, int(len(values)*.95))]}
                for key, values in _METRICS.items() if values
            }, "queue_capacity": 64, "connection_limit": 16}


def record_metric(kind: str, value: int) -> None:
    _METRICS.setdefault(kind, deque(maxlen=200)).append(value)
