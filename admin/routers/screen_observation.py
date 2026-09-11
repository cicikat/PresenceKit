"""Device-bound polling transport for an explicitly requested fresh screenshot."""
import base64
import hashlib
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from core.perception import screen_observation as service
from admin.auth import require_scopes

router = APIRouter()


class Poll(BaseModel):
    device: Literal["desktop", "mobile"]
    available: bool
    idle_seconds: float = Field(ge=0, le=864000)


class Result(BaseModel):
    device: Literal["desktop", "mobile"]
    request_id: str = Field(min_length=32, max_length=32)
    status: Literal["ok", "locked", "disabled", "unsupported", "sensitive", "failed"]
    image_base64: str = Field(default="", max_length=4_000_000)


def identity(request: Request, auth, device: str) -> str:
    if auth.profile != device and "admin" not in auth.scopes:
        raise HTTPException(403, "device profile mismatch")
    return hashlib.sha256(request.headers.get("authorization", "").encode()).hexdigest()


@router.post("/perception/screen/poll")
async def poll(body: Poll, request: Request, auth=Depends(require_scopes("sensor.write"))):
    return service.poll(body.device, identity(request, auth, body.device), body.available, body.idle_seconds)


@router.post("/perception/screen/result")
async def result(body: Result, request: Request, auth=Depends(require_scopes("sensor.write"))):
    producer = identity(request, auth, body.device)
    image = None
    if body.status == "ok":
        try:
            from PIL import Image
            image = base64.b64decode(body.image_base64, validate=True)
            with Image.open(io.BytesIO(image)) as frame:
                if frame.format != "JPEG" or frame.width > 1920 or frame.height > 1920:
                    raise ValueError("invalid image")
                frame.verify()
        except Exception:
            raise HTTPException(422, "invalid screenshot") from None
    if not service.accept(body.device, producer, body.request_id, image, body.status):
        raise HTTPException(409, "expired or mismatched request")
    return {"accepted": True}


@router.get("/perception/screen/status")
async def status(auth=Depends(require_scopes("state.read"))):
    return service.state()
