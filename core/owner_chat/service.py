"""Owner admission, frozen identity and lock across all turn phases."""

import logging
import time

from fastapi import HTTPException
from core.owner_chat.models import OwnerChatTurn

logger = logging.getLogger("admin.routers.chat")


from core.llm_reasoning_store import associate_owner_turn
from core.tool_activity import associate_owner_turn as associate_owner_tools


@associate_owner_turn
@associate_owner_tools
async def run_owner_chat_turn(
    message: str,
    provenance_channel: str,
    *,
    live_origin_channel: str | None = None,
    durable_mobile_mirror: bool = True,
    trusted_user_text: str | None = None,
    reply_to: dict | None = None,
    allowed_tool_categories: frozenset[str] | None = None,
    allowed_tool_names: frozenset[str] | None = None,
    audit_extras: dict | None = None,
    pipeline_override=None,
    fixed_user_id: str | None = None,
    frozen_scope=None,
    tool_execution_enabled: bool = True,
    prompt_context_note: str = "",
    prompt_capture_origin: str | None = None,
    turn_source: str = "user_chat",
    trigger_name: str = "",
    envelope=None,
    fanout="all",
    provenance_source: str = "",
    schedule_slow: bool = True,
    media_refs: list[dict] | None = None,
    event_context=None,
    ingress_event_id: str = "",
    ingress_dedupe_key: str = "",
    request_id: str = "",
) -> dict:
    """
    手机/桌宠共用的 owner 对话入口。
    conversation_lock 覆盖 fetch_context → LLM → critical post_process，
    保证同一用户多端输入不会并行读取旧上下文。

    trusted_user_text: 媒体拼接前的原始用户文本，仅用于 probe；
      不传时退化为 message（纯文字消息两者相同）。
      media 端点须显式传入原始 message（在 full_message 拼接前捕获）。

    reply_to: 引用回复契约（Brief 98 §2），可选 {text, ts}。校验通过则把
      「用户回复了你{相对时间}发送的这条消息「{text}」：」前缀拼进 message，
      随后 fetch_context / build_prompt / capture_turn 全部消费拼接后的
      message，short_term / mid_term / event_log 自然捕获，无需额外改造。
      探针（_probe_text）刻意使用拼接前的原始文本，避免引用原文里的操作性
      短语被探针误判为当轮指令。非法/缺失时静默降级为普通消息。
    """
    logger.debug("[owner_chat] stage=admission request_id=%s", request_id)
    from core.input_provenance import assert_production_allowed
    assert_production_allowed()
    live_origin_channel = live_origin_channel or provenance_channel
    from core.pipeline_registry import get as _get_pipeline
    pipeline = pipeline_override or _get_pipeline()
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Bot pipeline 未初始化，请先启动主程序")

    from core.config_loader import get_config
    user_id = fixed_user_id or str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    if not user_id:
        raise HTTPException(status_code=503, detail="owner_id 未配置")
    try:
        from core.scheduler.loop import mark_user_active
        from core.scheduler.state_machine import notify_owner_turn

        mark_user_active()
        notify_owner_turn(user_id)
        from core.scheduler.proactive_ledger import record_user_message
        record_user_message(user_id)
    except Exception:
        logger.exception("[owner_chat] trigger state notify_owner_turn 失败")

    _probe_text = trusted_user_text if trusted_user_text is not None else message

    from core.reply_context import apply_reply_prefix

    from core.conversation_gate import conversation_lock

    # ── N1: turn-level scope freeze ──────────────────────────────────────────
    # Resolve active character exactly once per owner turn; fetch_context /
    # build_prompt / post_process all consume this frozen scope so a mid-turn
    # character switch (admin panel) cannot split reads and writes across
    # two characters.
    try:
        _frozen_scope = frozen_scope or pipeline._current_reality_scope(user_id)
    except (ValueError, RuntimeError) as _scope_err:
        logger.error("[owner_chat] scope freeze 失败，本轮中止: %s", _scope_err)
        raise HTTPException(status_code=503, detail="active character 状态异常，本轮中止")

    message = apply_reply_prefix(message, reply_to, user_id=user_id, char_id=_frozen_scope.character_id)

    # Freeze ingress identity alongside the already-frozen memory scope. The
    # compatibility path is explicit and content-free; no active role lookup
    # occurs after this point.
    try:
        from core.event_context import EventContext
        if event_context is None:
            import uuid as _uuid
            ingress_id = str(ingress_event_id or _uuid.uuid4())
            dedupe_key = str(ingress_dedupe_key or ingress_id)
            event_context = EventContext.from_ingress(
                uid=user_id, char_id=_frozen_scope.character_id,
                ingress_event_id=ingress_id, dedupe_key=dedupe_key,
                source=provenance_source or turn_source, channel=provenance_channel,
                kind="user_message" if turn_source == "user_chat" else "trigger",
                actor="user" if turn_source == "user_chat" else "system",
            )
            from core.event_context_observer import record as _record_event_context
            _record_event_context(stage="ingress", disposition="accepted", context=event_context)
        elif event_context.scope != _frozen_scope:
            raise ValueError("event_context scope does not match owner chat scope")
    except Exception:
        logger.error("[owner_chat] EventContext construction failed", exc_info=True)
        raise HTTPException(status_code=503, detail="event context 状态异常，本轮中止")

    # Brief 28 · Path C 总闸：开关开 + owner（此端点固定 owner）+ chat preset 为
    # function_calling。为真时跳过探针，主生成走 run_agentic_loop。
    from core import tool_dispatcher as _td_loop
    _loop_active = tool_execution_enabled and _td_loop.tool_loop_active(user_id)
    from core.session_state import get as _get_loop_state
    _loop_session_state = _get_loop_state(f"user_{user_id}")

    _t_start = time.monotonic()
    async with conversation_lock(user_id):
        from core.tools.chat_artifacts import begin_turn_collection, drain_turn_artifacts
        begin_turn_collection()
        try:
            return await _execute_owner_chat_turn_locked(
                message=message,
                provenance_channel=provenance_channel,
                live_origin_channel=live_origin_channel,
                durable_mobile_mirror=durable_mobile_mirror,
                _probe_text=_probe_text,
                allowed_tool_categories=allowed_tool_categories,
                allowed_tool_names=allowed_tool_names,
                audit_extras=audit_extras,
                pipeline=pipeline,
                user_id=user_id,
                frozen_scope=_frozen_scope,
                tool_execution_enabled=tool_execution_enabled,
                prompt_context_note=prompt_context_note,
                prompt_capture_origin=prompt_capture_origin,
                turn_source=turn_source,
                trigger_name=trigger_name,
                envelope=envelope,
                fanout=fanout,
                provenance_source=provenance_source,
                schedule_slow=schedule_slow,
                media_refs=media_refs,
                event_context=event_context,
                _loop_active=_loop_active,
                _loop_session_state=_loop_session_state,
                _t_start=_t_start,
                request_id=request_id,
            )
        finally:
            drain_turn_artifacts()


async def _execute_owner_chat_turn_locked(
    *,
    message,
    provenance_channel,
    live_origin_channel,
    durable_mobile_mirror,
    _probe_text,
    allowed_tool_categories,
    allowed_tool_names,
    audit_extras,
    pipeline,
    user_id,
    frozen_scope,
    tool_execution_enabled,
    prompt_context_note,
    prompt_capture_origin,
    turn_source,
    trigger_name,
    envelope,
    fanout,
    provenance_source,
    schedule_slow,
    media_refs,
    event_context,
    _loop_active,
    _loop_session_state,
    _t_start,
    request_id,
):
    from core.owner_chat.prepare import prepare_turn
    from core.owner_chat.generation import generate_turn
    from core.owner_chat.delivery import commit_and_deliver
    # Preserve the existing local-extension/test injection point. The router
    # re-exports the single prepare implementation; it owns no probe logic.
    from admin.routers.chat import _probe_and_execute_tools

    turn = OwnerChatTurn(
        message=message,
        provenance_channel=provenance_channel,
        live_origin_channel=live_origin_channel,
        durable_mobile_mirror=durable_mobile_mirror,
        _probe_text=_probe_text,
        allowed_tool_categories=allowed_tool_categories,
        allowed_tool_names=allowed_tool_names,
        audit_extras=audit_extras,
        pipeline=pipeline,
        user_id=user_id,
        frozen_scope=frozen_scope,
        tool_execution_enabled=tool_execution_enabled,
        prompt_context_note=prompt_context_note,
        prompt_capture_origin=prompt_capture_origin,
        turn_source=turn_source,
        trigger_name=trigger_name,
        envelope=envelope,
        fanout=fanout,
        provenance_source=provenance_source,
        schedule_slow=schedule_slow,
        media_refs=media_refs,
        event_context=event_context,
        _loop_active=_loop_active,
        _loop_session_state=_loop_session_state,
        _t_start=_t_start,
        request_id=request_id,
    )
    stage = "prepare"
    try:
        logger.debug("[owner_chat] stage=%s request_id=%s", stage, request_id)
        prepared = await prepare_turn(turn, probe=_probe_and_execute_tools)
        stage = "generate"
        logger.debug("[owner_chat] stage=%s request_id=%s", stage, request_id)
        generated = await generate_turn(turn, prepared)
        stage = "commit/deliver"
        return await commit_and_deliver(turn, prepared, generated)
    except BaseException as exc:
        logger.debug("[owner_chat] stage=%s request_id=%s status=failed kind=%s",
                     stage, request_id, type(exc).__name__)
        raise
