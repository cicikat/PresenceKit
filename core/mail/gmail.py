"""Owner-bound Gmail OAuth and bounded transport."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import ipaddress
import os
from pathlib import Path
import re
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
_access: tuple[str, str, float, str] | None = None
_last_error = ""
TOKEN_ERRORS = frozenset({"disabled_client", "invalid_client", "invalid_grant",
                          "redirect_uri_mismatch", "access_denied", "unauthorized_client"})


class GmailError(Exception):
    """Only fixed, user-safe codes cross the transport boundary."""


@dataclass(frozen=True)
class OAuth:
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def revision(self) -> str:
        return hashlib.sha256(json.dumps((self.client_id, self.client_secret,
                                          self.redirect_uri)).encode()).hexdigest()[:24]

    @property
    def origin(self) -> str:
        url = urlsplit(self.redirect_uri)
        return f"{url.scheme}://{url.netloc}"


def _raw_config() -> tuple[OAuth, str]:
    stored = load().get("oauth_client")
    if stored is not None:
        if not isinstance(stored, dict):
            raise GmailError("storage_error")
        return OAuth(*(str(stored.get(key, "")) for key in (
            "client_id", "client_secret", "redirect_uri"))), "private_file"
    return OAuth(*(os.environ.get(key, "").strip() for key in (
        "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI",
    ))), "environment"


def _validate_config(cfg: OAuth, *, web: bool = False) -> None:
    try:
        url = urlsplit(cfg.redirect_uri)
        host = url.hostname or ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            public = bool(re.fullmatch(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
                                       r"[A-Za-z]{2,63}", host)) and not host.lower().endswith(
                                           (".localhost", ".local", ".internal", ".lan"))
        else:
            public = address.is_global
        valid = (
            url.scheme == "https" and public and url.username is None
            and url.password is None and not url.query and not url.fragment and url.path == CALLBACK
            and url.port in (None, 443) and "?" not in cfg.redirect_uri and "#" not in cfg.redirect_uri
        )
    except ValueError:
        valid = False
    if not (cfg.client_id and cfg.client_secret and valid):
        raise GmailError("invalid_oauth_config" if web else "oauth_not_configured")
    if web and (not re.fullmatch(r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", cfg.client_id)
                or any(c.isspace() for c in cfg.client_secret)):
        raise GmailError("invalid_oauth_config")


def oauth_config() -> OAuth:
    cfg, _ = _raw_config()
    _validate_config(cfg)
    return cfg


def config_status() -> dict:
    cfg, source = _raw_config()
    try:
        _validate_config(cfg)
        configured = True
    except GmailError:
        configured = False
    return {"client_id": cfg.client_id, "client_secret_set": bool(cfg.client_secret),
            "redirect_uri": cfg.redirect_uri, "configured": configured, "source": source,
            "credential_revision": cfg.revision if configured else ""}


def validate_config(client_id: str, client_secret: str | None, redirect_uri: str) -> OAuth:
    previous, _ = _raw_config()
    cfg = OAuth(client_id.strip(), (client_secret or "").strip() or previous.client_secret,
                redirect_uri.strip())
    _validate_config(cfg, web=True)
    return cfg


async def configure_client(client_id: str, client_secret: str | None, redirect_uri: str) -> dict:
    global _access, _last_error
    async with LOCK:
        cfg = validate_config(client_id, client_secret, redirect_uri)
        previous, _ = _raw_config()
        record = load()
        if record.get("refresh") and "verified_revision" not in record:
            record["verified_revision"] = previous.revision
        record["oauth_client"] = {"client_id": cfg.client_id, "client_secret": cfg.client_secret,
                                  "redirect_uri": cfg.redirect_uri}
        if cfg != previous:
            record.pop("token_error", None)
            record.pop("authorization_error", None)
        save(record)
        if cfg != previous:
            _access = None
            _last_error = ""
            _tickets.clear()
            _flows.clear()
        return config_status()


def owner() -> str:
    return str(get_config().get("scheduler", {}).get("owner_id") or "").strip()


def state_path() -> Path:
    return get_paths().auth_dir() / "gmail" / "mailbox.json"


def load() -> dict:
    try:
        value = json.loads(state_path().read_bytes())
        if not isinstance(value, dict):
            raise ValueError
        return value
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
        if not bound(record, uid, char_id) or not record.get("refresh"):
            return False
        oauth_config()
        return all(scope in record.get("scopes", []) for scope in scopes)
    except GmailError:
        return False


def normalize_address(raw: str) -> str:
    from email.errors import HeaderParseError
    from email.headerregistry import Address

    try:
        address = Address(addr_spec=raw.strip())
        if not address.username or not address.domain or len(address.addr_spec) > 254:
            raise ValueError
        return address.addr_spec
    except (HeaderParseError, ValueError, TypeError):
        raise GmailError("invalid_email_address") from None


async def request(method: str, url: str, *, sending: bool = False, token: str = "", **kwargs) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            async with session.request(method, url, headers=headers, allow_redirects=False, **kwargs) as res:
                if res.status >= 300:
                    if url == TOKEN and res.status in (400, 401, 403):
                        body = await res.content.read(4096)
                        try:
                            error = json.loads(body).get("error")
                            if isinstance(error, str) and error in TOKEN_ERRORS:
                                raise GmailError(error)
                        except (ValueError, AttributeError):
                            pass
                    code = {
                        401: "reauthorization_required", 403: "scope_or_policy_denied",
                        404: "not_found", 429: "rate_limited",
                    }.get(res.status, "upstream_unavailable" if res.status >= 500 else "request_rejected")
                    raise GmailError("outcome_unknown" if sending and (res.status >= 500 or res.status == 408) else code)
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
    except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, ValueError):
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
    _access = (record["refresh"], result["access_token"], time.monotonic() + ttl,
               oauth_config().revision)


async def access_token(uid: str, char_id: str, *scopes: str) -> str:
    global _last_error
    async with LOCK:
        record = load()
        if not bound(record, uid, char_id) or not record.get("refresh"):
            raise GmailError("gmail_not_connected")
        if not all(scope in record.get("scopes", []) for scope in scopes):
            raise GmailError("scope_missing")
        cfg = oauth_config()
        if (_access and _access[0] == record["refresh"] and _access[3] == cfg.revision
                and _access[2] > time.monotonic() + 60):
            return _access[1]
        try:
            result = await token_exchange(cfg, {
                "grant_type": "refresh_token", "refresh_token": record["refresh"],
            })
            if result.get("refresh_token") or "scope" in result:
                record["refresh"] = result.get("refresh_token") or record["refresh"]
                if "scope" in result:
                    record["scopes"] = sorted(set(str(result["scope"]).split()) & {READ, SEND})
            record["verified_revision"] = cfg.revision
            record.pop("token_error", None)
            record.pop("authorization_error", None)
            save(record)
            if not all(scope in record.get("scopes", []) for scope in scopes):
                raise GmailError("scope_missing")
            cache_access(record, result)
            _last_error = ""
            return result["access_token"]
        except GmailError as exc:
            _last_error = str(exc)
            if str(exc) in TOKEN_ERRORS:
                record["token_error"] = str(exc)
                save(record)
            raise


def note_api_error(token: str, error: GmailError) -> None:
    global _access, _last_error
    if _access and _access[1] == token:
        _last_error = str(error)
        if str(error) == "reauthorization_required":
            _access = None


def token_current(record: dict, token: str) -> bool:
    return bool(_access and _access[:2] == (record.get("refresh"), token))


async def api(uid: str, char_id: str, method: str, endpoint: str, *,
              scopes: tuple[str, ...] = (READ,), **kwargs) -> dict:
    global _last_error
    token = await access_token(uid, char_id, *scopes)
    try:
        result = await request(method, API + endpoint, token=token, **kwargs)
        if _access and _access[1] == token:
            _last_error = ""
        return result
    except GmailError as exc:
        note_api_error(token, exc)
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
    revision: str


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
                            secrets.token_urlsafe(64), secrets.token_urlsafe(32), cfg.revision)
    return f"{cfg.origin}{AUTHORIZE}?ticket={ticket}"


def authorize(ticket: str) -> tuple[str, str]:
    flow = _tickets.pop(ticket, None)
    if not flow or flow.expires <= time.monotonic():
        raise GmailError("invalid_authorization_session")
    cfg = oauth_config()
    if cfg.revision != flow.revision:
        raise GmailError("invalid_authorization_session")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(flow.verifier.encode()).digest()).rstrip(b"=").decode()
    _flows[flow.state] = flow
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": cfg.client_id, "redirect_uri": cfg.redirect_uri, "response_type": "code",
        "scope": f"{READ} {SEND}", "access_type": "offline", "prompt": "consent",
        "login_hint": flow.email, "state": flow.state, "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return url, flow.browser


async def complete(state: str, browser: str, code: str, uid: str, char_id: str, admin_valid,
                   error: str = "") -> None:
    global _last_error
    if state not in _flows:
        raise GmailError("invalid_authorization_session")
    async with LOCK:
        flow = _flows.get(state)
        if not flow or flow.expires <= time.monotonic() or not hmac.compare_digest(flow.browser, browser):
            raise GmailError("invalid_authorization_session")
        del _flows[state]
        if (flow.uid, flow.char_id) != (uid, char_id) or not admin_valid(flow.admin_token):
            raise GmailError("invalid_authorization_principal")
        record = load()
        if record.get("refresh") and (not bound(record, uid, char_id) or record.get("email") != flow.email):
            raise GmailError("disconnect_existing_mailbox")
        cfg = oauth_config()
        if cfg.revision != flow.revision:
            raise GmailError("invalid_authorization_session")
        try:
            if error:
                raise GmailError(error if error in TOKEN_ERRORS else "authorization_declined")
            if not code:
                raise GmailError("authorization_declined")
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
            from core.mail.gmail_poll import baseline
            record.setdefault("poll", baseline())
            record.pop("token_error", None)
            record.pop("authorization_error", None)
            save({**record, "uid": uid, "char_id": char_id, "email": flow.email,
                  "scopes": sorted(scopes & {READ, SEND}), "refresh": result["refresh_token"],
                  "verified_revision": cfg.revision})
            cache_access(load(), result)
            _last_error = ""
        except GmailError as exc:
            _last_error = str(exc)
            if str(exc) in TOKEN_ERRORS:
                record["authorization_error"] = {"uid": uid, "char_id": char_id,
                    "revision": cfg.revision, "error": str(exc)}
                save(record)
            raise


async def disconnect(uid: str, char_id: str) -> str:
    global _access, _last_error
    async with LOCK:
        record = load()
        if record.get("refresh") and not bound(record, uid, char_id):
            raise GmailError("gmail_not_connected")
        for pool in (_tickets, _flows):
            for key, flow in list(pool.items()):
                if (flow.uid, flow.char_id) == (uid, char_id):
                    del pool[key]
        refresh = record.get("refresh")
        if not refresh:
            return "already_disconnected"
        # Keep the delivery ledger across disconnect/reconnect.
        save({key: value for key, value in record.items() if key not in {
            "refresh", "scopes", "poll", "token_error", "authorization_error", "verified_revision"}})
        _access = None
        _last_error = ""
        try:
            await request("POST", REVOKE, data={"token": refresh})
            return "revoked"
        except GmailError:
            return "local_disconnect_upstream_unconfirmed"


def status(uid: str, char_id: str) -> dict:
    from core.mail.gmail_poll import status as poll_status
    from core.mail.gmail_pending import summary as pending_summary
    record = load()
    same = bound(record, uid, char_id)
    email = record.get("email", "") if same else ""
    mailbox = hashlib.sha256(json.dumps((uid, char_id, email), ensure_ascii=False).encode()).hexdigest()
    pending = [
        {"message_id": f"<{key}@{email.rsplit('@', 1)[-1]}>", "at": item["at"]}
        for key, item in record.get("deliveries", {}).items()
        if same and email and item.get("state") == "pending" and item.get("mailbox") == mailbox
    ]
    try:
        cfg = oauth_config()
        configured = True
    except GmailError:
        configured = False
    error = (_last_error or record.get("token_error", "")) if same else ""
    authorization_error = record.get("authorization_error", {})
    if not isinstance(authorization_error, dict):
        raise GmailError("storage_error")
    if configured and (authorization_error.get("uid"), authorization_error.get("char_id"),
                       authorization_error.get("revision")) == (uid, char_id, cfg.revision):
        error = authorization_error.get("error", "")
    token_state = "waiting_authorization"
    if not configured:
        token_state = "not_configured"
    elif same and record.get("refresh"):
        token_state = "pending_verification" if record.get("verified_revision", cfg.revision) != cfg.revision else "valid"
    if error == "disabled_client":
        token_state = "client_disabled"
    elif error in TOKEN_ERRORS | {"reauthorization_required"}:
        token_state = "reauthorization_required"
    return {
        "configured": configured, "char_id": char_id,
        "connected": bool(same and record.get("refresh")),
        "bound_elsewhere": bool(not same and record.get("refresh")),
        "email": email,
        "token_state": token_state,
        "unknown_delivery_count": len(pending),
        "unknown_deliveries": sorted(pending, key=lambda item: item["at"], reverse=True)[:10],
        "scopes": record.get("scopes", []) if same else [],
        "error": error,
        **poll_status(record, uid, char_id),
        **pending_summary(record, uid, char_id, token_state=token_state),
    }
