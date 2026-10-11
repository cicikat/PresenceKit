"""Owner-gated activity dialogue; structured evidence remains in its own surface."""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import uuid
import inspect
import typing

from fastapi import HTTPException
from core.activity import records
from core.config_loader import get_config
from core.conversation_gate import conversation_lock
from core.event_context import EventContext
from core.memory.scope import MemoryScope
from core.safe_write import safe_write_json
from core.sandbox import get_paths
from core.turn_sink import record_assistant_turn, TurnSource
from core.write_envelope import stamp_user_chat


def reality_dialogue(kind, *, assistant_only=False):
    def decorate(function):
        @functools.wraps(function)
        async def wrapped(body, auth=None):
            from admin.routers._common import active_char_id
            uid = str(get_config().get("scheduler", {}).get("owner_id") or "")
            char_id = active_char_id()
            if not uid:
                raise HTTPException(503, "owner not configured")
            requested = str(getattr(body, "uid", "") or "").strip()
            if requested and requested != uid:
                raise HTTPException(403, "activity owner mismatch")
            if hasattr(body, "uid"):
                body = body.model_copy(update={"uid": uid})
            if kind == "minecraft":
                from core.activity.minecraft import get_service
                binding = get_service().binding
                if binding is None or binding.uid != uid or binding.char_id != char_id:
                    raise HTTPException(409, "Minecraft session scope unavailable")
                sid = binding.session_id
            else:
                sid = body.session_id
            try:
                records.session(uid, char_id, kind, sid)
            except LookupError:
                raise HTTPException(404, "activity source unavailable")
            request_id = str(getattr(body, "request_id", "") or uuid.uuid4().hex)
            text = "" if assistant_only else str(getattr(body, "message", getattr(body, "text", "")))
            key = hashlib.sha256(request_id.encode()).hexdigest()
            directory = get_paths().activity_session_dir(char_id=char_id, uid=uid,
                                                         activity_type=kind, session_id=sid)
            receipt_path = directory / "dialogue_receipts" / f"{key}.json"
            async with conversation_lock(uid):
                if active_char_id() != char_id:
                    raise HTTPException(409, "character changed; retry in current scope")
                receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else None
                if receipt and receipt["input_hash"] != hashlib.sha256(text.encode()).hexdigest():
                    raise HTTPException(409, "request identity conflict")
                if receipt and receipt.get("status") == "completed":
                    return receipt["response"]
                if receipt is None:
                    response = await function(body, auth=auth)
                    if assistant_only and not response.get("comment"):
                        return response
                    receipt = {"status": "pending", "response": response, "request_id": request_id,
                               "input_hash": hashlib.sha256(text.encode()).hexdigest(),
                               "turn_id": uuid.uuid4().hex}
                    if not safe_write_json(receipt_path, receipt):
                        raise HTTPException(503, "activity receipt persistence failed")
                context = EventContext.from_ingress(
                    uid=uid, char_id=char_id, ingress_event_id=f"activity:{kind}:{sid}:{key}",
                    dedupe_key=key, source="user_chat", channel=f"activity:{kind}",
                    kind="trigger" if assistant_only else "user_message", actor="assistant" if assistant_only else "user").with_turn(receipt["turn_id"])
                result = await record_assistant_turn(
                    assistant_text=receipt["response"]["comment" if assistant_only else "reply"],
                    user_text=None if assistant_only else text,
                    trigger_name=f"activity_comment:{kind}" if assistant_only else None,
                    uid=uid, char_id=char_id, source=TurnSource.TRIGGER if assistant_only else TurnSource.USER_CHAT,
                    envelope=stamp_user_chat(), frozen_scope=MemoryScope.reality_scope(uid, char_id),
                    bypass_gate=True, fanout=[], durable_mobile_mirror=False,
                    event_channel=f"activity:{kind}", provenance_source=f"activity:{kind}",
                    event_context=context, request_id=request_id,
                    media_refs=[{"type": "activity_record", "source_ref": f"activity:{kind}:{sid}:dialogue"}])
                if not result.written_to_memory:
                    raise HTTPException(503, "activity history persistence pending")
                response = receipt["response"] | {"turn_id": result.turn_id, "msg_id": result.msg_id,
                                                    "request_id": request_id,
                                                    "source_ref": f"activity:{kind}:{sid}:dialogue"}
                receipt.update(status="completed", response=response)
                if not safe_write_json(receipt_path, receipt):
                    raise HTTPException(503, "activity receipt persistence failed")
                asyncio.create_task(refresh_projection(uid, char_id, kind, sid))
                return response
        hints = typing.get_type_hints(function)
        original = inspect.signature(function)
        wrapped.__signature__ = original.replace(parameters=[
            parameter.replace(annotation=hints.get(name, parameter.annotation))
            for name, parameter in original.parameters.items()])
        return wrapped
    return decorate


async def refresh_projection(uid, char_id, kind, sid):
    """Extractive segments, never invented emotions; originals remain authoritative."""
    from core.activity import activity_summary
    await activity_summary.generate_and_reflow(uid, char_id, kind, sid)
