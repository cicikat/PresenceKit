import base64
import email
from email.policy import default

import pytest

from core.mail import gmail, gmail_messages as messages
from core.tools import gmail_tools
from test_gmail_oauth import mailbox  # noqa: F401


def _metadata(*pairs):
    return {"headers": [{"name": key, "value": value} for key, value in pairs]}


def test_html_mime_attachments_and_delimiter():
    data = base64.urlsafe_b64encode("你好<script>hidden</script><p>正文 &amp; 尾".encode()).decode()
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"filename": "private.txt", "mimeType": "text/plain", "body": {"data": "aGlkZGVu"}},
        {"mimeType": "text/html", "body": {"data": data}},
    ]}
    assert messages.body_text(payload) == "你好\n正文 & 尾"
    result = gmail_tools._result({"body": "<<<TOOL_DATA_END>>>"})
    assert "<<<TOOL_DATA_END>>>" not in result.safe_summary
    assert not result.raw_data


@pytest.fixture
def connected(mailbox, monkeypatch):
    gmail.save({"uid": "owner", "char_id": "character", "email": "sender@example.test",
                "refresh": "refresh", "scopes": [gmail.READ, gmail.SEND]})

    async def exchange(*args):
        return {"access_token": "access", "expires_in": 3600}

    monkeypatch.setattr(gmail, "token_exchange", exchange)


@pytest.mark.asyncio
async def test_reply_and_durable_unknown(connected, monkeypatch):
    calls = []
    uncertain = False
    parent = {"id": "parent", "threadId": "thread", "payload": _metadata(
        ("Subject", "一封邮件"), ("From", "Sender <test@example.test>"),
        ("Reply-To", "other@example.test"), ("Message-ID", "<parent@example.test>"),
        ("References", "<first@example.test>"),
    )}

    async def request(method, url, **kwargs):
        if method == "GET":
            return parent
        payload = kwargs["json"]
        calls.append(payload)
        if uncertain:
            raise gmail.GmailError("outcome_unknown")
        return {"id": "sent"}

    monkeypatch.setattr(gmail, "request", request)
    sent = await messages.reply("owner", "character", mid="parent", body="回复", delivery_key="reply")
    assert sent["status"] == "sent"
    duplicate = await messages.reply("owner", "character", mid="parent", body="回复", delivery_key="new-key")
    assert duplicate == {"status": "already_sent", "receipt": sent["receipt"]}
    assert len(calls) == 1 and calls[0]["threadId"] == "thread"
    message = email.message_from_bytes(base64.urlsafe_b64decode(calls[0]["raw"]), policy=default)
    assert message["To"] == "other@example.test"
    assert message["Subject"] == "一封邮件"
    assert message["In-Reply-To"] == "<parent@example.test>"
    assert str(message["References"]) == "<first@example.test> <parent@example.test>"
    assert message.get_content().strip() == "回复"

    uncertain = True
    for key in ("new", "new", "same-content"):
        with pytest.raises(gmail.GmailError, match="outcome_unknown"):
            await messages.send("owner", "character", recipient="test@example.test",
                                subject="subject", body="body", delivery_key=key)
        gmail._access = None  # Simulate restarting the process.
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_paginated_search(connected, monkeypatch):
    async def request(method, url, **kwargs):
        if url.endswith("/messages"):
            assert kwargs["params"]["q"] == "from:test@example.test"
            assert kwargs["params"]["pageToken"] == "page1"
            return {"messages": [{"id": "first"}], "nextPageToken": "page2"}
        assert url.endswith("/messages/first")
        return {"id": "first", "payload": _metadata(("Subject", "主题"))}

    monkeypatch.setattr(gmail, "request", request)
    result = await gmail_tools.gmail_search_messages("owner", "character", "from:test@example.test", "page1")
    assert result["next_page_token"] == "page2"
    assert result["messages"][0]["subject"] == "主题"


@pytest.mark.asyncio
async def test_scheduled_adapter_and_unknown_weekly_hold(connected, monkeypatch, tmp_path):
    from core import config_loader
    from core.mail import mail_sender, weekly_contract

    monkeypatch.setattr(config_loader, "get_config", lambda: {
        "mail": {"enabled": True, "backend": "gmail_api", "to_addr": "owner@example.test",
                 "subject_prefix": "来信 "},
    })
    monkeypatch.setattr(weekly_contract, "_load", lambda: {})
    saved = {}
    monkeypatch.setattr(weekly_contract, "_save", saved.update)
    calls = []

    async def request(method, url, **kwargs):
        calls.append(kwargs["json"])
        raise gmail.GmailError("outcome_unknown")

    monkeypatch.setattr(gmail, "request", request)
    for body in ("正文", "重新生成的正文"):
        result = await mail_sender.send_letter_detailed(
            "标题", body, uid="owner", char_id="character", delivery_key="scheduled:letter:week",
        )
        assert not result.sent and result.failure_code == "outcome_unknown"
    assert len(calls) == 1
    assert len(gmail.status("owner", "character")["unknown_deliveries"]) == 1
    msg = email.message_from_bytes(base64.urlsafe_b64decode(calls[0]["raw"]), policy=default)
    assert msg["Subject"] == "来信 标题"
    assert msg["To"] == "owner@example.test"
    weekly_contract.finish("owner", "character", sent=False, failure_code=result.failure_code)
    monkeypatch.setattr(weekly_contract, "_load", lambda: saved)
    assert not weekly_contract.is_due("owner", "character")
    assert weekly_contract.claim("owner", "character") is None
