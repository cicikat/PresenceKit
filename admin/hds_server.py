"""Dedicated LAN listener for Health Data Server's local PUT protocol."""

from fastapi import FastAPI, HTTPException, Request

from core import hds_local
from core.config_loader import get_config

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_bound_port: int | None = None


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
