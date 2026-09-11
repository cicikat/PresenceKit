"""Best-effort remote model catalogue; never modifies model configuration."""
from urllib.parse import urlsplit, urlunsplit

import httpx


def catalogue_url(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("请填写有效的 HTTP(S) 模型地址")
    if parsed.query or parsed.fragment:
        raise ValueError("模型地址不能包含 query 或 fragment")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages", "/models"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    if not path:
        path = "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/models", "", ""))


async def discover(base_url: str, api_key: str, protocol: str, auth_mode: str) -> dict:
    from core.model_registry import _get_proxy_url, _make_http_client
    url = catalogue_url(base_url)
    headers = {}
    if protocol == "anthropic_messages":
        headers["anthropic-version"] = "2023-06-01"
    if api_key:
        if protocol == "anthropic_messages" and auth_mode == "x_api_key":
            headers["x-api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with _make_http_client(_get_proxy_url(), timeout_s=12) as client:
            response = await client.get(url, headers=headers, follow_redirects=False)
        code = response.status_code
        if code != 200:
            status = "unauthorized" if code in (401, 403) else "unsupported" if code in (404, 405, 501) else "http_error"
            return {"status": status, "models": [], "http_status": code}
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return {"status": "invalid_response", "models": []}
        models = sorted({row["id"].strip() for row in rows if isinstance(row, dict)
                         and isinstance(row.get("id"), str) and row["id"].strip()})
        return {"status": "ok" if models else "empty", "models": models,
                "has_more": bool(payload.get("has_more", False)) if isinstance(payload, dict) else False}
    except httpx.TimeoutException:
        return {"status": "timeout", "models": []}
    except (ValueError, TypeError):
        return {"status": "invalid_response", "models": []}
    except httpx.HTTPError:
        return {"status": "connection_error", "models": []}
