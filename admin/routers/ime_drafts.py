"""Passive, opt-in IME ingestion using the existing device Bearer identity."""
import asyncio
import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from admin.auth import require_scopes
from core.config_loader import get_config
from core import ime_drafts

router = APIRouter()
MAX_BYTES = 4 * 1024 * 1024


class EditEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    seq: int = Field(ge=1, le=2**63 - 1)
    at_ms: int = Field(ge=0, le=2**63 - 1)
    kind: Literal['insert', 'delete_backward', 'compose_delete', 'clear', 'restore']
    text: str = Field(default='', max_length=4096)
    outcome: Literal['applied', 'requested'] = 'requested'


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: int = Field(ge=1, le=2**63 - 1)
    created_at: int = Field(ge=0, le=2**63 - 1)
    updated_at: int = Field(ge=0, le=2**63 - 1)
    revision: int = Field(ge=1, le=2**63 - 1)
    app_package: str = Field(min_length=1, max_length=255)
    source: Literal['keyboard', 'voice', 'mixed']
    content: str = Field(max_length=1_000_000)
    edit_events: list[EditEvent] = Field(default_factory=list, max_length=256)

    @model_validator(mode='after')
    def timestamps(self):
        if self.updated_at < self.created_at or self.updated_at > int(time.time() * 1000) + 5 * 60 * 1000:
            raise ValueError('invalid timestamp')
        last = 0
        for event in self.edit_events:
            if event.seq <= last or event.seq > self.revision or not self.created_at <= event.at_ms <= self.updated_at:
                raise ValueError('invalid edit event order')
            last = event.seq
        return self


@router.post('/v1/ime/drafts', status_code=204, summary='接收 IME 草稿与编辑事件；后台按独立开关处理')
async def ingest(request: Request, auth=Depends(require_scopes('sensor.write'))):
    if not get_config().get('ime_ingest', {}).get('enabled', False):
        raise HTTPException(503, 'IME 接收尚未启用')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, 'IME 批次过大')
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) > 2000:
            raise ValueError('invalid batch')
        rows = TypeAdapter(list[Draft]).validate_python(payload)
    except (ValueError, ValidationError, UnicodeError):
        # Do not echo Pydantic's input values (draft text) in validation errors.
        raise HTTPException(422, 'IME 草稿格式错误：需要 v2 完整记录数组') from None
    try:
        await asyncio.to_thread(ime_drafts.receive, auth.label, [row.model_dump() for row in rows])
    except Exception:
        raise HTTPException(503, 'IME 批次未确认，请重试') from None
    return Response(status_code=204)


@router.get('/observability/ime-drafts', summary='查看 IME 接收状态与三小时内草稿')
async def observe(device_id: str = '', limit: int = Query(50, ge=1, le=200),
                  before: int | None = Query(None, ge=1), auth=Depends(require_scopes('admin'))):
    enabled = bool(get_config().get('ime_ingest', {}).get('enabled', False))
    try:
        rows = await asyncio.to_thread(ime_drafts.query, device_id=device_id, limit=limit, before=before)
        summary = await asyncio.to_thread(ime_drafts.summary, device_id=device_id)
    except Exception:
        raise HTTPException(503, 'IME 存储暂时不可读取') from None
    from core.ime_awareness import effective_state
    awareness = effective_state()
    return {'enabled': enabled, 'effective': enabled, 'mode': 'awareness' if awareness['enabled'] else 'receive_only',
            'awareness': awareness, 'analyses': await asyncio.to_thread(ime_drafts.analysis_query, device_id=device_id),
            'blocking_reason': '' if enabled else 'disabled', 'retention_hours': 3,
            'summary': summary, 'entries': rows, 'next_before': rows[-1]['seq'] if len(rows) == limit else None}
