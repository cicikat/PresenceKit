"""
手机端轮询接口。
"""

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from admin.auth import require_scopes
from core.audio_perception import voice_context

router = APIRouter()


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
    from core.owner_turn_service import legacy_mobile_context, run_legacy_owner_turn
    from core.config_loader import get_config

    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    _check_reality_not_in_dream(uid)
    context = legacy_mobile_context(getattr(_auth, "label", "legacy-admin"))
    from admin.routers.chat import _run_session_request, _session_grant
    grant = _session_grant(x_presence_session, getattr(_auth, "label", "legacy-admin"))
    if grant is None and any(key in body for key in ("char_id", "session_id", "request_id")):
        raise HTTPException(status_code=422, detail="session_scope_required")

    async def _execute():
        if grant is None:
            return await run_legacy_owner_turn(
                message, context, reply_to=reply_to, executor=run_owner_chat_turn,
            )
        return await run_owner_chat_turn(
            message, context.provenance_channel,
            live_origin_channel=context.live_origin_channel,
            durable_mobile_mirror=context.durable_mobile_mirror,
            reply_to=reply_to, frozen_scope=grant.memory_scope,
            request_id=str(body.get("request_id") or ""),
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
    send_kwargs = {"char_id": char_id} if char_id is not None else {}
    if isinstance(behavior, dict):
        await mobile.send_with_behavior(content, user_id, behavior, **send_kwargs)
    else:
        await mobile.send(content, user_id, **send_kwargs)
    return {"ok": True, "queued": True, "active": mobile.is_active}
