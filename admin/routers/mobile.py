"""
手机端轮询接口。
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from admin.auth import verify_token

router = APIRouter()


def _get_mobile_channel():
    from channels.registry import get
    from channels.mobile import MobileChannel

    channel = get("mobile")
    if isinstance(channel, MobileChannel):
        return channel
    return None


@router.post("/mobile/activate", summary="手机端上线并激活 mobile 通道")
async def mobile_activate(auth=Depends(verify_token)):
    mobile = _get_mobile_channel()
    if mobile is None:
        return {"ok": False, "error": "mobile channel 未注册"}
    mobile.set_active(True)
    return {"ok": True, "active": True}


@router.post("/mobile/deactivate", summary="手机端下线并停用 mobile 通道")
async def mobile_deactivate(auth=Depends(verify_token)):
    mobile = _get_mobile_channel()
    if mobile is None:
        return {"ok": False, "error": "mobile channel 未注册"}
    mobile.set_active(False)
    return {"ok": True, "active": False}


@router.post("/mobile/chat", summary="手机端对话（走 mobile channel 语义）")
async def mobile_chat(body: dict = Body(...), auth=Depends(verify_token)):
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")

    # Safety net: hard reject reality turns when dream is active
    from admin.routers.chat import _check_reality_not_in_dream
    from core.config_loader import get_config as _cfg
    _uid = str(_cfg().get("scheduler", {}).get("owner_id", "owner"))
    _check_reality_not_in_dream(_uid)

    mobile = _get_mobile_channel()
    if mobile is not None:
        mobile.set_active(True)

    from core.scheduler.loop import mark_user_active
    from admin.routers.chat import run_owner_chat_turn

    mark_user_active()
    return await run_owner_chat_turn(message, "mobile")


@router.get("/mobile/poll", summary="手机端轮询主动消息")
async def mobile_poll(
    limit: int = Query(default=20, ge=1, le=50),
    wait: float = Query(default=0, ge=0, le=60),
    auth=Depends(verify_token),
):
    mobile = _get_mobile_channel()
    if mobile is None:
        return {"messages": [], "count": 0, "active": False}
    messages = await mobile.poll(limit=limit, wait_seconds=wait)
    return {"messages": messages, "count": len(messages), "active": True}


@router.post("/mobile/push", summary="向手机端主动消息队列写入一条消息")
async def mobile_push(body: dict, auth=Depends(verify_token)):
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
    if isinstance(behavior, dict):
        await mobile.send_with_behavior(content, user_id, behavior)
    else:
        await mobile.send(content, user_id)
    return {"ok": True, "queued": True, "active": mobile.is_active}
