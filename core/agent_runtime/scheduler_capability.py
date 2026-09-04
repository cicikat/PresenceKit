"""Reality-only durable scheduling capability (Brief 235).

Schedule payloads are kept in the scheduler capability store; Task Manager owns
the lifecycle/leases and is the only source of task status.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.agent_runtime.models import CausationRef, TaskPrincipal
from core.agent_runtime import task_manager
from core.data_paths import DEFAULT_CHAR_ID, safe_user_id
from core.sandbox import get_paths
from core.safe_write import safe_write_json


def _path(principal: TaskPrincipal) -> Path:
    safe_user_id(principal.uid); safe_user_id(principal.char_id)
    p = get_paths().agent_runtime_reality_root() / "schedules" / principal.uid / f"{principal.char_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(p: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def create_schedule(principal: TaskPrincipal, *, content: str, due_at: float,
                    recurrence_seconds: int | None = None, ttl_seconds: int = 365*86400,
                    idempotency_key: str | None = None, causation_ref: CausationRef | None = None) -> dict[str, Any]:
    if principal.realm != "reality" or not isinstance(content, str) or not content.strip():
        raise ValueError("invalid_schedule")
    if recurrence_seconds is not None and (not isinstance(recurrence_seconds, int) or recurrence_seconds < 60):
        raise ValueError("invalid_recurrence")
    idem = idempotency_key or uuid.uuid4().hex
    receipt, created = task_manager.create_task(principal, capability="scheduler", source="user_schedule",
        idempotency_key=idem, ttl_seconds=ttl_seconds, causation_ref=causation_ref)
    p = _path(principal); rows = _load(p)
    existing = next((r for r in rows if r["task_id"] == receipt["task_id"]), None)
    if existing:
        return {**receipt, "schedule_id": existing["schedule_id"], "due_at": existing["due_at"], "recurrence_seconds": existing.get("recurrence_seconds"), "created": False}
    row = {"schedule_id": uuid.uuid4().hex, "task_id": receipt["task_id"], "content": content.strip(), "due_at": float(due_at), "recurrence_seconds": recurrence_seconds, "canceled": False, "delivered_count": 0}
    rows.append(row); safe_write_json(p, rows)
    return {**receipt, "schedule_id": row["schedule_id"], "due_at": row["due_at"], "recurrence_seconds": recurrence_seconds, "created": created}


def due_schedules(principal: TaskPrincipal, *, now: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else float(now)
    return [r for r in _load(_path(principal)) if not r.get("canceled") and r.get("due_at", 0) <= now]


def mark_delivered(principal: TaskPrincipal, schedule_id: str, *, now: float | None = None) -> bool:
    rows = _load(_path(principal)); found = False; now = time.time() if now is None else float(now)
    for r in rows:
        if r.get("schedule_id") != schedule_id or r.get("canceled"): continue
        found = True; r["delivered_count"] = int(r.get("delivered_count", 0)) + 1
        try:
            lease = task_manager.claim_next(principal, task_id=r["task_id"], now=now)
            if lease:
                task_manager.complete_task(principal, lease, result_metadata={"outcome_code": "delivered"}, now=now)
        except Exception:
            pass
        if r.get("recurrence_seconds"): r["due_at"] = now + int(r["recurrence_seconds"])
        else: r["canceled"] = True
    if found: safe_write_json(_path(principal), rows)
    return found


def cancel_schedule(principal: TaskPrincipal, schedule_id: str) -> bool:
    rows = _load(_path(principal)); found = False
    for r in rows:
        if r.get("schedule_id") == schedule_id and not r.get("canceled"):
            r["canceled"] = True; found = True
            try: task_manager.request_cancel(principal, r["task_id"])
            except Exception: pass
    if found: safe_write_json(_path(principal), rows)
    return found


def list_schedules(principal: TaskPrincipal) -> list[dict[str, Any]]:
    return [{k: v for k, v in r.items() if k != "content"} for r in _load(_path(principal)) if not r.get("canceled")]
