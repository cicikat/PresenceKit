"""Private mailbox controls and the two browser-only OAuth handoff routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from admin.auth import require_scopes, resolve_token
from admin.routers.character import _active_character_id
from core.mail import gmail

router = APIRouter()
admin = require_scopes("admin")
HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


class Start(BaseModel):
    email: str = Field(max_length=254)


def _principal() -> tuple[str, str]:
    return gmail.owner(), _active_character_id()


def _admin_valid(token: str) -> bool:
    info = resolve_token(token)
    return bool(info and "admin" in info.scopes)


def _problem(exc: gmail.GmailError) -> HTTPException:
    return HTTPException(409, str(exc))


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

    text = "Google 邮箱已连接。请回到邮件连接页面刷新状态。" if ok else (
        f"授权未完成（{escape(code)}）。请关闭此页并在邮件连接页面重新发起。"
    )
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
                             *_principal(), _admin_valid)
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
