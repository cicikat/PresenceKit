"""Redacted Garden integration status plus a guarded manual test hint."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from admin import token_registry
from admin.auth import require_scopes
from core.config_loader import get_config

router = APIRouter()

_GARDEN_PROVIDER = "galatea_garden"
_INTEGRATION_TOKEN_LABEL = "garden-wake"
_BRIDGE_STALE_AFTER_SECONDS = 5 * 60
_MANUAL_TEST_REASON = "manual_test"
_MANUAL_TEST_MESSAGE = "这是管理面板发出的低信任 Garden 测试提示；请仅按既有 Garden 边界处理。"


def _record_is_current(record: token_registry.TokenRecord) -> bool:
    if record.disabled:
        return False
    if not record.expires_at:
        return True
    try:
        expires_at = datetime.fromisoformat(record.expires_at)
    except ValueError:
        return False
    now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
    return expires_at > now


def _integration_token_configured() -> bool:
    return any(
        record.label == _INTEGRATION_TOKEN_LABEL
        and "integration.write" in record.scopes
        and _record_is_current(record)
        for record in token_registry.list_records()
    )


def _active_scope() -> tuple[str, str]:
    from core.scheduler.loop import _active_char_id_or_none

    uid = str(get_config().get("scheduler", {}).get("owner_id") or "").strip()
    return uid, str(_active_char_id_or_none() or "").strip()


def _garden_entry(uid: str, char_id: str) -> dict[str, Any]:
    from core.wake_bridge import query_state

    entries = query_state(uid=uid, char_id=char_id, provider=_GARDEN_PROVIDER)
    return entries[0] if entries else {}


def _scheduler_running() -> bool:
    from core.scheduler import loop

    task = getattr(loop, "_scheduler_task", None)
    return bool(task is not None and not task.done())


def _bridge_status(*, enabled: bool, last_received_at: float | None, next_attempt_at: float | None) -> str:
    if not enabled:
        return "unknown"
    now = time.time()
    if next_attempt_at and next_attempt_at > now:
        return "backoff"
    if not last_received_at:
        return "unknown"
    return "connected" if now - last_received_at <= _BRIDGE_STALE_AFTER_SECONDS else "stale"


def garden_status() -> dict[str, Any]:
    """Return aggregates only; never return token values, IDs, messages, cursors, or hashes."""
    uid, char_id = _active_scope()
    entry = _garden_entry(uid, char_id) if uid and char_id else {}
    enabled = _integration_token_configured()
    last_received_at = entry.get("last_received_at")
    next_attempt_at = entry.get("next_attempt_at")
    return {
        "garden": {
            "enabled": enabled,
            "bridge_status": _bridge_status(
                enabled=enabled,
                last_received_at=last_received_at,
                next_attempt_at=next_attempt_at,
            ),
            # The upstream bridge owns this secret. PresenceKit never stores it;
            # this merely reports whether it exists in this process environment.
            "machine_token": "configured" if os.environ.get("GARDEN_MACHINE_TOKEN") else "missing",
            "integration_token": "configured" if enabled else "missing",
            "uid": uid,
            "char_id": char_id,
            "last_wake_received": last_received_at,
            "last_successful_drain": entry.get("last_success_at"),
            "last_reason": entry.get("last_reason"),
            "last_disposition": entry.get("last_disposition"),
            "last_attempt_at": entry.get("last_attempt_at"),
            "last_next_attempt_at": entry.get("last_next_attempt_at"),
            "time_sensitive_lane": bool(entry.get("last_time_sensitive_lane")),
            "pending_count": int(entry.get("pending_count") or 0),
            "processing_count": int(entry.get("processing_count") or 0),
            "expired_count": int(entry.get("expired_count") or 0),
            "consecutive_failures": int(entry.get("consecutive_failures") or 0),
            "current_backoff_until": next_attempt_at,
            "scheduler_running": _scheduler_running(),
        }
    }


@router.get("/integrations/garden/status", summary="Read redacted Galatea Garden integration status")
async def get_garden_status(_auth=Depends(require_scopes("state.read"))):
    return garden_status()


@router.post("/integrations/garden/test-wake", summary="Submit one gated Garden test hint")
async def send_garden_test_wake(_auth=Depends(require_scopes("admin"))):
    """Persist a normal Garden hint; never run drain, LLM, or turn sink inline."""
    if not _integration_token_configured():
        return {"status": "rejected", "reason": "integration_token_missing"}
    uid, char_id = _active_scope()
    if not uid or not char_id:
        raise HTTPException(status_code=503, detail="Garden test wake requires configured owner and active character")

    from core.wake_bridge import WakeBridge

    return (await WakeBridge().submit_garden_mapping({
        "provider": _GARDEN_PROVIDER,
        "reason": _MANUAL_TEST_REASON,
        "message": _MANUAL_TEST_MESSAGE,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "uid": uid,
        "char_id": char_id,
    })).to_dict()
