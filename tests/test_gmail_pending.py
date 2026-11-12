
from types import SimpleNamespace
import time

import pytest

from core.autonomy import runner, store
from core.autonomy.models import Job, Opportunity, Run, Signal
from core.mail import gmail, gmail_pending, gmail_poll
from core.tools import gmail_tools
from test_gmail_messages import connected  # noqa: F401
from test_gmail_oauth import mailbox  # noqa: F401


def _seed(*ids):
    record = gmail.load()
    now = time.time()
    gmail_pending.add_discovered(record, [(mid, int(now * 1000)) for mid in ids], now)
    gmail.save(record)


@pytest.mark.asyncio
async def test_pending_outlives_signal_in_real_autonomy_evaluation(connected, monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_path", lambda *_: tmp_path / "autonomy.json")
    _seed("private-id-one", "private-id-two", "private-id-three")
    state = store.load("owner", "character")
    monkeypatch.setattr(runner.policy, "admission", lambda *args: None)
    monkeypatch.setattr(runner.policy, "allowed_tools", lambda *args: [])
    monkeypatch.setattr(runner, "_user_became_active", lambda *_: False)
    seen = []

    async def chat_turn(_preset, messages, *_args, **_kwargs):
        seen.append(messages)
        return SimpleNamespace(tool_calls=[], continuation_items=[], assistant_message={})

    monkeypatch.setattr("core.llm_client.chat_turn", chat_turn)

    async def evaluate():
        opportunity = Opportunity.merge([
            Signal(source="interval", reason="normal evaluation", expiry=time.time() + 600),
        ]).to_dict()
        return await runner._run_locked(
            Job(uid="owner", char_id="character", source="autonomy", opportunity=opportunity),
            state,
            Run(uid="owner", char_id="character", source="autonomy", job_id="job"),
        )

    first = await evaluate()
    summary = next(row["content"] for row in first.prompt_snapshot
                   if row.get("_layer") == "gmail_pending_summary")
    assert "pending_count: 3" in summary
    assert "private-id" not in summary
    assert seen

    for row in gmail_pending.list_items("owner", "character")["items"]:
        await gmail_tools.gmail_mark_handled("owner", "character", row["pending_id"], row["revision"])
    second = await evaluate()
    assert not any(row.get("_layer") == "gmail_pending_summary" for row in second.prompt_snapshot)


@pytest.mark.asyncio
async def test_read_does_not_handle_unknown_reply_and_verified_reply_does(connected, monkeypatch):
    _seed("message-one", "message-two")
    inbox = gmail_pending.list_items("owner", "character")["items"]
    record = gmail.load()
    ids = {gmail_pending.resolve_message_id(record, row["pending_id"]): row for row in inbox}

    async def request(method, url, **kwargs):
        if method == "GET":
            mid = url.rsplit("/", 1)[-1]
            return {"id": mid, "threadId": "thread", "labelIds": ["INBOX", "UNREAD"],
                    "payload": {"headers": [
                        {"name": "Message-ID", "value": "<example@example.test>"},
                        {"name": "From", "value": "external@example.test"},
                    ]}}
        if kwargs["json"]["threadId"] == "thread" and not getattr(request, "accept", False):
            raise gmail.GmailError("outcome_unknown")
        return {"id": "outgoing"}

    monkeypatch.setattr(gmail, "request", request)
    first, second = ids["message-one"], ids["message-two"]
    await gmail_tools.gmail_read_message("owner", "character", first["pending_id"])
    assert gmail.status("owner", "character")["pending_count"] == 2

    with pytest.raises(gmail.GmailError, match="outcome_unknown"):
        await gmail_tools.gmail_reply_message("owner", "character", first["pending_id"], "text", "reply-1")
    assert gmail.status("owner", "character")["pending_count"] == 2

    request.accept = True
    await gmail_tools.gmail_reply_message("owner", "character", second["pending_id"], "text", "reply-2")
    assert gmail.status("owner", "character")["pending_count"] == 1
    rows = gmail_pending.list_items("owner", "character", include_handled=True)["items"]
    handled = next(row for row in rows if row["pending_id"] == second["pending_id"])
    with pytest.raises(gmail.GmailError, match="pending_revision_conflict"):
        await gmail_tools.gmail_reopen_pending("owner", "character", second["pending_id"], second["revision"])
    await gmail_tools.gmail_reopen_pending("owner", "character", second["pending_id"], handled["revision"])
    assert gmail.status("owner", "character")["pending_count"] == 2

    record = gmail.load()
    record["email"] = "other@example.test"
    gmail.save(record)
    assert gmail.status("owner", "character")["pending_count"] == 0
    assert gmail_pending.list_items("owner", "another-character")["items"] == []
    with pytest.raises(gmail.GmailError, match="pending_not_found"):
        await gmail_tools.gmail_mark_handled("owner", "character", first["pending_id"], first["revision"])


@pytest.mark.asyncio
async def test_overflow_keeps_the_scan_cursor_and_no_partial_page(connected, monkeypatch):
    monkeypatch.setattr(gmail_pending, "MAX_ROWS", 1)
    record = gmail.load()
    record["poll"] = gmail_poll.baseline()
    cursor = record["poll"]["cursor"]
    gmail.save(record)

    async def page(*_args, **_kwargs):
        return {"messages": [{"id": "first"}, {"id": "second"}]}

    async def get(_uid, _char_id, mid):
        return {"id": mid, "internalDate": cursor + 1, "labelIds": ["INBOX"]}

    monkeypatch.setattr(gmail_poll.messages, "list_page", page)
    monkeypatch.setattr(gmail_poll.messages, "get", get)
    now = cursor / 1000 + 10
    await gmail_poll.tick("owner", "character", now=now)
    assert gmail.load()["poll"]["cursor"] == cursor
    assert gmail.status("owner", "character")["overflow"]
    assert gmail.status("owner", "character")["pending_count"] == 0

    monkeypatch.setattr(gmail_pending, "MAX_ROWS", 2)
    await gmail_poll.tick("owner", "character", now=now + 305)
    assert gmail.load()["poll"]["cursor"] > cursor
    assert not gmail.status("owner", "character")["overflow"]
    assert gmail.status("owner", "character")["pending_count"] == 2
