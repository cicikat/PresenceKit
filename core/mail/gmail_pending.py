
"""Durable, mailbox-scoped handling state independent of Gmail's labels."""

from __future__ import annotations

import hashlib
import json
import time

from core.mail import gmail

MAX_ROWS = 2_000
HISTORY_SECONDS = 30 * 86400


def mailbox_key(record: dict) -> str:
    values = (record.get("uid"), record.get("char_id"), record.get("email"))
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def _pending_id(key: str, message_id: str) -> str:
    return hashlib.sha256(f"{key}:{message_id}".encode()).hexdigest()[:32]


def _ledger(record: dict) -> dict:
    ledger = record.get("pending_ledger", {})
    if not isinstance(ledger, dict) or any(
        not isinstance(row, dict) or row.get("status") not in {"pending", "handled"}
        or not isinstance(row.get("mailbox"), str)
        or not isinstance(row.get("message_id"), str)
        or not isinstance(row.get("revision"), int)
        for row in ledger.values()
    ):
        raise gmail.GmailError("pending_storage_error")
    return ledger


def add_discovered(record: dict, candidates: list[tuple[str, int]], now: float) -> bool:
    """Update the caller's transaction. On overflow the caller keeps its scan cursor."""
    ledger = dict(_ledger(record))
    key = mailbox_key(record)
    cursor = record.get("poll", {}).get("cursor", 0)
    for identity, row in list(ledger.items()):
        if (row["status"] == "handled" and row.get("handled_at", now) < now - HISTORY_SECONDS
                and (row["mailbox"] != key or row.get("internal_date", cursor + 1) <= cursor)):
            del ledger[identity]
    new_ids = {
        _pending_id(key, message_id) for message_id, _ in candidates
    } - ledger.keys()
    needed = len(ledger) + len(new_ids) - MAX_ROWS
    if needed > 0:
        removable = sorted(
            (row["handled_at"], identity)
            for identity, row in ledger.items()
            if row["status"] == "handled"
            and (row["mailbox"] != key or row.get("internal_date", cursor + 1) <= cursor)
        )
        if len(removable) < needed:
            return False
        for _, identity in removable[:needed]:
            del ledger[identity]
    for message_id, internal_date in candidates:
        identity = _pending_id(key, message_id)
        if identity not in ledger:
            ledger[identity] = {
                "mailbox": key, "message_id": message_id, "internal_date": internal_date,
                "status": "pending", "first_seen_at": now, "revision": 1,
            }
        ledger[identity]["last_confirmed_at"] = now
    record["pending_ledger"] = ledger
    return True


def summary(record: dict, uid: str, char_id: str, *, token_state: str = "") -> dict:
    bound = gmail.bound(record, uid, char_id) and bool(record.get("email"))
    key = mailbox_key(record)
    rows = [row for row in _ledger(record).values() if bound and row["mailbox"] == key]
    poll = record.get("poll", {}) if bound else {}
    error = poll.get("error", "")
    available = bool(bound and record.get("refresh") and gmail.READ in record.get("scopes", [])
                     and token_state not in {"client_disabled", "reauthorization_required",
                                             "not_configured", "pending_verification"}
                     and error in {"", "pending_overflow", "autonomy_queue_write_failed"})
    pending = sum(row["status"] == "pending" for row in rows)
    return {
        "pending_count": pending,
        "handled_count_recent": sum(row["status"] == "handled"
                                    and row.get("handled_at", 0) > time.time() - HISTORY_SECONDS for row in rows),
        "pending_sync_state": "connected" if available else "mailbox_unavailable",
        "last_error_code": error or (token_state if bound and not available else ""),
        "overflow": error == "pending_overflow",
        "mailbox_identity_valid": bound,
    }


def list_items(uid: str, char_id: str, *, after: str = "", limit: int = 5,
               include_handled: bool = False) -> dict:
    if not 1 <= limit <= 50 or len(after) > 32:
        raise gmail.GmailError("invalid_page_arguments")
    record = gmail.load()
    if not gmail.bound(record, uid, char_id) or not record.get("email"):
        return {"items": [], "next_cursor": ""}
    key = mailbox_key(record)
    rows = [(identity, row) for identity, row in sorted(_ledger(record).items())
            if identity > after and row["mailbox"] == key and (include_handled or row["status"] == "pending")]
    items = [{
        "pending_id": identity, "status": row["status"], "first_seen_at": row["first_seen_at"],
        "revision": row["revision"], "availability": row.get("availability", "last_seen"),
    } for identity, row in rows[:limit]]
    return {"items": items, "next_cursor": items[-1]["pending_id"] if len(rows) > limit else ""}


def pending_anchor(record: dict) -> str:
    key = mailbox_key(record)
    ids = sorted(identity for identity, row in _ledger(record).items()
                 if row["mailbox"] == key and row["status"] == "pending")
    return hashlib.sha256(json.dumps(ids).encode()).hexdigest()


def is_pending_id(record: dict, identity: str) -> bool:
    row = _ledger(record).get(identity)
    return bool(row and row["mailbox"] == mailbox_key(record) and row["status"] == "pending")


def resolve_message_id(record: dict, identifier: str) -> str:
    row = _ledger(record).get(identifier)
    if row is not None:
        if row["mailbox"] != mailbox_key(record):
            raise gmail.GmailError("pending_not_found")
        return row["message_id"]
    return identifier


def transition(record: dict, identity: str, *, handled: bool, actor: str,
               reason: str = "", revision: int | None = None, receipt: str = "") -> dict:
    row = _ledger(record).get(identity)
    if row is None or row["mailbox"] != mailbox_key(record):
        raise gmail.GmailError("pending_not_found")
    status = "handled" if handled else "pending"
    if revision is not None and row["revision"] != revision and row["status"] != status:
        raise gmail.GmailError("pending_revision_conflict")
    if row["status"] != status:
        row.update(status=status, revision=row["revision"] + 1, handled_at=time.time() if handled else None,
                   handled_by=actor, handled_reason=reason[:160], reply_receipt=receipt)
    return {"pending_id": identity, "status": status, "revision": row["revision"]}


async def set_handled(uid: str, char_id: str, identity: str, **change) -> dict:
    async with gmail.LOCK:
        record = gmail.load()
        if not gmail.bound(record, uid, char_id):
            raise gmail.GmailError("pending_not_found")
        result = transition(record, identity, **change)
        gmail.save(record)
        return result


def handle_verified_reply(record: dict, message_id: str, receipt: str) -> None:
    identity = _pending_id(mailbox_key(record), message_id)
    if identity in _ledger(record):
        transition(record, identity, handled=True, actor="system-verified", receipt=receipt)


async def note_unavailable(uid: str, char_id: str, key: str, message_id: str) -> None:
    async with gmail.LOCK:
        record = gmail.load()
        if not gmail.bound(record, uid, char_id) or mailbox_key(record) != key:
            return
        row = _ledger(record).get(_pending_id(key, message_id))
        if row is not None and row.get("availability") != "unavailable":
            row.update(availability="unavailable", revision=row["revision"] + 1)
            gmail.save(record)
