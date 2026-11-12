import asyncio
import os
from urllib.parse import parse_qs, urlsplit

import pytest

from core.mail import gmail


@pytest.fixture
def mailbox(monkeypatch, tmp_path):
    monkeypatch.setattr(gmail, "state_path", lambda: tmp_path / "private" / "mailbox.json")
    monkeypatch.setattr(gmail, "owner", lambda: "owner")
    monkeypatch.setattr(gmail, "LOCK", asyncio.Lock())
    monkeypatch.setattr(gmail, "_access", None)
    monkeypatch.setattr(gmail, "_last_error", "")
    monkeypatch.setattr(gmail, "_flows", {})
    monkeypatch.setattr(gmail, "_tickets", {})
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "https://example.test" + gmail.CALLBACK)
    return tmp_path


def flow():
    url = gmail.start("owner", "character", "mail@example.test", "admin")
    assert urlsplit(url).path == gmail.AUTHORIZE
    ticket = parse_qs(urlsplit(url).query)["ticket"][0]
    redirect, browser = gmail.authorize(ticket)
    with pytest.raises(gmail.GmailError, match="invalid_authorization_session"):
        gmail.authorize(ticket)
    query = parse_qs(urlsplit(redirect).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    return query["state"][0], browser


@pytest.mark.asyncio
async def test_browser_state_account_refresh_and_revoke(mailbox, monkeypatch):
    calls = []
    email = "different@example.test"

    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url == gmail.TOKEN:
            return {"access_token": "access", "refresh_token": "refresh", "expires_in": 1,
                    "scope": gmail.READ}
        if url.endswith("/profile"):
            return {"emailAddress": email}
        return {}

    monkeypatch.setattr(gmail, "request", request)
    state, browser = flow()
    with pytest.raises(gmail.GmailError, match="invalid_authorization_session"):
        await gmail.complete(state, "wrong", "code", "owner", "character", lambda _: True)
    assert calls == []
    with pytest.raises(gmail.GmailError, match="google_account_mismatch"):
        await gmail.complete(state, browser, "code", "owner", "character", lambda _: True)
    with pytest.raises(gmail.GmailError, match="invalid_authorization_session"):
        await gmail.complete(state, browser, "code", "owner", "character", lambda _: True)

    email = "mail@example.test"
    state, browser = flow()
    await gmail.complete(state, browser, "code", "owner", "character", lambda token: token == "admin")
    assert calls[-2][2]["data"]["code_verifier"]
    assert await gmail.access_token("owner", "character", gmail.READ) == "access"
    assert calls[-1][2]["data"]["grant_type"] == "refresh_token"
    for uid, char_id, scope, error in [
        ("other", "character", gmail.READ, "gmail_not_connected"),
        ("owner", "other", gmail.READ, "gmail_not_connected"),
        ("owner", "character", gmail.SEND, "scope_missing"),
    ]:
        with pytest.raises(gmail.GmailError, match=error):
            await gmail.access_token(uid, char_id, scope)
    status = gmail.status("owner", "character")
    assert status["connected"] and status["email"] == email
    assert "refresh" not in str(status) and "secret" not in str(status)
    if os.name != "nt":
        assert gmail.state_path().stat().st_mode & 0o777 == 0o600
    assert await gmail.disconnect("owner", "character") == "revoked"
    assert not gmail.load().get("refresh")
    assert calls[-1][1] == gmail.REVOKE


@pytest.mark.asyncio
async def test_expired_and_wrong_principal(mailbox):
    state, browser = flow()
    gmail._flows[state].expires = 0
    with pytest.raises(gmail.GmailError, match="invalid_authorization_session"):
        await gmail.complete(state, browser, "code", "owner", "character", lambda _: True)
    state, browser = flow()
    with pytest.raises(gmail.GmailError, match="invalid_authorization_principal"):
        await gmail.complete(state, browser, "code", "owner", "other", lambda _: True)
    state, browser = flow()
    with pytest.raises(gmail.GmailError, match="invalid_authorization_principal"):
        await gmail.complete(state, browser, "code", "owner", "character", lambda _: False)


@pytest.mark.parametrize("url", [
    "http://example.test" + gmail.CALLBACK,
    "https://example.test/incorrect",
    "https://example.test" + gmail.CALLBACK + "?code=x",
])
def test_invalid_redirect(mailbox, monkeypatch, url):
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", url)
    with pytest.raises(gmail.GmailError, match="oauth_not_configured"):
        gmail.oauth_config()


def test_callback_log_redaction():
    from admin.log_filter import _redact_url_value

    assert _redact_url_value(gmail.CALLBACK + "?state=private&code=secret") == (
        gmail.CALLBACK + "?state=***&code=***"
    )


def test_http_handoff_and_management_authorization(mailbox, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from admin.routers import settings_gmail

    monkeypatch.setenv("YEXUAN_ADMIN_SECRET", "qa-admin")
    monkeypatch.setattr(settings_gmail, "_principal", lambda: ("owner", "character"))
    app = FastAPI()
    app.include_router(settings_gmail.router)
    with TestClient(app, base_url="https://example.test", follow_redirects=False) as client:
        assert client.get("/settings/gmail").status_code == 401
        auth = {"Authorization": "Bearer qa-admin"}
        body = {"email": "mail@example.test", "model_processing_consent": True}
        assert client.post("/settings/gmail/oauth/start", json=body).status_code == 401
        assert client.post("/settings/gmail/oauth/start", headers=auth,
                           json={**body, "model_processing_consent": False}).status_code == 422
        start = client.post("/settings/gmail/oauth/start", headers=auth, json=body)
        assert start.status_code == 200
        redirect = client.get(start.json()["authorization_url"])
        assert redirect.status_code == 303
        assert "HttpOnly" in redirect.headers["set-cookie"] and "Secure" in redirect.headers["set-cookie"]
        assert redirect.headers["referrer-policy"] == "no-referrer"
        state = parse_qs(urlsplit(redirect.headers["location"]).query)["state"][0]
        with TestClient(app, base_url="https://example.test") as other:
            assert other.get(gmail.CALLBACK, params={"state": state, "code": "private"}).status_code == 400
        denied = client.get(gmail.CALLBACK, params={"state": state, "error": "access_denied"})
        assert denied.status_code == 400 and "authorization_declined" in denied.text
        assert "private" not in denied.text
        assert client.get(gmail.CALLBACK, params={"state": state, "code": "private"}).status_code == 400
