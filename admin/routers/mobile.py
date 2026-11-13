"""
手机端轮询接口。
"""

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, WebSocket

from admin.auth import require_scopes
from core.audio_perception import voice_context
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

router = APIRouter()


_INSTALLATION_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class PushRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    installation_id: str = Field(pattern=_INSTALLATION_PATTERN)
    registration: str = Field(min_length=16, max_length=4096, pattern=r"^[A-Za-z0-9:_-]+$")
    consent: Literal[True]


def _push_credential(request: Request, auth) -> str:
    from admin.token_registry import hash_token

    if auth.profile != "mobile":
        raise HTTPException(403, "mobile_profile_required")
    return hash_token(request.headers["Authorization"].split(None, 1)[1])


@router.put("/mobile/push/devices/self")
async def register_push_device(
    request: Request,
    session_id: str = Header(alias="X-Presence-Session"),
    auth=Depends(require_scopes("chat")),
):
    from pydantic import ValidationError
    from admin.routers.chat import _session_grant
    from core import mobile_push_devices

    credential = _push_credential(request, auth)
    grant = _session_grant(session_id, auth.label)
    if grant is None:
        raise HTTPException(422, "session_scope_required")
    if not mobile_push_devices.configured():
        raise HTTPException(503, "fcm_unconfigured")
    try:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 5000:
                raise ValueError
            raw.extend(chunk)
        body = PushRegistration.model_validate_json(raw)
    except (ValidationError, ValueError):
        raise HTTPException(422, "invalid_registration") from None
    try:
        return mobile_push_devices.register(
            body.installation_id, grant.owner_id, grant.char_id, credential, body.registration,
        )
    except (PermissionError, OverflowError) as error:
        raise HTTPException(409, str(error)) from None


@router.delete("/mobile/push/devices/self")
async def unregister_push_device(
    request: Request, installation_id: str = Query(pattern=_INSTALLATION_PATTERN),
    auth=Depends(require_scopes("chat")),
):
    from core import mobile_push_devices

    mobile_push_devices.remove(installation_id, _push_credential(request, auth))
    return {"registered": False}


@router.get("/mobile/push/devices/status")
async def push_device_status(
    request: Request, installation_id: str = Query(pattern=_INSTALLATION_PATTERN),
    auth=Depends(require_scopes("chat")),
):
    from core import mobile_push_devices

    return {
        "configured": mobile_push_devices.configured(),
        **mobile_push_devices.status(installation_id, _push_credential(request, auth)),
    }


@router.get("/mobile/push/message")
async def read_push_message(
    request: Request,
    installation_id: str = Query(pattern=_INSTALLATION_PATTERN),
    generation: int = Query(ge=0),
    seq: int = Query(ge=1),
    message_id: str = Query(min_length=1, max_length=128),
    auth=Depends(require_scopes("chat")),
):
    from core import mobile_push_devices

    credential = _push_credential(request, auth)
    character = mobile_push_devices.bound_character(installation_id, credential, generation)
    mobile = _get_mobile_channel()
    if not character or mobile is None:
        raise HTTPException(404, "push_message_unavailable")
    item = await mobile.push_message(seq, message_id, mobile_push_devices.owner_id(), character)
    if item is None:
        raise HTTPException(404, "push_message_unavailable")
    return item


class MobileRealtimeMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["connect_ms", "reply_apply_ms", "recovered_messages", "fallback"]
    value: int = Field(ge=0, le=600_000)


@router.post("/mobile/realtime/metric", summary="手机实时传输计数（不接受正文或标识）")
async def mobile_realtime_metric(body: MobileRealtimeMetric, auth=Depends(require_scopes("chat"))):
    from core.mobile_realtime import record_metric
    record_metric(body.kind, body.value)
    return {"ok": True}


@router.websocket("/ws/mobile")
async def mobile_websocket(websocket: WebSocket):
    from core.mobile_realtime import serve
    await serve(websocket)


@router.get("/mobile/realtime", summary="手机实时传输能力（只读）")
async def mobile_realtime_capability(auth=Depends(require_scopes("chat"))):
    from core.mobile_realtime import enabled
    return {"enabled": enabled(), "mode": "final_only", "protocol": "mobile-final-v1"}


@router.get("/mobile/history", summary="持久最终展示消息（不推进投递ACK）")
async def mobile_final_history(
    before: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=50),
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    auth=Depends(require_scopes("memory.read")),
):
    from admin.routers.chat import _session_grant
    from core.mobile_realtime import history
    grant = _session_grant(x_presence_session, getattr(auth, "label", "legacy-admin"))
    if grant is None:
        raise HTTPException(422, "session_scope_required")
    return history(grant.memory_scope, before=before, limit=limit)


@router.get("/observability/mobile-realtime", summary="手机实时连接观测（无正文/凭据）")
async def mobile_realtime_observability(auth=Depends(require_scopes("state.read"))):
    from core.mobile_realtime import snapshot
    return snapshot()


@router.get("/observability/mobile-delivery", summary="手机后台投递时延（无正文/凭据）")
async def mobile_delivery_observability(auth=Depends(require_scopes("state.read"))):
    from core.mobile_delivery_trace import snapshot
    return snapshot()


@router.post("/mobile/chat", summary="手机端普通对话（Bearer 鉴权）")
@voice_context("mobile")
async def mobile_chat(
    body: dict,
    x_presence_session: str | None = Header(None, alias="X-Presence-Session"),
    _auth=Depends(require_scopes("chat")),
):
    """Run the shared reality-chat pipeline with mobile provenance."""
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")
    reply_to = body.get("reply_to")

    from admin.routers.chat import _check_reality_not_in_dream, run_owner_chat_turn
    from core.audio_perception import current_voice_extras
    from core.owner_turn_service import legacy_mobile_context, run_legacy_owner_turn
    from core.config_loader import get_config

    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    _check_reality_not_in_dream(uid)
    context = legacy_mobile_context(getattr(_auth, "label", "legacy-admin"))
    from admin.routers.chat import _run_session_request, _session_grant
    grant = _session_grant(x_presence_session, getattr(_auth, "label", "legacy-admin"))
    if grant is None and any(key in body for key in ("char_id", "session_id", "request_id")):
        raise HTTPException(status_code=422, detail="session_scope_required")

    voice_extras = current_voice_extras()  # A3：语音回执打标

    async def _execute():
        if grant is None:
            return await run_legacy_owner_turn(
                message, context, reply_to=reply_to, audit_extras=voice_extras,
                executor=run_owner_chat_turn,
            )
        return await run_owner_chat_turn(
            message, context.provenance_channel,
            live_origin_channel=context.live_origin_channel,
            durable_mobile_mirror=context.durable_mobile_mirror,
            reply_to=reply_to, frozen_scope=grant.memory_scope,
            request_id=str(body.get("request_id") or ""), audit_extras=voice_extras,
        )

    result = (
        await _run_session_request(
            grant=grant, request_id=body.get("request_id"),
            payload={"kind": "mobile_chat", "message": message, "reply_to": reply_to},
            executor=_execute,
        )
        if grant is not None else await _execute()
    )

    from core.scheduler.sensor_events import notify_chat_happened
    notify_chat_happened()
    return result


def _get_mobile_channel():
    from channels.registry import get
    from channels.mobile import MobileChannel

    channel = get("mobile")
    if isinstance(channel, MobileChannel):
        return channel
    return None


@router.post("/mobile/activate", summary="手机端上线并激活 mobile 通道")
async def mobile_activate(auth=Depends(require_scopes("chat"))):
    mobile = _get_mobile_channel()
    if mobile is None:
        return {"ok": False, "active": False, "error": "mobile channel 未注册"}
    mobile.set_active(True)
    return {"ok": True, "active": True}


@router.post("/mobile/deactivate", summary="手机端下线并停用 mobile 通道")
async def mobile_deactivate(auth=Depends(require_scopes("chat"))):
    mobile = _get_mobile_channel()
    if mobile is None:
        return {"ok": False, "active": False, "error": "mobile channel 未注册"}
    mobile.set_active(False)
    return {"ok": True, "active": False}


@router.get("/mobile/poll", summary="手机端轮询主动消息")
async def mobile_poll(
    after: int | None = Query(default=None, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    wait: float = Query(default=0, ge=0, le=60),
    auth=Depends(require_scopes("chat")),
):
    mobile = _get_mobile_channel()
    if mobile is None:
        return {
            "ok": False,
            "active": False,
            "error": "mobile channel 未注册",
            "messages": [],
            "count": 0,
            "cursor": after,
        }
    messages = await mobile.poll(after=after, limit=limit, wait_seconds=wait)
    cursor = max((message["seq"] for message in messages), default=after)
    return {
        "ok": True,
        "active": True,
        "messages": messages,
        "count": len(messages),
        "cursor": cursor,
    }


@router.post("/mobile/ack", summary="确认手机端已持久化的主动消息")
async def mobile_ack(body: dict = Body(...), auth=Depends(require_scopes("chat"))):
    ack_seq = body.get("ack_seq")
    if not isinstance(ack_seq, int) or isinstance(ack_seq, bool) or ack_seq < 0:
        raise HTTPException(status_code=422, detail="ack_seq 必须是非负整数")

    mobile = _get_mobile_channel()
    if mobile is None:
        return {"ok": False, "remaining": 0, "error": "mobile channel 未注册"}
    remaining = await mobile.ack(ack_seq)
    return {"ok": True, "remaining": remaining}


@router.post("/mobile/push", summary="向手机端主动消息队列写入一条消息")
async def mobile_push(body: dict, auth=Depends(require_scopes("chat"))):
    mobile = _get_mobile_channel()
    if mobile is None:
        return {"ok": False, "error": "mobile channel 未注册"}

    content = (body.get("content") or "").strip()
    if not content:
        return {"ok": False, "error": "content 不能为空"}

    user_id = str(body.get("user_id") or "").strip()
    if not user_id:
        from core.config_loader import get_config

        user_id = str(get_config().get("scheduler", {}).get("owner_id", ""))

    behavior = body.get("behavior")
    char_id = str(body.get("char_id") or "").strip() or None
    send_kwargs = {"proactive_push": True}
    if char_id is not None:
        send_kwargs["char_id"] = char_id
    if isinstance(behavior, dict):
        await mobile.send_with_behavior(content, user_id, behavior, **send_kwargs)
    else:
        await mobile.send(content, user_id, **send_kwargs)
    return {"ok": True, "queued": True, "active": mobile.is_active}
