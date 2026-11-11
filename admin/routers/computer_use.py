"""Desktop polling and scoped JPEG grounding."""
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import Field
from starlette.requests import ClientDisconnect

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from admin.routers.screen_observation import identity
from core import config_loader
from core.agent_runtime import computer_use as service
from core.agent_runtime import computer_use_vision as vision
from core.agent_runtime.computer_use_contract import Poll, Result, Strict, VisionRequest

_no_store = {"Cache-Control": "no-store"}


class _NoStoreRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def with_no_store(request: Request) -> Response:
            try:
                response = await handle(request)
            except HTTPException as exc:
                if self.name == "locate":
                    return JSONResponse({"error": "vision_device_denied"}, status_code=exc.status_code,
                                        headers={**(exc.headers or {}), **_no_store})
                exc.headers = {**(exc.headers or {}), **_no_store}
                raise
            response.headers.update(_no_store)
            return response

        return with_no_store


router = APIRouter(prefix="/computer-use", tags=["Computer Use"], route_class=_NoStoreRoute)
_desktop = require_scopes("ws.desktop", "sensor.write")


class VisionSettings(Strict):
    local_vision_timeout_s: int = Field(ge=1, le=service.VISION_TIMEOUT_DEFAULTS["local"])
    cloud_vision_timeout_s: int = Field(ge=1, le=service.VISION_TIMEOUT_DEFAULTS["cloud"])


@router.post("/poll")
async def poll(body: Poll, request: Request, auth=Depends(_desktop)):
    return service.poll(identity(request, auth, "desktop"), body)


@router.post("/result")
async def result(body: Result, request: Request, auth=Depends(_desktop)):
    if not service.accept(identity(request, auth, "desktop"), body):
        raise HTTPException(409, "stale_step")
    return {"accepted": True}


@router.put("/vision/settings")
async def update_vision_settings(body: VisionSettings, auth=Depends(require_scopes("admin"))):
    path = config_loader.get_config_path()
    config = read_config_file(path)
    block = config.get("computer_use")
    config["computer_use"] = {**(block if isinstance(block, dict) else {}), **body.model_dump()}
    write_config_file(path, config)
    config_loader.reload_config()
    return service.vision_timeouts()


def _vision_error(code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


async def _read_jpeg(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > vision.MAX_JPEG:
            raise vision.VisionError("vision_image_too_large", 413)
        data.extend(chunk)
    return bytes(data)


@router.post("/vision/locate", responses={
    200: {"description": "Found normalized coordinates or uncertain"},
    409: {"description": "Missing, revoked, expired or already claimed delivery target"},
}, openapi_extra={"requestBody": {"required": True, "content": {
    "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
}}, "parameters": [{"in": "header", "name": "X-Computer-Use-Lease", "required": True,
                    "schema": {"type": "string", "maxLength": 768},
                    "description": "JSON VisionRequest containing the delivery reference, frame version and target index"}]})
async def locate(request: Request, auth=Depends(_desktop)):
    try:
        device_identity = identity(request, auth, "desktop")
    except HTTPException:
        return _vision_error("vision_device_denied", 403)
    try:
        lease = request.headers.get("X-Computer-Use-Lease", "")
        if len(lease) > 768:
            raise ValueError
        body = VisionRequest.model_validate_json(lease)
    except ValueError:
        return _vision_error("vision_invalid_lease", 422)
    if request.headers.get("Content-Type") != "image/jpeg":
        return _vision_error("vision_invalid_image", 415)
    match = service.vision_lease(device_identity, body)
    if match is None:
        return _vision_error("vision_stale_step", 409)
    run, delivery, description = match
    # Claim before reading any bytes so concurrent replays cannot buffer images.
    delivery.vision_claims.add(body.target_index)
    # Leave time for the client to receive the answer, recheck the frame and send its receipt.
    deadline = min(delivery.expires - 2, run.device.expires, run.deadline)
    try:
        budget = deadline - time.monotonic()
        if budget <= 0:
            return _vision_error("vision_stale_step", 409)
        jpeg = await asyncio.wait_for(_read_jpeg(request), timeout=min(budget, 5))
        vision.validate_jpeg(jpeg, delivery.frame_size)
    except vision.VisionError as exc:
        return _vision_error(exc.code, exc.status)
    except (TimeoutError, asyncio.TimeoutError):
        return _vision_error("vision_timeout", 504)
    except ClientDisconnect:
        return _vision_error("vision_stale_step", 409)

    timeout = min(delivery.vision_timeout_ms / 1000, deadline - time.monotonic())
    if timeout <= 0 or not service.vision_active(run, delivery):
        return _vision_error("vision_stale_step", 409)
    preset = run.device.cloud_route["config"]
    started = time.monotonic()
    call = asyncio.create_task(vision.locate(preset, jpeg, description, timeout))
    outcome = "vision_stale_step"
    try:
        while service.vision_active(run, delivery):
            if await request.is_disconnected():
                break
            done, _ = await asyncio.wait((call,), timeout=0.1)
            if done:
                if not service.vision_active(run, delivery):
                    break
                result = call.result()
                outcome = "vision_uncertain" if result["status"] == "uncertain" else "found"
                return JSONResponse(result)
        return _vision_error(outcome, 409)
    except vision.VisionError as exc:
        outcome = exc.code
        return _vision_error(exc.code, exc.status)
    finally:
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        service.record_vision(run, outcome, round((time.monotonic() - started) * 1000), delivery.frame_size)


@router.get("/status")
async def status(auth=Depends(require_scopes("state.read"))):
    return service.snapshot()
