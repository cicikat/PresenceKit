"""Life record v1 API; owner comes from the authenticated single-owner deployment."""
import asyncio
import base64
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import io
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from admin.auth import require_scopes
from core import life_records as store
from core.config_loader import get_config

router = APIRouter()
IDENTIFIER = r'^[A-Za-z0-9_-]{1,128}$'


class Item(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = Field(max_length=500)
    quantity: str | None = None
    unit: str | None = Field(None, max_length=50)
    amount: str | None = None
    currency: str | None = Field(None, max_length=20)

    @field_validator('quantity', 'amount')
    @classmethod
    def decimal_string(cls, value):
        if value in (None, ''): return None
        if len(value) > 40 or not re.fullmatch(r'-?\d+(?:\.\d+)?', value): raise ValueError('decimal string required')
        try:
            if not Decimal(value).is_finite(): raise ValueError('finite decimal required')
        except InvalidOperation: raise ValueError('decimal string required') from None
        return value


class Record(BaseModel):
    model_config = ConfigDict(extra='ignore')
    category: Literal['diet', 'bill', 'cart']
    occurred_on: date
    captured_at: datetime
    title: str = Field('', max_length=1000)
    note: str = Field('', max_length=20000)
    items: list[Item] = Field(default_factory=list, max_length=300)
    user_edited_fields: list[Literal['title', 'note', 'category', 'occurred_on', 'items']] = Field(default_factory=list, max_length=5)

    @field_validator('captured_at')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None: raise ValueError('captured_at requires timezone')
        return value.astimezone(timezone.utc)


class Sync(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    owner_id: str
    operation_id: str = Field(pattern=IDENTIFIER)
    record_id: str = Field(pattern=IDENTIFIER)
    action: Literal['upsert', 'delete']
    base_revision: int = Field(ge=0)
    record: Record | None = None
    image_base64: str | None = Field(None, max_length=14_000_000)
    image_mime: Literal['image/jpeg', 'image/png', 'image/webp'] | None = None


def owner(owner_id):
    expected = str(get_config().get('scheduler', {}).get('owner_id', ''))
    if not expected: raise HTTPException(503, 'owner_not_configured')
    if str(owner_id) != expected: raise HTTPException(403, 'owner_mismatch')
    return expected


@router.get('/life-records/capabilities')
async def capabilities(owner_id: str, auth=Depends(require_scopes('life_records'))):
    owner(owner_id)
    return store.settings()


@router.post('/life-records/sync')
async def sync(body: Sync, auth=Depends(require_scopes('life_records'))):
    uid = owner(body.owner_id)
    if not store.settings()['enabled']: raise HTTPException(503, 'life_records_disabled')
    if body.action == 'upsert' and body.record is None: raise HTTPException(422, 'record_required')
    if body.action == 'delete' and body.image_base64 is not None: raise HTTPException(422, 'delete_has_image')
    image = None
    if body.image_base64 is not None:
        try:
            image = base64.b64decode(body.image_base64, validate=True)
            if len(image) > 10 * 1024 * 1024: raise HTTPException(413, 'image_too_large')
            from PIL import Image
            with Image.open(io.BytesIO(image)) as parsed:
                if parsed.width * parsed.height > 40_000_000: raise ValueError()
                if Image.MIME.get(parsed.format) != body.image_mime: raise ValueError()
                parsed.verify()
        except HTTPException: raise
        except Exception: raise HTTPException(422, 'invalid_image') from None
    try:
        return await asyncio.to_thread(store.sync, uid, auth.label, body.model_dump(mode='json'), image)
    except store.Conflict as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={'detail': 'revision_or_operation_conflict', 'current_record': exc.record})
    except ValueError as exc: raise HTTPException(422, str(exc)) from None


@router.get('/life-records')
async def records(owner_id: str, category: Literal['diet', 'bill', 'cart'] | None = None,
                  date_from: date | None = Query(None, alias='from'), date_to: date | None = Query(None, alias='to'),
                  q: str = Query('', max_length=200), cursor: str = Query('', max_length=2000),
                  limit: int = Query(50, ge=1, le=200), auth=Depends(require_scopes('life_records'))):
    uid = owner(owner_id)
    if date_from and date_to and date_from > date_to: raise HTTPException(422, 'invalid_date_range')
    try:
        return await asyncio.to_thread(store.listing, uid, category=category or '', date_from=str(date_from or ''), date_to=str(date_to or ''), q=q, cursor=cursor, limit=limit)
    except ValueError as exc: raise HTTPException(422, str(exc)) from None


@router.get('/life-records/observability')
async def observation(owner_id: str, auth=Depends(require_scopes('life_records'))):
    return await asyncio.to_thread(store.observe, owner(owner_id))


@router.get('/life-records/{record_id}')
async def record(record_id: str, owner_id: str, auth=Depends(require_scopes('life_records'))):
    value = await asyncio.to_thread(store.get, owner(owner_id), record_id)
    if value is None: raise HTTPException(404, 'record_not_found')
    return {'record': value}


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = False
    character_readable: bool = False
    background_sync: bool = True
    retain_images: bool = True


@router.get('/settings/life-records')
async def settings(auth=Depends(require_scopes('admin'))):
    uid = str(get_config().get('scheduler', {}).get('owner_id', ''))
    result = await asyncio.to_thread(store.observe, uid)
    try:
        from core.scheduler.loop import _active_char_id_or_none
        from core.context_continuity import observability
        char_id = _active_char_id_or_none()
        result['continuity'] = await asyncio.to_thread(observability, uid, char_id) if uid and char_id else {'unavailable': True}
    except Exception:
        result['continuity'] = {'unavailable': True}
    return result


@router.put('/settings/life-records')
async def save_settings(body: Settings, auth=Depends(require_scopes('admin'))):
    from core.config_loader import get_config_path, reload_config
    from admin.config_control import read_config_file, write_config_file
    path = get_config_path()
    cfg = read_config_file(path)
    cfg['life_records'] = body.model_dump()
    write_config_file(path, cfg)
    reload_config()
    return store.settings()


@router.post('/settings/life-records/{record_id}/retry')
async def retry(record_id: str, auth=Depends(require_scopes('admin'))):
    uid = str(get_config().get('scheduler', {}).get('owner_id', ''))
    try:
        return await asyncio.to_thread(store.retry_failed, uid, record_id)
    except ValueError as exc: raise HTTPException(422, str(exc)) from None
