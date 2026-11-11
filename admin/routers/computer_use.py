"""Desktop-only polling endpoints. Desktop grants remain local and time-limited."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from admin.auth import require_scopes
from admin.routers.screen_observation import identity
from core.agent_runtime import computer_use as service
from core.agent_runtime.computer_use_contract import Poll, Result

router = APIRouter(prefix="/computer-use", tags=["Computer Use"])
_desktop = require_scopes("ws.desktop", "sensor.write")


@router.post("/poll")
async def poll(body: Poll, request: Request, response: Response, auth=Depends(_desktop)):
    response.headers["Cache-Control"] = "no-store"
    return service.poll(identity(request, auth, "desktop"), body)


@router.post("/result")
async def result(body: Result, request: Request, auth=Depends(_desktop)):
    if not service.accept(identity(request, auth, "desktop"), body):
        raise HTTPException(409, "stale_step")
    return {"accepted": True}


@router.get("/status")
async def status(auth=Depends(require_scopes("state.read"))):
    return service.snapshot()
