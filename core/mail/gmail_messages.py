"""Gmail message projections and durable send reservations."""

from __future__ import annotations

import base64
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, parseaddr
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
import time

from core.mail import gmail

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_MESSAGE_ID = re.compile(r"<[^\s<>@]+@[^\s<>@]+>")
_HEADERS = ["Subject", "From", "To", "Date", "Reply-To", "Message-ID", "References", "Auto-Submitted"]
_TEXT_LIMIT = 1300


def message_id(raw: str) -> str:
    if not isinstance(raw, str) or not _ID.fullmatch(raw):
        raise gmail.GmailError("invalid_message_id")
    return raw


def headers(message: dict) -> dict[str, str]:
    return {
        str(item["name"]).casefold(): str(item.get("value", ""))[:2048]
        for item in message.get("payload", {}).get("headers", [])
        if isinstance(item, dict) and "name" in item
    }


def summary(message: dict) -> dict:
    values = headers(message)
    return {
        "id": message.get("id", ""),
        "subject": values.get("subject", "")[:100],
        "from": values.get("from", "")[:100],
        "date": values.get("date", "")[:60],
    }


async def get(uid: str, char_id: str, mid: str, *, full: bool = False) -> dict:
    params = [("format", "full" if full else "metadata")]
    if not full:
        params.extend(("metadataHeaders", name) for name in _HEADERS)
    return await gmail.api(uid, char_id, "GET", f"messages/{message_id(mid)}", params=params)


async def list_page(uid: str, char_id: str, *, query: str = "", page_token: str = "", limit: int = 5) -> dict:
    params: dict[str, str | int] = {"maxResults": limit, "fields": "messages(id),nextPageToken"}
    if query:
        params["q"] = query
    if page_token:
        params["pageToken"] = page_token
    return await gmail.api(uid, char_id, "GET", "messages", params=params)


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head", "template"}:
            self.hidden += 1
        elif not self.hidden and tag in {"br", "p", "div", "tr", "li", "blockquote"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def body_text(payload: dict) -> str:
    pending = [(payload, 0)]
    html = ""
    for _ in range(40):
        if not pending:
            break
        part, depth = pending.pop()
        mime = str(part.get("mimeType", "")).lower()
        part_headers = headers({"payload": part})
        if part.get("filename") or "attachment" in part_headers.get("content-disposition", "").lower():
            continue
        if mime.startswith("multipart/") and depth < 6:
            pending.extend((child, depth + 1) for child in reversed(part.get("parts", [])[:40]))
        if mime not in {"text/plain", "text/html"}:
            continue
        data = part.get("body", {}).get("data", "")
        if not data:
            continue
        try:
            # A prefix whose length is divisible by four can be decoded independently.
            data = data[:32768]
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        except ValueError:
            continue
        from email.message import Message
        mime_headers = Message()
        mime_headers["Content-Type"] = part_headers.get("content-type", mime)
        charset = mime_headers.get_content_charset() or "utf-8"
        try:
            text = decoded.decode(charset, errors="replace")
        except (LookupError, TypeError):
            text = decoded.decode("utf-8", errors="replace")
        if mime == "text/plain" and text.strip():
            return text.strip()[:_TEXT_LIMIT]
        if mime == "text/html" and not html:
            parser = _PlainHTML()
            parser.feed(text)
            html = "".join(parser.parts).strip()[:_TEXT_LIMIT]
    return html


def _fingerprint(*values: str) -> str:
    return sha256(json.dumps(values, ensure_ascii=False).encode("utf-8")).hexdigest()


async def send(uid: str, char_id: str, *, recipient: str, subject: str, body: str,
               delivery_key: str, parent: dict | None = None, from_name: str = "") -> dict:
    """Persist an in-flight reservation before issuing the single POST."""
    recipient = gmail.normalize_address(recipient)
    if any(c in subject for c in "\r\n") or len(subject) > 400 or not 0 < len(body) <= 100_000:
        raise gmail.GmailError("invalid_message")
    if not 0 < len(delivery_key) <= 160:
        raise gmail.GmailError("invalid_delivery_key")
    scopes = (gmail.READ, gmail.SEND) if parent else (gmail.SEND,)
    token = await gmail.access_token(uid, char_id, *scopes)
    async with gmail.LOCK:
        record = gmail.load()
        if not gmail.bound(record, uid, char_id) or not record.get("refresh"):
            raise gmail.GmailError("gmail_not_connected")
        if not gmail.token_current(record, token):
            raise gmail.GmailError("connection_changed")
        sender = record["email"]
        if recipient.casefold() == sender:
            raise gmail.GmailError("self_delivery_blocked")
        key = _fingerprint(uid, char_id, sender, delivery_key)
        content = _fingerprint(uid, char_id, sender, recipient.casefold(), subject, body, str((parent or {}).get("id", "")))
        entries = record.setdefault("deliveries", {})
        existing = entries.get(key)
        if not existing:
            # Covers a model retry that invents a new request key.
            existing = next((row for row in entries.values() if row["content"] == content), None)
        if existing:
            if existing["content"] != content and not delivery_key.startswith("scheduled:"):
                raise gmail.GmailError("delivery_key_conflict")
            if existing["state"] != "sent":
                raise gmail.GmailError("outcome_unknown")
            return {"status": "already_sent", "receipt": existing["receipt"]}

        if len(entries) >= 10000:
            raise gmail.GmailError("delivery_ledger_full")
        msg = EmailMessage(policy=SMTP)
        try:
            from_address = Address(addr_spec=sender)
            msg["From"] = Address(display_name=from_name, username=from_address.username, domain=from_address.domain)
            msg["To"] = recipient
            msg["Subject"] = subject
            msg["Message-ID"] = f"<{key}@{from_address.domain}>"
            msg["Date"] = formatdate(usegmt=True)
            msg.set_content(body, charset="utf-8")
            payload = {}
            if parent:
                previous = headers(parent)
                reply_id = previous.get("message-id", "").strip()
                if not _MESSAGE_ID.fullmatch(reply_id) or not parent.get("threadId"):
                    raise gmail.GmailError("invalid_reply_thread")
                refs = _MESSAGE_ID.findall(previous.get("references", ""))[-12:]
                msg["In-Reply-To"] = reply_id
                msg["References"] = " ".join([*refs, reply_id])
                payload["threadId"] = message_id(parent["threadId"])
            payload["raw"] = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        except (ValueError, TypeError, UnicodeError):
            raise gmail.GmailError("invalid_message") from None

        receipt = key[:24]
        entries[key] = {"state": "pending", "content": content, "receipt": receipt, "at": int(time.time())}
        gmail.save(record)
        try:
            result = await gmail.request("POST", gmail.API + "messages/send", token=token,
                                         sending=True, json=payload)
            if not result.get("id"):
                raise gmail.GmailError("outcome_unknown")
        except gmail.GmailError as exc:
            if str(exc) != "outcome_unknown":
                del entries[key]
                gmail.save(record)
            raise
        entries[key]["state"] = "sent"
        try:
            gmail.save(record)
        except gmail.GmailError:
            raise gmail.GmailError("outcome_unknown") from None
        return {"status": "sent", "receipt": receipt}


async def reply(uid: str, char_id: str, *, mid: str, body: str, delivery_key: str) -> dict:
    parent = await get(uid, char_id, mid)
    original = headers(parent)
    if original.get("auto-submitted", "no").strip().lower() != "no":
        raise gmail.GmailError("automated_message_reply_blocked")
    recipient = parseaddr(original.get("reply-to") or original.get("from", ""))[1]
    return await send(uid, char_id, recipient=recipient, subject=original.get("subject", ""),
                      body=body, delivery_key=delivery_key, parent=parent)
