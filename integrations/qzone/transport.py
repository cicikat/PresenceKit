"""Bounded, proxy-independent client for Gu-Heping/onebot-qzone REST."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import aiohttp

READ_ACTIONS = frozenset({"get_login_info", "check_cookie", "get_friend_feeds", "get_emotion_list", "get_comment_list"})
WRITE_ACTIONS = frozenset({"send_private_msg", "send_comment", "send_like", "unlike", "delete_msg"})


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("需要无凭据、无查询参数的 HTTP(S) 桥接根地址")
    # Accessing port also rejects malformed ports.
    _ = parsed.port
    return value.rstrip("/")


def bounded_data(value, *, depth=0):
    if depth > 8:
        return None
    if isinstance(value, dict):
        return {str(k): bounded_data(v, depth=depth + 1) for k, v in list(value.items())[:50]
                if not any(word in str(k).lower() for word in ("cookie", "token", "skey", "base64", "raw_html"))}
    if isinstance(value, list):
        return [bounded_data(v, depth=depth + 1) for v in value[:20]]
    if isinstance(value, str):
        return value[:2000]
    return value


async def request(base_url: str, token: str, action: str, params: dict, *, timeout: float = 30) -> dict:
    if action not in READ_ACTIONS | WRITE_ACTIONS | {"login_cookie"}:
        return {"ok": False, "code": "unsupported_action"}
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed("qzone_" + action)
    try:
        base_url = validate_url(base_url)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(base_url + "/" + action, json=params, headers=headers, allow_redirects=False) as response:
                if response.status != 200:
                    return {"ok": False, "code": "outcome_unknown" if action in WRITE_ACTIONS and response.status >= 500 else "http_error", "http_status": response.status}
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    if len(raw) > 524288:
                        return {"ok": False, "code": "outcome_unknown" if action in WRITE_ACTIONS else "response_too_large"}
                body = json.loads(raw)
        if not isinstance(body, dict) or body.get("status") != "ok" or body.get("retcode") != 0:
            return {"ok": False, "code": "bridge_rejected", "retcode": body.get("retcode") if isinstance(body, dict) else None}
        data = body.get("data")
        # Some upstream list fallbacks wrap a QZone failure in OneBot status=ok.
        if isinstance(data, dict) and (data.get("code", 0) not in (0, None) or data.get("ret", 0) not in (0, None)):
            return {"ok": False, "code": "qzone_rejected"}
        return {"ok": True, "data": bounded_data(data)}
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return {"ok": False, "code": "outcome_unknown" if action in WRITE_ACTIONS else "bridge_unreachable"}
    except (ValueError, TypeError, UnicodeError):
        return {"ok": False, "code": "outcome_unknown" if action in WRITE_ACTIONS else "invalid_response"}
