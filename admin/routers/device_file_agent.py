"""Bounded, non-cacheable desktop file transport."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from admin.auth import require_scopes
from admin.routers.screen_observation import identity
from core.agent_runtime import device_file_agent as service

router = APIRouter()
_desktop = require_scopes("ws.desktop", "sensor.write")


async def _body(request, model):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > service.MAX_BYTES:
            raise HTTPException(413, "device_file_body_too_large")
        data.extend(chunk)
    try:
        return model.model_validate_json(data)
    except ValidationError:
        raise HTTPException(422, "invalid_device_file_packet") from None


def _reply(value):
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.post("/device-file-agent/poll")
async def poll(request: Request, auth=Depends(_desktop)):
    producer = identity(request, auth, "desktop")
    return _reply(service.poll(producer, await _body(request, service.Poll)))


@router.post("/device-file-agent/result")
async def result(request: Request, auth=Depends(_desktop)):
    producer = identity(request, auth, "desktop")
    if not service.accept(producer, await _body(request, service.Result)):
        raise HTTPException(409, "stale_device_file_receipt")
    return _reply({"accepted": True})


@router.get("/observability/device-file-agent")
async def status(auth=Depends(require_scopes("state.read"))):
    from core.agent_runtime import browser_workbench
    return _reply({**service.snapshot(), "browser_screenshots": browser_workbench.snapshot(service.principal())})


@router.get("/observability/device-file-agent/browser/{task_id}/{sha256}")
async def browser_image(task_id: str, sha256: str, auth=Depends(require_scopes("state.read"))):
    from core.agent_runtime import browser_workbench
    item = browser_workbench.image(service.principal(), task_id, sha256)
    if not item:
        raise HTTPException(404, "screenshot_unavailable")
    return Response(item["raw"], media_type="image/jpeg", headers={"Cache-Control": "no-store",
                    "Content-Security-Policy": "default-src 'none'", "X-Content-Type-Options": "nosniff"})
