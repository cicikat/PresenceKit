"""Dedicated LAN listener for Health Data Server's local PUT protocol."""

from fastapi import FastAPI, HTTPException, Request

from core import hds_local
from core.config_loader import get_config

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


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
    body = await request.body()
    if len(body) > 8192:
        raise HTTPException(413, "payload too large")
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

    settings = hds_local.config()
    server = uvicorn.Server(uvicorn.Config(
        app=app,
        host=str(settings.get("host") or "0.0.0.0"),
        port=int(settings.get("port") or 3476),
        loop="asyncio",
        log_level="info",
        proxy_headers=False,
    ))
    await server.serve()
