"""Poll one bounded Gmail page per scheduler pass and enqueue metadata-only facts."""

from __future__ import annotations

import asyncio
from email.utils import parseaddr
from hashlib import sha256
import time

from core.autonomy import store
from core.autonomy.effective_state import autonomy_enabled, scheduler_enabled
from core.autonomy.models import ActionMode, Signal
from core.mail import gmail, gmail_messages as messages

SOURCE = "gmail_new_mail"
DEFAULT_INTERVAL = 300


def baseline() -> dict:
    return {"enabled": True, "interval": DEFAULT_INTERVAL, "cursor": int(time.time() * 1000)}


def _active(record: dict, uid: str, char_id: str) -> bool:
    return bool(gmail.bound(record, uid, char_id) and record.get("refresh")
                and gmail.READ in record.get("scopes", [])
                and record.get("poll", {}).get("enabled", True))


def source_active(uid: str, char_id: str, signals) -> bool:
    if not any(isinstance(signal, dict) and signal.get("source") == SOURCE for signal in signals):
        return True
    try:
        return scheduler_enabled() and _active(gmail.load(), uid, char_id)
    except gmail.GmailError:
        return False


def status(record: dict, uid: str, char_id: str) -> dict:
    poll = record.get("poll", {}) if gmail.bound(record, uid, char_id) else {}
    return {
        "sync_enabled": poll.get("enabled", True),
        "sync_effective": bool(scheduler_enabled() and _active(record, uid, char_id)),
        "sync_interval_seconds": poll.get("interval", DEFAULT_INTERVAL),
        "last_sync_at": poll.get("last_sync"),
        "sync_error": poll.get("error", ""),
        "sync_retry_at": poll.get("next_due") if poll.get("failures") else None,
    }


async def configure(uid: str, char_id: str, enabled: bool, interval: int) -> None:
    async with gmail.LOCK:
        record = gmail.load()
        if not gmail.bound(record, uid, char_id) or not record.get("refresh"):
            raise gmail.GmailError("gmail_not_connected")
        poll = record.get("poll") or baseline()
        if enabled != poll["enabled"]:
            poll = {**baseline(), "last_sync": poll.get("last_sync")}
        if interval != poll["interval"]:
            poll.pop("next_due", None)
        poll.update(enabled=enabled, interval=interval)
        record["poll"] = poll
        gmail.save(record)
    if not enabled:
        store.discard_pending_signals_by_source(uid, char_id, {SOURCE})


def _new_mail(row: dict, email: str, start: int, end: int) -> bool:
    stamp = int(row.get("internalDate", 0))
    fields = messages.headers(row)
    sender = parseaddr(fields.get("from", ""))[1].casefold()
    return (start < stamp <= end and "SENT" not in row.get("labelIds", [])
            and sender != email and fields.get("auto-submitted", "no").strip().lower() == "no")


async def tick(uid: str, char_id: str, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    try:
        initial = gmail.load()
    except gmail.GmailError:
        return
    if not _active(initial, uid, char_id):
        return
    previous = initial.get("poll")
    if not previous:
        async with gmail.LOCK:
            record = gmail.load()
            if _active(record, uid, char_id) and not record.get("poll"):
                record["poll"] = baseline()
                gmail.save(record)
        return
    if previous.get("next_due", 0) > now:
        return
    poll = dict(previous)
    cursor = poll["cursor"]
    upper = poll.get("upper") or int(now) * 1000
    if upper <= cursor:
        return
    page_token = poll.get("page_token", "")
    seen = dict(poll.get("seen", {}))
    candidates: list[str] = []
    try:
        page = await messages.list_page(
            uid, char_id, limit=10, page_token=page_token,
            query=f"in:inbox -in:sent after:{max(0, cursor // 1000 - 1)} before:{upper // 1000 + 1}",
        )

        async def fetch(mid: str) -> dict:
            try:
                return await messages.get(uid, char_id, mid)
            except gmail.GmailError as exc:
                if str(exc) == "not_found":  # Mail deleted since the list request.
                    return {}
                raise

        rows = await asyncio.gather(*(fetch(item["id"]) for item in page.get("messages", [])[:10]))
        for row in rows:
            if not _new_mail(row, initial["email"], cursor, upper):
                continue
            key = sha256((initial["email"] + ":" + row["id"]).encode()).hexdigest()[:32]
            if key not in seen:
                candidates.append(key)
                seen[key] = True
        token = page.get("nextPageToken", "")
        poll.update(last_sync=now, failures=0, error="", next_due=now + (60 if token else poll["interval"]))
        if token:
            poll.update(upper=upper, page_token=token, seen=seen)
        else:
            poll.update(cursor=upper, upper=0, page_token="", seen={})
    except (gmail.GmailError, KeyError, TypeError, ValueError) as exc:
        code = str(exc) if isinstance(exc, gmail.GmailError) else "invalid_gmail_response"
        if page_token and code == "request_rejected":
            poll["page_token"] = ""  # Restart the same time window, keeping seen hashes.
        failures = min(previous.get("failures", 0) + 1, 8)
        poll.update(error=code, failures=failures, next_due=now + min(6 * 3600, poll["interval"] * 2 ** failures))
        candidates = []

    async with gmail.LOCK:
        record = gmail.load()
        if not _active(record, uid, char_id) or record.get("poll") != previous:
            return  # Settings changed or the mailbox disconnected while the request was in flight.
        state = store.load(uid, char_id) if candidates else {}
        if candidates and autonomy_enabled(uid, char_id, state):
            # Use per-message hashes for retry/restart deduplication. The signal
            # contains no Gmail IDs, subjects, senders, or body.
            for key in candidates:
                signal = Signal(
                    source=SOURCE, reason="A new message is available in the character Gmail inbox.",
                    evidence=[{"fact": "gmail_message_available"}], created_at=now, expires_at=now + 1200,
                    priority=0.2, urgency=0.2, confidence=1, action_mode=ActionMode.USE_TOOLS.value,
                )
                queued, disposition = store.enqueue_signal(
                    uid, char_id, signal, dedupe_key=f"{SOURCE}:{key}", durable=True,
                )
                if not queued and disposition != "duplicate":
                    poll = {**previous, "error": "autonomy_queue_write_failed", "failures": 1,
                            "next_due": now + poll["interval"]}
                    break
        record["poll"] = poll
        gmail.save(record)
