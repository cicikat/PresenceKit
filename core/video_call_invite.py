"""Ephemeral owner-only desktop video-call invitation lifecycle."""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

INVITE_SECONDS = 10
_pending: dict[tuple[str, str], dict] = {}
_active: dict[tuple[str, str], str] = {}
_counts = {"accepted": 0, "declined": 0, "unanswered": 0, "disconnected": 0}


def snapshot() -> dict:
    return {"pending": len(_pending), "active": len(_active), "counts": dict(_counts)}


async def invite(uid: str, char_id: str) -> dict:
    from channels import desktop_ws

    key = (str(uid), str(char_id))
    if not desktop_ws.is_connected():
        return {"status": "desktop_offline"}
    if key in _pending or key in _active:
        return {"status": "already_in_call"}
    invite_id = uuid4().hex
    future = asyncio.get_running_loop().create_future()
    row = {"id": invite_id, "future": future, "expires_at": time.monotonic() + INVITE_SECONDS}
    _pending[key] = row
    try:
        if not await desktop_ws.push_video_call_invite(invite_id, char_id, INVITE_SECONDS):
            return {"status": "desktop_offline"}
        try:
            status = await asyncio.wait_for(future, timeout=INVITE_SECONDS)
        except asyncio.TimeoutError:
            status = "unanswered"
        if status in _counts:
            _counts[status] += 1
        return {"status": status, "invite_id": invite_id}
    finally:
        if _pending.get(key) is row:
            _pending.pop(key, None)


def respond(uid: str, char_id: str, invite_id: str, status: str) -> bool:
    if status not in {"accepted", "declined"}:
        return False
    row = _pending.get((str(uid), str(char_id)))
    if not row or row["id"] != invite_id or time.monotonic() >= row["expires_at"]:
        return False
    future = row["future"]
    if future.done():
        return False
    if status == "accepted":
        _active[(str(uid), str(char_id))] = invite_id
    future.set_result(status)
    return True


def hangup(uid: str, char_id: str, invite_id: str) -> bool:
    key = (str(uid), str(char_id))
    if _active.get(key) != invite_id:
        return False
    _active.pop(key, None)
    from core.autonomy.models import ActionMode, Signal
    from core.autonomy import store

    store.enqueue_signal(uid, char_id, Signal(
        source="video_call_hangup",
        evidence=[{"fact": "owner_hung_up_video_call", "invite_id": invite_id}],
        reason="The owner ended the accepted video call; decide whether to act or say something once.",
        expiry=time.time() + 10 * 60,
        priority=0.7,
        action_mode=ActionMode.REFLECT.value,
    ), dedupe_key=f"video-call-hangup:{invite_id}")
    return True


def disconnect() -> None:
    for row in _pending.values():
        if not row["future"].done():
            row["future"].set_result("disconnected")
    _active.clear()
