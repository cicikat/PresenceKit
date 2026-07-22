"""Brief 56 visual ingress. Clients should only upload on meaningful scene changes."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from admin.auth import require_scopes
from core.safe_write import safe_append_jsonl
from core.sandbox import get_paths

logger = logging.getLogger(__name__)
router = APIRouter()

VISUAL_SOURCE_COOLDOWN_SECONDS = 5 * 60
VISUAL_TRACE_RETENTION_DAYS = 30
_last_accepted: dict[str, float] = {}


def _append_trace(*, source: str, observation=None, dropped: str | None = None) -> bool:
    row = {"ts": time.time(), "source": source, "dropped": dropped}
    if observation is not None:
        row.update(scene=observation.scene, activity=observation.activity,
                   confidence=observation.confidence, caption=observation.caption)
    return safe_append_jsonl(get_paths().visual_trace_log(), row)


def cleanup_visual_trace() -> int:
    path = get_paths().visual_trace_log()
    if not path.exists():
        return 0
    cutoff = time.time() - VISUAL_TRACE_RETENTION_DAYS * 86400
    try:
        kept = [line for line in path.read_text(encoding="utf-8").splitlines()
                if line and __import__("json").loads(line).get("ts", 0) >= cutoff]
        removed = sum(1 for _ in path.read_text(encoding="utf-8").splitlines()) - len(kept)
        if removed:
            from core.safe_write import safe_write_text
            safe_write_text(path, "\n".join(kept) + ("\n" if kept else ""))
        return removed
    except Exception as exc:
        logger.warning("[perception] visual trace cleanup failed: %s", exc)
        return 0


async def process_visual_image(image_bytes: bytes, source: str, context_hint: str = "") -> None:
    """Background-only VLM work. image_bytes is deliberately never written to disk."""
    from core.perception.vlm_client import describe_with_status
    observation, reason = await describe_with_status(image_bytes, context_hint)
    if observation is None:
        _append_trace(source=source, dropped="invalid" if reason == "invalid" else "vlm_error")
        logger.warning("[perception] visual observation dropped source=%s reason=%s", source, reason)
    elif observation.sensitive:
        _append_trace(source=source, dropped="sensitive")
    else:
        _append_trace(source=source, observation=observation)


@router.post("/perception/visual", status_code=202, summary="影子模式接收本地视觉观察")
async def ingest_visual(
    image: UploadFile = File(...),
    source: Literal["screen", "camera"] = Form(...),
    _auth=Depends(require_scopes("sensor.write")),
):
    from core.perception.vlm_client import get_visual_perception_config
    if not get_visual_perception_config().get("enabled", False):
        return {"accepted": True, "processing": False}
    now = time.monotonic()
    last_accepted = _last_accepted.get(source)
    if last_accepted is not None and now - last_accepted < VISUAL_SOURCE_COOLDOWN_SECONDS:
        _append_trace(source=source, dropped="cooldown")
        return {"accepted": True, "processing": False}
    image_bytes = await image.read()
    _last_accepted[source] = now
    asyncio.create_task(process_visual_image(image_bytes, source))
    return {"accepted": True, "processing": True}


@router.get("/perception/visual/config", summary="读取视觉观测生产者预检状态")
async def get_visual_producer_config(
    _auth=Depends(require_scopes("sensor.write")),
):
    """Return only the producer-safe gate; never expose VLM connection details."""
    from core.perception.vlm_client import get_visual_perception_config

    return {
        "enabled": bool(get_visual_perception_config().get("enabled", False)),
        "cooldown_seconds": VISUAL_SOURCE_COOLDOWN_SECONDS,
    }


@router.get("/perception/visual-trace", summary="读取视觉 shadow trace")
async def get_visual_trace(
    date: str = "",
    limit: int = Query(100, ge=1, le=500),
    before: float | None = None,
    _auth=Depends(require_scopes("state.read")),
):
    """Diagnostic-only view; missing trace files are a normal empty result."""
    import datetime as _dt
    import json
    if date:
        try:
            _dt.date.fromisoformat(date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date 须为 YYYY-MM-DD") from exc
    path = get_paths().visual_trace_log()
    if not path.exists():
        return {"date": date or None, "entries": [], "count": 0}
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("ts"), (int, float)):
            continue
        if date and _dt.datetime.fromtimestamp(row["ts"]).date().isoformat() != date:
            continue
        if before is not None and row["ts"] >= before:
            continue
        entries.append(row)
    entries = entries[-limit:]
    return {"date": date or None, "entries": entries, "count": len(entries)}
