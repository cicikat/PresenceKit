"""Authenticated character-owned period-date input."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from core.config_loader import get_config
from core.memory import health_state
from admin.routers.users import _resolve_char_id

router = APIRouter()


class PeriodDateBody(BaseModel):
    last_period_date: str


def _owner_id() -> str:
    uid = str(get_config().get("scheduler", {}).get("owner_id") or "").strip()
    if not uid:
        raise HTTPException(status_code=503, detail="period input owner is not configured")
    return uid


def _response(uid: str, char_id: str) -> dict:
    info = health_state.get_period_info(uid, char_id=char_id)
    last_period_date = info["last_period_date"]
    return {
        "last_period_date": last_period_date,
        "period_reminder_input_ready": last_period_date is not None,
    }


@router.get("/period", summary="Get the current period reminder input")
async def get_period_date(char_id: str | None = None, auth=Depends(require_scopes("state.read"))):
    return _response(_owner_id(), _resolve_char_id(char_id))


@router.put("/period", summary="Set the current period reminder input")
async def put_period_date(body: PeriodDateBody, char_id: str | None = None, auth=Depends(require_scopes("sensor.write"))):
    uid = _owner_id()
    char_id = _resolve_char_id(char_id)
    try:
        health_state.set_period_date(uid, body.last_period_date, char_id=char_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="last_period_date must be YYYY-MM-DD") from exc
    return _response(uid, char_id)


@router.delete("/period", summary="Clear the current period reminder input")
async def delete_period_date(char_id: str | None = None, auth=Depends(require_scopes("sensor.write"))):
    uid = _owner_id()
    char_id = _resolve_char_id(char_id)
    health_state.clear_period_date(uid, char_id=char_id)
    return _response(uid, char_id)
