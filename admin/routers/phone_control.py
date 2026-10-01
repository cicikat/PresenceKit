"""手机自动化（"computer use 手机版"）循环端点。

见 docs/protocols/phone-control-protocol.md（Emerald-mobile 仓库）。设备侧每一步调用一次
POST /phone_control/step：上报当前观察，换回下一步动作。步数上限/超时由后端 task_state 维护，
不信设备自己数；敏感页面拦截在调用视觉模型之前做，未过滤通过的观察不会喂给视觉模型。
"""
import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from admin.auth import require_scopes

logger = logging.getLogger(__name__)
router = APIRouter()


def _node_texts(nodes: list) -> list[str]:
    texts: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for key in ("text", "content_desc"):
            value = node.get(key)
            if isinstance(value, str) and value:
                texts.append(value)
    return texts


@router.post("/phone_control/step", summary="手机自动化循环：上报观察，换回下一步动作")
async def phone_control_step(body: dict = Body(...), auth=Depends(require_scopes("chat"))):
    from core.phone_control import sensitive_filter, task_state, vision_client

    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=422, detail="task_id 不能为空")

    package_name = str(body.get("package_name") or "").strip()
    screen_title = str(body.get("screen_title") or "").strip()
    nodes = body.get("nodes")
    nodes = nodes if isinstance(nodes, list) else []
    screenshot_base64 = body.get("screenshot_base64")
    if not isinstance(screenshot_base64, str) or not screenshot_base64:
        screenshot_base64 = None

    step, refuse_reason = task_state.record_step(task_id)
    if step is None:
        logger.info("[phone_control] task=%s refused at gate: %s", task_id, refuse_reason)
        return {"status": "refused", "action": None, "message": _refuse_message(refuse_reason)}

    # 敏感页面硬拦截：先过这道闸，过不了就不调用视觉模型——不给它判断的机会。
    block_reason = sensitive_filter.check_observation(
        package_name=package_name,
        screen_title=screen_title,
        node_texts=_node_texts(nodes),
    )
    if block_reason is not None:
        task_state.mark_status(task_id, "need_confirmation")
        logger.info("[phone_control] task=%s blocked by sensitive_filter: %s", task_id, block_reason)
        return {"status": "need_confirmation", "action": None, "message": block_reason}

    entry = task_state.get_task(task_id) or {}
    task_description = str(entry.get("task") or "")
    history_summary = task_state.get_history_summary(task_id)

    decision, error = await vision_client.decide_next_action(
        task=task_description,
        package_name=package_name,
        screen_title=screen_title,
        nodes=nodes,
        screenshot_base64=screenshot_base64,
        history_summary=history_summary,
    )
    if decision is None:
        task_state.mark_status(task_id, "refused")
        logger.warning("[phone_control] task=%s vision_client failed: %s", task_id, error)
        return {"status": "refused", "action": None, "message": _vision_error_message(error)}

    action_type = decision.action.get("type") if decision.action else decision.status
    task_state.append_history(task_id, step, action_type, decision.reasoning)

    if decision.status in ("done", "need_confirmation"):
        task_state.mark_status(task_id, decision.status)

    return {
        "status": decision.status,
        "action": decision.action,
        "message": decision.message,
    }


@router.get("/phone_control/status", summary="phone_control 只读状态：角色是否已授权 + 视觉模型是否已配置")
async def phone_control_status(auth=Depends(require_scopes("chat"))):
    """给手机端能力检查页用的只读诊断，不暴露 api_key 等敏感字段本身，只给布尔判断。

    - tool_enabled：保留为 Path C 是否暴露 ``phone_control`` 的兼容字段；同时返回
      path_a_enabled/path_c_enabled，二者都由共享 tool_exposure 解析器计算。
    - vision_configured：screen 路由主连接（core/phone_control/vision_client.phone_vision_chain()）
      的 base_url + model 是否都非空（不检查 api_key 是否真的有效，只检查有没有填）。
    """
    from admin.routers.character import _active_character_id
    from core.phone_control.vision_client import phone_vision_chain

    path_a_enabled = False
    path_c_enabled = False
    active_id = _active_character_id()
    if active_id:
        try:
            from core.tool_exposure import resolve as resolve_exposure

            def _enabled(path: str) -> bool:
                exposure = resolve_exposure(path, char_id=active_id)
                if exposure.character_load_failed:
                    return False
                return (
                    "phone_control" in exposure.categories
                    and (exposure.tools is None or "phone_control_start" in exposure.tools)
                    and "phone_control_start" not in exposure.exclude_tools
                )

            path_a_enabled = _enabled("path_a")
            path_c_enabled = _enabled("path_c")
        except Exception:
            pass

    chain = phone_vision_chain()
    vision_cfg = chain[0]["config"] if chain else {}
    vision_configured = bool(vision_cfg.get("base_url")) and bool(vision_cfg.get("model"))

    return {
        "tool_enabled": path_c_enabled,
        "path_a_enabled": path_a_enabled,
        "path_c_enabled": path_c_enabled,
        "vision_configured": vision_configured,
        "char_id": active_id,
    }


@router.post(
    "/phone_control/debug/start",
    summary="调试：跳过 LLM 判断和 chat 二次确认，直接发起一次手机自动化任务",
)
async def phone_control_debug_start(body: dict = Body(...), auth=Depends(require_scopes("chat"))):
    """只供测试用：跳过"要不要调用这个工具"的 LLM 判断，也跳过 chat 内的二次确认，
    但不跳过真正的安全闸——danger-mode 门禁在这里手动复用 tool_dispatcher._current_mode()，
    走的是和角色真的在对话里调用 phone_control_start 时同一道闸，不因为是调试端点就放宽。
    task_state 注册、behavior 下发都复用 tool_dispatcher._phone_control_start_wrapper()，
    跟真实调用路径是同一份代码，不是另起一套逻辑。
    """
    from core.tool_dispatcher import _current_mode, _phone_control_start_wrapper

    task = str(body.get("task") or "").strip()
    if not task:
        raise HTTPException(status_code=422, detail="task 不能为空")

    if _current_mode() != "danger":
        return {
            "ok": False,
            "message": "现在是安全模式，我不能操作你的手机。要先 PATCH /system/meta-mode 开启危险模式。",
        }

    user_id = str(body.get("user_id") or "").strip()
    if not user_id:
        from core.config_loader import get_config
        user_id = str(get_config().get("scheduler", {}).get("owner_id", ""))

    from admin.routers.character import _active_character_id
    char_id = str(body.get("char_id") or "").strip() or _active_character_id()

    message = await _phone_control_start_wrapper(task, user_id=user_id, char_id=char_id)
    return {"ok": True, "message": message}


def _refuse_message(reason: str | None) -> str:
    return {
        "unknown_task": "任务不存在或已结束",
        "max_steps_exceeded": "这个任务步数太多了，先停在这里，需要的话请自己继续",
        "step_timeout": "这一步等太久了，已经放弃，需要的话请自己继续",
    }.get(reason or "", "任务已结束")


def _vision_error_message(error: str | None) -> str:
    return {
        "unconfigured": "视觉模型还没配置好，没法真的帮你操作手机",
        "invalid": "视觉模型返回的内容没看懂，先停在这里",
        "error": "视觉模型调用失败，先停在这里",
    }.get(error or "", "自动操作出错，先停在这里")
