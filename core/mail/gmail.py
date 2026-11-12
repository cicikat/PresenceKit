"""Owner-bound Gmail OAuth and bounded transport."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import urlencode, urlsplit

import aiohttp

from core.config_loader import get_config
from core.sandbox import get_paths

READ = "https://www.googleapis.com/auth/gmail.readonly"
SEND = "https://www.googleapis.com/auth/gmail.send"
CALLBACK = "/settings/gmail/oauth/callback"
AUTHORIZE = "/settings/gmail/oauth/authorize"
API = "https://gmail.googleapis.com/gmail/v1/users/me/"
TOKEN = "https://oauth2.googleapis.com/token"
REVOKE = "https://oauth2.googleapis.com/revoke"
COOKIE = "__Secure-presencekit-gmail"
LOCK = asyncio.Lock()
_access: tuple[str, str, float] | None = None
_last_error = ""


class GmailError(Exception):
    """Only fixed, user-safe codes cross the transport boundary."""


@dataclass(frozen=True)
class OAuth:
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def origin(self) -> str:
        url = urlsplit(self.redirect_uri)
        return f"{url.scheme}://{url.netloc}"


def oauth_config() -> OAuth:
    cfg = OAuth(*(os.environ.get(key, "").strip() for key in (
        "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI",
    )))
    try:
        url = urlsplit(cfg.redirect_uri)
        valid = (
            url.scheme == "https" and bool(url.hostname) and url.username is None
            and url.password is None and not url.query and not url.fragment and url.path == CALLBACK
        )
    except ValueError:
        valid = False
    if not (cfg.client_id and cfg.client_secret and valid):
        raise GmailError("oauth_not_configured")
    return cfg


def owner() -> str:
    return str(get_config().get("scheduler", {}).get("owner_id") or "").strip()


def state_path() -> Path:
    return get_paths().auth_dir() / "gmail" / "mailbox.json"


def load() -> dict:
    try:
        return json.loads(state_path().read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise GmailError("storage_error") from None


def save(record: dict) -> None:
    path = state_path()
    tmp = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            path.parent.chmod(0o700)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(json.dumps(record).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    except OSError:
        raise GmailError("storage_error") from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def bound(record: dict, uid: str | None, char_id: str | None) -> bool:
    return bool(uid and char_id and uid == owner()
                and (record.get("uid"), record.get("char_id")) == (uid, char_id))


def allowed(uid: str | None, char_id: str | None, *scopes: str) -> bool:
    try:
        record = load()
        return bool(bound(record, uid, char_id) and record.get("refresh")
                    and all(scope in record.get("scopes", []) for scope in scopes))
    except GmailError:
        return False


def normalize_address(raw: str) -> str:
    from email.headerregistry import Address

    try:
        address = Address(addr_spec=raw.strip())
        if not address.username or not address.domain or len(address.addr_spec) > 254:
            raise ValueError
        return address.addr_spec
    except (ValueError, TypeError):
        raise GmailError("invalid_email_address") from None


async def request(method: str, url: str, *, sending: bool = False, token: str = "", **kwargs) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            async with session.request(method, url, headers=headers, allow_redirects=False, **kwargs) as res:
                if res.status >= 300:
                    if url == TOKEN and res.status == 400:
                        body = await res.content.read(4096)
                        try:
                            if json.loads(body).get("error") == "invalid_grant":
                                raise GmailError("reauthorization_required")
                        except (ValueError, AttributeError):
                            pass
                    code = {
                        401: "reauthorization_required", 403: "scope_or_policy_denied",
                        404: "not_found", 429: "rate_limited",
                    }.get(res.status, "upstream_unavailable" if res.status >= 500 else "request_rejected")
                    raise GmailError("outcome_unknown" if sending and res.status >= 500 else code)
                if url == REVOKE:
                    return {}
                body = bytearray()
                async for chunk in res.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise GmailError("outcome_unknown" if sending else "response_too_large")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError
                return result
    except (aiohttp.ClientError, TimeoutError, ValueError):
        raise GmailError("outcome_unknown" if sending else "transport_or_response_error") from None


async def token_exchange(cfg: OAuth, fields: dict) -> dict:
    result = await request("POST", TOKEN, data={
        "client_id": cfg.client_id, "client_secret": cfg.client_secret, **fields,
    })
    if not result.get("access_token"):
        raise GmailError("invalid_token_response")
    return result


def cache_access(record: dict, result: dict) -> None:
    global _access
    try:
        ttl = int(result.get("expires_in", 0))
    except (ValueError, TypeError):
        ttl = 0
    _access = (record["refresh"], result["access_token"], time.monotonic() + ttl)


async def access_token(uid: str, char_id: str, *scopes: str) -> str:
    global _last_error
    async with LOCK:
        record = load()
        if not bound(record, uid, char_id) or not record.get("refresh"):
            raise GmailError("gmail_not_connected")
        if not all(scope in record.get("scopes", []) for scope in scopes):
            raise GmailError("scope_missing")
        if _access and _access[0] == record["refresh"] and _access[2] > time.monotonic() + 60:
            return _access[1]
        try:
            result = await token_exchange(oauth_config(), {
                "grant_type": "refresh_token", "refresh_token": record["refresh"],
            })
            if result.get("refresh_token"):
                record["refresh"] = result["refresh_token"]
                save(record)
            cache_access(record, result)
            _last_error = ""
            return result["access_token"]
        except GmailError as exc:
            _last_error = str(exc)
            raise


async def api(uid: str, char_id: str, method: str, endpoint: str, *,
              scopes: tuple[str, ...] = (READ,), **kwargs) -> dict:
    global _access, _last_error
    token = await access_token(uid, char_id, *scopes)
    try:
        return await request(method, API + endpoint, token=token, **kwargs)
    except GmailError as exc:
        if str(exc) == "reauthorization_required" and _access and _access[1] == token:
            _access = None
        _last_error = str(exc)
        raise


@dataclass
class Flow:
    uid: str
    char_id: str
    email: str
    admin_token: str
    expires: float
    state: str
    verifier: str
    browser: str


_tickets: dict[str, Flow] = {}
_flows: dict[str, Flow] = {}


def start(uid: str, char_id: str, email: str, admin_token: str) -> str:
    cfg = oauth_config()
    record = load()
    if record.get("refresh") and (not bound(record, uid, char_id) or record.get("email") != email):
        raise GmailError("disconnect_existing_mailbox")
    now = time.monotonic()
    for pool in (_tickets, _flows):
        for key, flow in list(pool.items()):
            if flow.expires <= now or (flow.uid, flow.char_id) == (uid, char_id):
                del pool[key]
        if len(pool) >= 32:
            raise GmailError("authorization_busy")
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = Flow(uid, char_id, email, admin_token, now + 600, secrets.token_urlsafe(32),
                            secrets.token_urlsafe(64), secrets.token_urlsafe(32))
    return f"{cfg.origin}{AUTHORIZE}?ticket={ticket}"


def authorize(ticket: str) -> tuple[str, str]:
    flow = _tickets.pop(ticket, None)
    if not flow or flow.expires <= time.monotonic():
        raise GmailError("invalid_authorization_session")
    cfg = oauth_config()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(flow.verifier.encode()).digest()).rstrip(b"=").decode()
    _flows[flow.state] = flow
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": cfg.client_id, "redirect_uri": cfg.redirect_uri, "response_type": "code",
        "scope": f"{READ} {SEND}", "access_type": "offline", "prompt": "consent",
        "login_hint": flow.email, "state": flow.state, "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return url, flow.browser


async def complete(state: str, browser: str, code: str, uid: str, char_id: str, admin_valid) -> None:
    global _last_error
    flow = _flows.get(state)
    if not flow or flow.expires <= time.monotonic() or not hmac.compare_digest(flow.browser, browser):
        raise GmailError("invalid_authorization_session")
    del _flows[state]
    if (flow.uid, flow.char_id) != (uid, char_id) or not admin_valid(flow.admin_token):
        raise GmailError("invalid_authorization_principal")
    if not code:
        raise GmailError("authorization_declined")
    async with LOCK:
        record = load()
        if record.get("refresh") and (not bound(record, uid, char_id) or record.get("email") != flow.email):
            raise GmailError("disconnect_existing_mailbox")
        cfg = oauth_config()
        try:
            result = await token_exchange(cfg, {
                "grant_type": "authorization_code", "code": code, "redirect_uri": cfg.redirect_uri,
                "code_verifier": flow.verifier,
            })
            scopes = set(str(result.get("scope", "")).split())
            if READ not in scopes or not result.get("refresh_token"):
                raise GmailError("readonly_or_offline_grant_missing")
            profile = await request("GET", API + "profile", token=result["access_token"])
            if str(profile.get("emailAddress") or "").casefold() != flow.email:
                raise GmailError("google_account_mismatch")
            if not admin_valid(flow.admin_token) or uid != owner():
                raise GmailError("invalid_authorization_principal")
            save({**record, "uid": uid, "char_id": char_id, "email": flow.email,
                  "scopes": sorted(scopes & {READ, SEND}), "refresh": result["refresh_token"]})
            cache_access(load(), result)
            _last_error = ""
        except GmailError as exc:
            _last_error = str(exc)
            raise


async def disconnect(uid: str, char_id: str) -> str:
    global _access, _last_error
    async with LOCK:
        record = load()
        if record and not bound(record, uid, char_id):
            raise GmailError("gmail_not_connected")
        for pool in (_tickets, _flows):
            for key, flow in list(pool.items()):
                if (flow.uid, flow.char_id) == (uid, char_id):
                    del pool[key]
        refresh = record.get("refresh")
        if not refresh:
            return "already_disconnected"
        # Keep the delivery ledger across disconnect/reconnect.
        save({key: value for key, value in record.items() if key not in {"refresh", "scopes", "poll"}})
        _access = None
        _last_error = ""
        try:
            await request("POST", REVOKE, data={"token": refresh})
            return "revoked"
        except GmailError:
            return "local_disconnect_upstream_unconfirmed"


def status(uid: str, char_id: str) -> dict:
    record = load()
    same = bound(record, uid, char_id)
    try:
        oauth_config()
        configured = True
    except GmailError:
        configured = False
    return {
        "configured": configured, "char_id": char_id,
        "connected": bool(same and record.get("refresh")),
        "bound_elsewhere": bool(not same and record.get("refresh")),
        "email": record.get("email", "") if same else "",
        "scopes": record.get("scopes", []) if same else [],
        "error": _last_error if same else "",
    }
