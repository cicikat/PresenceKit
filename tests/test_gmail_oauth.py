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


@pytest.mark.asyncio
async def test_first_authorization_error_survives_restart_and_is_role_scoped(mailbox, monkeypatch):
    state, browser = flow()
    with pytest.raises(gmail.GmailError, match="disabled_client"):
        await gmail.complete(state, browser, "", "owner", "character", lambda _: True,
                             error="disabled_client")
    monkeypatch.setattr(gmail, "_last_error", "")
    assert gmail.status("owner", "character")["token_state"] == "client_disabled"
    assert gmail.status("owner", "other")["error"] == ""
    from admin.routers.settings_gmail import _result
    assert "Google Cloud" in _result(False, "disabled_client").body.decode()


@pytest.mark.asyncio
async def test_web_configuration_survives_authorize_revoke_and_restart(mailbox, monkeypatch):
    await gmail.configure_client("123-fixture.apps.googleusercontent.com", "fixture-secret",
                                 "https://example.test" + gmail.CALLBACK)
    stored_config = gmail.load()["oauth_client"]

    async def request(method, url, **kwargs):
        if url == gmail.TOKEN:
            return {"access_token": "access", "refresh_token": "refresh", "scope": gmail.READ}
        if url.endswith("/profile"):
            return {"emailAddress": "mail@example.test"}
        return {}

    monkeypatch.setattr(gmail, "request", request)
    state, browser = flow()
    await gmail.complete(state, browser, "code", "owner", "character", lambda _: True)
    assert gmail.load()["oauth_client"] == stored_config
    assert await gmail.disconnect("owner", "character") == "revoked"
    monkeypatch.setattr(gmail, "_access", None)
    assert gmail.load()["oauth_client"] == stored_config
    assert gmail.status("owner", "character")["token_state"] == "waiting_authorization"


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
        assert denied.status_code == 400 and "access_denied" in denied.text
        assert "private" not in denied.text
        assert client.get(gmail.CALLBACK, params={"state": state, "code": "private"}).status_code == 400


CLIENT_ID = "123-fixture.apps.googleusercontent.com"
REDIRECT_URI = "https://example.test" + gmail.CALLBACK


@pytest.mark.asyncio
async def test_client_configuration_persists_and_rotation_preserves_mailbox(mailbox, monkeypatch):
    state, browser = flow()
    original = {"uid": "owner", "char_id": "character", "email": "mail@example.test",
                "refresh": "existing-refresh", "scopes": [gmail.READ],
                "poll": {"enabled": False}, "deliveries": {"fixture": {"state": "sent"}}}
    gmail.save(original)
    gmail._access = ("existing-refresh", "old-access", 10**12, gmail.oauth_config().revision)
    old_revision = gmail.config_status()["credential_revision"]
    result = await gmail.configure_client(CLIENT_ID, "new-secret", REDIRECT_URI)
    assert result["source"] == "private_file" and result["configured"]
    assert result["credential_revision"] != old_revision
    assert result["client_secret_set"] and "new-secret" not in str(result)
    assert gmail.load()["refresh"] == original["refresh"]
    assert gmail.load()["poll"] == original["poll"]
    assert gmail.load()["deliveries"] == original["deliveries"]
    assert gmail._access is None and not gmail._flows and not gmail._tickets
    assert gmail.status("owner", "character")["token_state"] == "pending_verification"
    with pytest.raises(gmail.GmailError, match="invalid_authorization_session"):
        await gmail.complete(state, browser, "code", "owner", "character", lambda _: True)
    # Simulate a fresh process: only the private file remains authoritative.
    for key in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI"):
        monkeypatch.delenv(key)
    assert gmail.oauth_config().client_secret == "new-secret"
    same = await gmail.configure_client(CLIENT_ID, "", REDIRECT_URI)
    assert same == result
    assert gmail.oauth_config().client_secret == "new-secret"
    if os.name != "nt":
        assert gmail.state_path().stat().st_mode & 0o777 == 0o600
        assert gmail.state_path().parent.stat().st_mode & 0o777 == 0o700

    async def request(method, url, **kwargs):
        assert url == gmail.TOKEN
        assert kwargs["data"]["client_id"] == CLIENT_ID
        assert kwargs["data"]["client_secret"] == "new-secret"
        return {"access_token": "new-access", "expires_in": 3600}

    monkeypatch.setattr(gmail, "request", request)
    assert await gmail.access_token("owner", "character", gmail.READ) == "new-access"
    assert gmail.status("owner", "character")["token_state"] == "valid"


@pytest.mark.asyncio
async def test_successful_refresh_clears_previous_authorization_error(mailbox, monkeypatch):
    gmail.save({"uid": "owner", "char_id": "character", "email": "mail@example.test",
                "refresh": "fixture-refresh", "scopes": [gmail.READ],
                "authorization_error": {"uid": "owner", "char_id": "character",
                    "revision": gmail.oauth_config().revision, "error": "disabled_client"}})

    async def request(*args, **kwargs):
        return {"access_token": "fixture-access", "expires_in": 3600}

    monkeypatch.setattr(gmail, "request", request)
    assert await gmail.access_token("owner", "character", gmail.READ) == "fixture-access"
    assert "authorization_error" not in gmail.load()
    assert gmail.status("owner", "character")["token_state"] == "valid"


@pytest.mark.parametrize("uri", [
    "http://example.test" + gmail.CALLBACK,
    "https://localhost" + gmail.CALLBACK,
    "https://127.0.0.1" + gmail.CALLBACK,
    "https://10.0.0.1" + gmail.CALLBACK,
    "https://[::1]" + gmail.CALLBACK,
    "https://server.local" + gmail.CALLBACK,
    "https://example.test:invalid" + gmail.CALLBACK,
    "https://user:pass@example.test" + gmail.CALLBACK,
    REDIRECT_URI + "?code=private",
    REDIRECT_URI + "#fragment",
    REDIRECT_URI + "?",
    "https://example.test/elsewhere",
])
def test_web_configuration_rejects_unsafe_callback(mailbox, uri):
    with pytest.raises(gmail.GmailError, match="invalid_oauth_config"):
        gmail.validate_config(CLIENT_ID, "fixture-secret", uri)
    assert gmail.load() == {}


@pytest.mark.asyncio
async def test_failed_atomic_write_preserves_config_and_sessions(mailbox, monkeypatch):
    await gmail.configure_client(CLIENT_ID, "old-secret", REDIRECT_URI)
    state, _ = flow()
    before = gmail.state_path().read_bytes()

    def fail(*args):
        raise OSError("fixture failure")

    monkeypatch.setattr(type(gmail.state_path()), "replace", fail)
    with pytest.raises(gmail.GmailError, match="storage_error"):
        await gmail.configure_client(CLIENT_ID, "replacement", REDIRECT_URI)
    assert gmail.state_path().read_bytes() == before
    assert state in gmail._flows


def test_config_api_is_admin_only_and_never_echoes_secrets(mailbox, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin.routers import settings_gmail
    from admin import auth as admin_auth

    monkeypatch.setenv("YEXUAN_ADMIN_SECRET", "qa-admin")
    resolve = admin_auth.resolve_token
    monkeypatch.setattr(admin_auth, "resolve_token", lambda token: admin_auth.TokenInfo(
        label="mobile", scopes=frozenset({"chat", "state.read"})) if token == "qa-mobile" else resolve(token))
    app = FastAPI()
    app.include_router(settings_gmail.router)
    body = {"client_id": CLIENT_ID, "client_secret": "private-secret", "redirect_uri": REDIRECT_URI}
    auth = {"Authorization": "Bearer qa-admin"}
    with TestClient(app) as client:
        for method, path in (("GET", "/settings/gmail/oauth/config"),
                             ("PUT", "/settings/gmail/oauth/config"),
                             ("POST", "/settings/gmail/oauth/config/validate")):
            assert client.request(method, path, json=body).status_code == 401
            assert client.request(method, path, json=body,
                                  headers={"Authorization": "Bearer qa-mobile"}).status_code == 403
        validated = client.post("/settings/gmail/oauth/config/validate", headers=auth, json=body)
        assert validated.json() == {"valid": True, "authorization_verified": False}
        assert gmail.load() == {}
        saved = client.put("/settings/gmail/oauth/config", headers=auth, json=body)
        assert saved.status_code == 200 and "private-secret" not in saved.text
        response = client.get("/settings/gmail/oauth/config", headers=auth)
        assert response.json() == saved.json()
        assert response.headers["cache-control"] == "no-store"
        bad = client.put("/settings/gmail/oauth/config", headers=auth,
                         json={**body, "client_secret": "private-secret" * 200})
        assert bad.status_code == 422 and "private-secret" not in bad.text
        kept = client.put("/settings/gmail/oauth/config", headers=auth, json={**body, "client_secret": ""})
        assert kept.status_code == 200 and gmail.oauth_config().client_secret == "private-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(gmail.TOKEN_ERRORS))
async def test_google_token_error_classification_does_not_echo_response(mailbox, monkeypatch, code):
    class Content:
        async def read(self, limit):
            return ('{"error":"' + code + '","error_description":"private-secret"}').encode()

    class Response:
        status = 401
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session(Response):
        def __init__(self, **kwargs):
            pass

        def request(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(gmail.aiohttp, "ClientSession", Session)
    with pytest.raises(gmail.GmailError) as error:
        await gmail.request("POST", gmail.TOKEN)
    assert str(error.value) == code


@pytest.mark.asyncio
async def test_disabled_client_status_survives_restart(mailbox, monkeypatch):
    gmail.save({"uid": "owner", "char_id": "character", "email": "mail@example.test",
                "refresh": "fixture-refresh", "scopes": [gmail.READ]})

    async def request(*args, **kwargs):
        raise gmail.GmailError("disabled_client")

    monkeypatch.setattr(gmail, "request", request)
    with pytest.raises(gmail.GmailError, match="disabled_client"):
        await gmail.access_token("owner", "character", gmail.READ)
    gmail._last_error = ""
    assert gmail.status("owner", "character")["token_state"] == "client_disabled"
    assert gmail.load()["refresh"] == "fixture-refresh"
    await gmail.configure_client(CLIENT_ID, "replacement", REDIRECT_URI)
    assert gmail.status("owner", "character")["token_state"] == "pending_verification"
