"""Private mailbox controls and the two browser-only OAuth handoff routes."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, SecretStr

from admin.auth import require_scopes, resolve_token
from admin.routers.character import _active_character_id
from core.mail import gmail

admin = require_scopes("admin")
HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Pydantic's default response can echo a rejected secret as `input`.
                return JSONResponse({"detail": "invalid_request"}, status_code=422, headers=HEADERS)

        return private_handler


router = APIRouter(route_class=PrivateRoute)


class Sync(BaseModel):
    enabled: bool
    interval_seconds: int = Field(ge=60, le=3600)


@router.put("/settings/gmail/sync")
async def configure_sync(body: Sync, auth=Depends(admin)):
    from core.mail.gmail_poll import configure

    try:
        uid, char_id = _principal()
        await configure(uid, char_id, body.enabled, body.interval_seconds)
        return gmail.status(uid, char_id)
    except gmail.GmailError as exc:
        raise _problem(exc) from None


class Start(BaseModel):
    email: str = Field(max_length=254)
    model_processing_consent: Literal[True]


class OAuthConfig(BaseModel):
    client_id: str = Field(max_length=256)
    client_secret: SecretStr | None = Field(default=None, max_length=1024)
    redirect_uri: str = Field(max_length=2048)


@router.get("/settings/gmail/oauth/config")
async def oauth_config(response: Response, auth=Depends(admin)):
    response.headers.update(HEADERS)
    try:
        return gmail.config_status()
    except gmail.GmailError as exc:
        raise _problem(exc) from None


@router.put("/settings/gmail/oauth/config")
async def save_oauth_config(body: OAuthConfig, response: Response, auth=Depends(admin)):
    from admin.audit import log_event

    response.headers.update(HEADERS)
    try:
        result = await gmail.configure_client(body.client_id,
                    body.client_secret.get_secret_value() if body.client_secret else None, body.redirect_uri)
        log_event("gmail_oauth_config_updated", label=auth.label, path="/settings/gmail/oauth/config")
        return result
    except gmail.GmailError as exc:
        raise _problem(exc) from None


@router.post("/settings/gmail/oauth/config/validate")
async def validate_oauth_config(body: OAuthConfig, response: Response, auth=Depends(admin)):
    response.headers.update(HEADERS)
    try:
        gmail.validate_config(body.client_id,
                    body.client_secret.get_secret_value() if body.client_secret else None, body.redirect_uri)
        return {"valid": True, "authorization_verified": False}
    except gmail.GmailError as exc:
        raise _problem(exc) from None


def _principal() -> tuple[str, str]:
    return gmail.owner(), _active_character_id()


def _admin_valid(token: str) -> bool:
    info = resolve_token(token)
    return bool(info and "admin" in info.scopes)


def _problem(exc: gmail.GmailError) -> HTTPException:
    return HTTPException(409, str(exc), headers=HEADERS)


@router.get("/settings/gmail")
async def status(auth=Depends(admin)):
    try:
        return gmail.status(*_principal())
    except gmail.GmailError as exc:
        raise _problem(exc) from None


@router.post("/settings/gmail/oauth/start")
async def start(body: Start, request: Request, auth=Depends(admin)):
    uid, char_id = _principal()
    if not uid or not char_id:
        raise HTTPException(409, "owner_or_character_missing")
    try:
        email = gmail.normalize_address(body.email or "").casefold()
        url = gmail.start(uid, char_id, email, request.headers["authorization"].split(None, 1)[1])
        return {"authorization_url": url}
    except gmail.GmailError as exc:
        raise _problem(exc) from None


def _result(ok: bool, code: str = "") -> HTMLResponse:
    from html import escape

    if ok:
        text = "Google 邮箱已连接，请回邮件连接页面刷新。Gmail connected; return to Mail connections and refresh."
    elif code == "disabled_client":
        text = ("Google OAuth 客户端已被禁用，请在 Google Cloud 恢复或更换客户端配置。"
                "Google OAuth client disabled. Restore it in Google Cloud or replace the client configuration.")
    else:
        text = (f"授权未完成（{escape(code)}），请回邮件连接页面查看状态。"
                "Authorization incomplete; return to Mail connections to check its status.")
    return HTMLResponse(f'<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                        f'<title>PresenceKit Gmail</title><p>{text}</p></html>',
                        status_code=200 if ok else 400, headers=HEADERS)


@router.get(gmail.AUTHORIZE, include_in_schema=False)
async def authorize(request: Request):
    try:
        url, browser = gmail.authorize(request.query_params.get("ticket", "")[:128])
        response = RedirectResponse(url, status_code=303, headers=HEADERS)
        response.set_cookie(gmail.COOKIE, browser, max_age=600, path=gmail.CALLBACK,
                            secure=True, httponly=True, samesite="lax")
        return response
    except gmail.GmailError as exc:
        return _result(False, str(exc))


@router.get(gmail.CALLBACK, include_in_schema=False)
async def callback(request: Request):
    try:
        await gmail.complete(request.query_params.get("state", "")[:128],
                             request.cookies.get(gmail.COOKIE, "")[:128],
                             request.query_params.get("code", "")[:4096],
                             *_principal(), _admin_valid,
                             error=request.query_params.get("error", "")[:128])
        response = _result(True)
        response.delete_cookie(gmail.COOKIE, path=gmail.CALLBACK, secure=True, httponly=True)
        return response
    except gmail.GmailError as exc:
        return _result(False, str(exc))


@router.post("/settings/gmail/test")
async def test(auth=Depends(admin)):
    try:
        uid, char_id = _principal()
        result = await gmail.api(uid, char_id, "GET", "profile")
        if str(result.get("emailAddress", "")).casefold() != gmail.status(uid, char_id)["email"]:
            raise gmail.GmailError("google_account_mismatch")
        return {"ok": True, "email": gmail.status(uid, char_id)["email"]}
    except gmail.GmailError as exc:
        raise _problem(exc) from None


@router.post("/settings/gmail/disconnect")
async def disconnect(auth=Depends(admin)):
    try:
        return {"result": await gmail.disconnect(*_principal())}
    except gmail.GmailError as exc:
        raise _problem(exc) from None
