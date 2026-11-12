"""Poll one bounded Gmail page per scheduler pass and enqueue metadata-only facts."""

from __future__ import annotations

import asyncio
from email.utils import parseaddr
from hashlib import sha256
import time

from core.autonomy import store
from core.autonomy.effective_state import autonomy_enabled, scheduler_enabled
from core.autonomy.models import ActionMode, Signal
from core.mail import gmail, gmail_messages as messages, gmail_pending

SOURCE = "gmail_new_mail"
REMINDER = "gmail_pending_reminder"
REMINDER_INTERVAL = 7200
DEFAULT_INTERVAL = 300


def baseline() -> dict:
    now = time.time()
    return {"enabled": True, "interval": DEFAULT_INTERVAL, "cursor": int(now * 1000),
            "enabled_since": now}


def _active(record: dict, uid: str, char_id: str) -> bool:
    return bool(gmail.bound(record, uid, char_id) and record.get("refresh")
                and gmail.READ in record.get("scopes", [])
                and record.get("poll", {}).get("enabled", True))


def source_active(uid: str, char_id: str, signals) -> bool:
    matching = [signal for signal in signals if isinstance(signal, dict) and signal.get("source") in {SOURCE, REMINDER}]
    if not matching:
        return True
    try:
        record = gmail.load()
        enabled_since = record.get("poll", {}).get("enabled_since", 0)
        if any(signal.get("source") == REMINDER for signal in matching):
            summary = gmail_pending.summary(record, uid, char_id)
            if not summary["pending_count"] or summary["pending_sync_state"] != "connected":
                return False
        return bool(scheduler_enabled() and _active(record, uid, char_id)
                    and all(signal.get("created_at", 0) >= enabled_since for signal in matching))
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
        store.discard_pending_signals_by_source(uid, char_id, {SOURCE, REMINDER})


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
    upper = poll.get("upper") or min(int(now) * 1000, cursor + 86_400_000)
    if upper <= cursor:
        return
    page_token = poll.get("page_token", "")
    seen = dict(poll.get("seen", {}))
    candidates: list[str] = []
    discovered: list[tuple[str, int]] = []
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
            discovered.append((messages.message_id(row["id"]), int(row["internalDate"])))
            key = sha256((initial["email"] + ":" + row["id"]).encode()).hexdigest()[:32]
            if key not in seen:
                candidates.append(key)
                seen[key] = True
        token = page.get("nextPageToken", "")
        poll.update(last_sync=now, failures=0, error="", next_due=now + (60 if token else poll["interval"]))
        if token:
            poll.update(upper=upper, page_token=token, seen=dict(list(seen.items())[-2000:]))
        else:
            poll.update(cursor=upper, upper=0, page_token="", seen={})
    except (gmail.GmailError, KeyError, TypeError, ValueError) as exc:
        code = str(exc) if isinstance(exc, gmail.GmailError) else "invalid_gmail_response"
        if page_token and code == "request_rejected":
            poll["page_token"] = ""  # Restart the same time window, keeping seen hashes.
        failures = min(previous.get("failures", 0) + 1, 8)
        poll.update(error=code, failures=failures, next_due=now + min(6 * 3600, poll["interval"] * 2 ** failures))
        candidates = []
        discovered = []

    async with gmail.LOCK:
        record = gmail.load()
        if (not _active(record, uid, char_id) or record.get("poll") != previous
                or gmail_pending.mailbox_key(record) != gmail_pending.mailbox_key(initial)):
            return  # Settings changed or the mailbox disconnected while the request was in flight.
        if not gmail_pending.add_discovered(record, discovered, now):
            record["poll"] = {**previous, "error": "pending_overflow", "failures": 1,
                              "next_due": now + poll["interval"]}
            gmail.save(record)
            return
        # Store the inbox fact and scan cursor together before waking Autonomy.
        record["poll"] = poll
        gmail.save(record)
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
                    record["poll"] = poll
                    gmail.save(record)
                    break


async def remind_pending(uid: str, char_id: str, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    if not scheduler_enabled() or not autonomy_enabled(uid, char_id, store.load(uid, char_id)):
        return
    async with gmail.LOCK:
        record = gmail.load()
        if not _active(record, uid, char_id) or not gmail.allowed(uid, char_id, gmail.READ):
            return
        poll = record.get("poll", {})
        if not poll.get("last_sync") or now - poll.get("last_pending_reminder", 0) < REMINDER_INTERVAL:
            return
        summary = gmail_pending.summary(record, uid, char_id)
        if not summary["pending_count"] or summary["pending_sync_state"] != "connected":
            return
        signal = Signal(
            source=REMINDER, reason="The character Gmail inbox still has pending mail.",
            evidence=[{"fact": "gmail_pending_available"}], created_at=now, expires_at=now + 1200,
            priority=0.15, urgency=0.1, confidence=1, action_mode=ActionMode.USE_TOOLS.value,
        )
        key = f"{REMINDER}:{gmail_pending.mailbox_key(record)}:{int(now) // REMINDER_INTERVAL}"
        queued, disposition = store.enqueue_signal(uid, char_id, signal, dedupe_key=key, durable=True)
        if queued or disposition == "duplicate":
            poll["last_pending_reminder"] = now
            gmail.save(record)
