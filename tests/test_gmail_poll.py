import asyncio
import time

import pytest

from core.autonomy import store
from core.mail import gmail, gmail_poll
from test_gmail_oauth import mailbox  # noqa: F401
from test_gmail_messages import connected  # noqa: F401


@pytest.mark.asyncio
async def test_baseline_pagination_and_restart_dedup(connected, monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_path", lambda *_: tmp_path / "autonomy.json")
    store.replace_config("owner", "character", {**store.DEFAULT_CONFIG, "enabled": True})
    now = int(time.time())
    await gmail_poll.tick("owner", "character", now=now)
    initial = gmail.load()
    baseline = initial["poll"]["cursor"]
    calls = []

    async def page(*_, query, page_token, limit):
        calls.append((query, page_token, limit))
        return {"messages": [{"id": x} for x in ("old", "own", "new")],
                "nextPageToken": "page2" if not page_token else ""}

    async def get(_uid, _char_id, mid):
        return {
            "id": mid, "internalDate": baseline + (-1 if mid == "old" else 1),
            "labelIds": ["SENT"] if mid == "own" else ["INBOX"],
            "payload": {"headers": [{"name": "Subject", "value": "private subject"}]},
        }

    monkeypatch.setattr(gmail_poll.messages, "list_page", page)
    monkeypatch.setattr(gmail_poll.messages, "get", get)
    await gmail_poll.tick("owner", "character", now=now + 5)
    signals = store.drain_pending_signals("owner", "character")
    assert len(signals) == 1 and signals[0].source == gmail_poll.SOURCE
    assert signals[0].evidence == [{"fact": "gmail_message_available"}]
    assert "private subject" not in str(store.load("owner", "character"))

    # A crash before saving the poll cursor can replay the page after drain.
    gmail.save(initial)
    await gmail_poll.tick("owner", "character", now=now + 5)
    assert not store.drain_pending_signals("owner", "character")
    await gmail_poll.tick("owner", "character", now=now + 65)
    assert calls[-1][1] == "page2"
    assert not store.drain_pending_signals("owner", "character")
    assert gmail.load()["poll"]["last_sync"] == now + 65


@pytest.mark.asyncio
async def test_poll_backoff_and_off_while_request_in_flight(connected, monkeypatch):
    now = int(time.time())
    await gmail_poll.tick("owner", "character", now=now)

    async def broken(*args, **kwargs):
        raise gmail.GmailError("rate_limited")

    monkeypatch.setattr(gmail_poll.messages, "list_page", broken)
    await gmail_poll.tick("owner", "character", now=now + 2)
    poll = gmail.load()["poll"]
    assert poll["error"] == "rate_limited"
    assert poll["next_due"] == now + 2 + 600
    await gmail_poll.tick("owner", "character", now=now + 10)
    assert gmail.load()["poll"] == poll

    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed(*args, **kwargs):
        started.set()
        await release.wait()
        return {}

    monkeypatch.setattr(gmail_poll.messages, "list_page", delayed)
    task = asyncio.create_task(gmail_poll.tick("owner", "character", now=now + 602))
    await started.wait()
    await gmail_poll.configure("owner", "character", False, 120)
    release.set()
    await task
    disabled = gmail.load()["poll"]
    assert not disabled["enabled"]
    assert not disabled.get("last_sync")
    assert disabled["interval"] == 120
