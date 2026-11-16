"""Dedicated finance scope, single-owner identity derived on server."""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from admin.auth import require_scopes
from core.config_loader import get_config
from core import finance as store

router = APIRouter()
ID = r'^[A-Za-z0-9_-]{1,128}$'


def owner():
    uid = str(get_config().get('scheduler', {}).get('owner_id', ''))
    if not uid:
        raise HTTPException(503, 'owner_not_configured')
    return uid


def enabled():
    if not store.settings()['effective']:
        raise HTTPException(503, 'finance_disabled')


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    candidate_id: str = Field(pattern=ID)
    source_app: Literal['com.tencent.mm', 'com.eg.android.AlipayGphone', 'com.icbc', 'com.ccb.start', 'cmb.pb', 'manual']
    source_event_key: str = Field(pattern=r'^[a-f0-9]{64}$')
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    occurred_at: int = Field(ge=946684800000, le=4102444800000)
    observed_at: int = Field(ge=946684800000, le=4102444800000)
    timezone_offset_minutes: int = Field(ge=-840, le=840)
    direction: Literal['expense', 'income', 'refund', 'transfer', 'unknown']
    amount_minor: int | None = Field(None, gt=0, le=99999999999)
    currency: Literal['CNY'] | None = None
    merchant: str | None = Field(None, max_length=120)
    category: str | None = Field(None, max_length=80)
    confidence: Literal['low', 'medium', 'high', 'user']
    reason_codes: list[str] = Field(max_length=20)
    status: Literal['candidate', 'confirmed', 'ignored', 'duplicate', 'deleted'] = 'candidate'
    linked_record_id: str | None = Field(None, pattern=ID)
    linked_transaction_id: str | None = Field(None, pattern=ID)
    revision: int = Field(0, ge=0)

    @model_validator(mode='after')
    def complete_confirmation(self):
        if any(len(v) > 80 or not v.replace('_', '').isalnum() for v in self.reason_codes):
            raise ValueError('invalid_reason_codes')
        if self.status == 'confirmed' and (self.amount_minor is None or self.currency is None or self.direction == 'unknown'):
            raise ValueError('confirmation_incomplete')
        return self


class Operation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    candidate_id: str = Field(pattern=ID)
    operation_id: str = Field(pattern=ID)
    base_revision: int = Field(ge=0)
    record: Record

    @model_validator(mode='after')
    def matching_id(self):
        if self.candidate_id != self.record.candidate_id:
            raise ValueError('candidate_mismatch')
        return self


@router.get('/finance/capabilities')
async def capabilities(auth=Depends(require_scopes('finance_ingest'))):
    owner()
    return store.settings()


@router.post('/finance/candidates/sync')
async def sync(body: Operation, auth=Depends(require_scopes('finance_ingest'))):
    uid = owner(); enabled()
    try:
        return await asyncio.to_thread(store.sync, uid, body.model_dump())
    except store.Conflict as exc:
        return JSONResponse(status_code=409, content={'code': 'finance_conflict', 'current_record': exc.record})


@router.get('/finance/candidates')
async def listing(cursor: str = Query('', max_length=128), limit: int = Query(50, ge=1, le=200),
                  auth=Depends(require_scopes('finance_ingest'))):
    uid = owner(); enabled()
    return await asyncio.to_thread(store.listing, uid, cursor=cursor, limit=limit)


@router.get('/finance/candidates/{candidate_id}')
async def detail(candidate_id: str, auth=Depends(require_scopes('finance_ingest'))):
    uid = owner(); enabled()
    record = await asyncio.to_thread(store.get, uid, candidate_id)
    if not record:
        raise HTTPException(404, 'candidate_not_found')
    return {'record': record, 'history': await asyncio.to_thread(store.history, uid, candidate_id)}


@router.get('/observability/finance')
async def observation(auth=Depends(require_scopes('state.read'))):
    return await asyncio.to_thread(store.observe, owner())


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = False
    character_readable: bool = False


@router.get('/settings/finance')
async def settings(auth=Depends(require_scopes('admin'))):
    return await asyncio.to_thread(store.observe, owner())


@router.put('/settings/finance')
async def save_settings(body: Settings, auth=Depends(require_scopes('admin'))):
    from core.config_loader import get_config_path, reload_config
    from admin.config_control import read_config_file, write_config_file
    path = get_config_path()
    cfg = read_config_file(path)
    cfg['finance_ingest'] = body.model_dump()
    write_config_file(path, cfg)
    reload_config()
    return store.settings()
