"""Dedicated LAN listener for Health Data Server's local PUT protocol."""

from fastapi import FastAPI, HTTPException, Request
from threading import Lock
import time

from core import hds_local
from core.config_loader import get_config

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_bound_port: int | None = None
_request_lock = Lock()
_request_stats = {"total": 0, "methods": {}, "statuses": {}, "last_at": None,
                  "last_method": None, "last_status": None}


def request_stats() -> dict:
    """Process-local transport diagnostics; never store request bodies."""
    with _request_lock:
        return {**_request_stats, "methods": dict(_request_stats["methods"]),
                "statuses": dict(_request_stats["statuses"])}


@app.middleware("http")
async def count_requests(request: Request, call_next):
    response = await call_next(request)
    with _request_lock:
        _request_stats["total"] += 1
        method = request.method
        status = str(response.status_code)
        _request_stats["methods"][method] = _request_stats["methods"].get(method, 0) + 1
        _request_stats["statuses"][status] = _request_stats["statuses"].get(status, 0) + 1
        _request_stats["last_at"] = time.time()
        _request_stats["last_method"] = method
        _request_stats["last_status"] = response.status_code
    return response


def bound_port() -> int | None:
    return _bound_port


@app.put("/")
async def receive(request: Request):
    if not hds_local.enabled():
        raise HTTPException(503, "HDS local receiver disabled")
    source = request.client.host if request.client else ""
    if not hds_local.allowed_source(source):
        raise HTTPException(403, "source not allowed")
    try:
        declared_size = int(request.headers.get("content-length", "0") or 0)
    except ValueError:
        raise HTTPException(400, "invalid content length") from None
    if declared_size > 8192:
        raise HTTPException(413, "payload too large")
    parts = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > 8192:
            raise HTTPException(413, "payload too large")
        parts.append(chunk)
    body = b"".join(parts)
    try:
        payload = __import__("json").loads(body)
        uid = str(get_config().get("scheduler", {}).get("owner_id") or "")
        if not uid:
            raise HTTPException(503, "owner not configured")
        return hds_local.ingest(uid, payload)
    except (ValueError, TypeError):
        raise HTTPException(422, "invalid HDS heart-rate payload") from None


async def start():
    import uvicorn
    global _bound_port

    settings = hds_local.config()
    port = int(settings.get("port") or 3476)
    server = uvicorn.Server(uvicorn.Config(
        app=app,
        host=str(settings.get("host") or "0.0.0.0"),
        port=port,
        loop="asyncio",
        log_level="info",
        proxy_headers=False,
    ))
    _bound_port = port
    try:
        await server.serve()
    finally:
        _bound_port = None
