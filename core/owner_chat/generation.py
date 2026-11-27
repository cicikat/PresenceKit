"""Model/Agent selection, protocol filtering and streaming lifecycle."""

import logging
import time

from fastapi import HTTPException
from core.tool_grounding import ToolProtocolStreamFilter, tool_protocol_offset
from core.owner_chat.models import OwnerChatTurn, PreparedTurn, GeneratedTurn
from core.pretool_router import PreToolRouteResult

logger = logging.getLogger("admin.routers.chat")


async def generate_turn(turn: OwnerChatTurn, prepared: PreparedTurn) -> GeneratedTurn:
    message = turn.message
    provenance_channel = turn.provenance_channel
    live_origin_channel = turn.live_origin_channel
    allowed_tool_categories = turn.allowed_tool_categories
    allowed_tool_names = turn.allowed_tool_names
    pipeline = turn.pipeline
    user_id = turn.user_id
    frozen_scope = turn.frozen_scope
    prompt_context_note = turn.prompt_context_note
    turn_source = turn.turn_source
    provenance_source = turn.provenance_source
    media_refs = turn.media_refs
    _loop_active = turn._loop_active
    _loop_session_state = turn._loop_session_state
    request_id = turn.request_id
    pretool_result = prepared.pretool_result
    context = prepared.context
    tool_result_text = prepared.tool_result_text
    _pretool_direct_reply = prepared._pretool_direct_reply
    _fast_path_exclude_tools = prepared._fast_path_exclude_tools
    _tool_result_status = prepared._tool_result_status
    _tool_result_generated_at = prepared._tool_result_generated_at
    _tool_call_required = prepared._tool_call_required
    _required_tool_names = prepared._required_tool_names
    _frozen_scope = turn.frozen_scope
    # ── 流式 vs 非流式分支 ──────────────────────────────────────────────────
    # desktop channel 且任一 UI 客户端（桌宠/设备）已连接时走流式：逐 token 推送，
    # 最终用 scrub 后文本替换。其余 channel（mobile、QQ 触发路径）和全部离线时
    # 保持非流式，确保完整消息语义。
    from channels import desktop_ws as _dws
    from channels import ui_push as _ui_push
    # Bind the NOW overlay at emit time. A turn-start snapshot of
    # is_connected() would drop tool_status for the whole turn if the
    # desktop WS connected after /desktop/chat started. Mobile HTTP
    # still uses this same owner-turn helper; the observer itself
    # must stay fail-open so a desktop WS stall cannot freeze
    # /mobile/activate or /mobile/chat.
    _tool_status_observer = _dws.push_tool_status

    _t_first_delta = None
    _t_stream = None
    _t_llm = None
    _use_stream = not _pretool_direct_reply and (
        provenance_channel == "desktop"
        and live_origin_channel == "desktop"
        and _ui_push.any_connected()
    )
    _stream_paragraph_enforcer = None
    if _pretool_direct_reply:
        messages = []
        _t_prompt = 0.0
        _stream_msg_id = None
        _t_llm = 0.0
        reply = _pretool_direct_reply
    else:
        _t0 = time.monotonic()
        messages, _ = pipeline.build_prompt(
            user_id,
            message,
            context,
            tool_result=tool_result_text,
            tool_result_status=_tool_result_status,
            tool_result_generated_at=_tool_result_generated_at,
            tool_call_required=_tool_call_required,
            required_tool_names=_required_tool_names,
            channel=provenance_channel,
            char_id=_frozen_scope.character_id,
        )
        if isinstance(pretool_result, PreToolRouteResult):
            _cu_prompt = pretool_result.computer_use_prompt()
            if _cu_prompt:
                messages.insert(max(0, len(messages) - 1), _cu_prompt)
        if prompt_context_note:
            messages.insert(0, {
                "role": "system",
                "content": prompt_context_note,
                "_layer": "companion_ingress",
            })
        _t_prompt = time.monotonic() - _t0
    if _use_stream:
        from core.output.segment_enforcer import (
            ParagraphStreamEnforcer,
            get_segment_enforce_settings,
        )

        _segment_enabled, _segment_min_len = get_segment_enforce_settings()
        if _segment_enabled:
            _stream_paragraph_enforcer = ParagraphStreamEnforcer(
                _segment_min_len,
            )
    if _use_stream:
        logger.debug("[owner_chat] stage=stream request_id=%s", request_id)
        _stream_msg_id = _dws._new_msg_id()
        await _ui_push.push_stream_start(
            _stream_msg_id,
            char_id=_frozen_scope.character_id,
            domain="reality",
            request_id=request_id or None,
        )
        _chunks: list[str] = []
        protocol_filter = ToolProtocolStreamFilter()
        _t_stream_launch = time.monotonic()
        _t_first_delta_ts = None
        _stream_source = None
        try:
            if _loop_active:
                _stream_source = await pipeline.tools.run(
                    messages, uid=user_id, char_id=_frozen_scope.character_id,
                    session_state=_loop_session_state, is_group=False, stream=True,
                    tool_event_observer=_tool_status_observer,
                    exclude_tools=_fast_path_exclude_tools,
                    tool_call_required=_tool_call_required,
                    required_tool_names=_required_tool_names,
                    media_refs=media_refs,
                    allowed_tool_categories=allowed_tool_categories,
                    allowed_tool_names=allowed_tool_names,
                )
            else:
                _stream_source = pipeline.generation.run_llm_stream(
                    messages, char_id=_frozen_scope.character_id, user_id=user_id,
                )
            async for piece in _stream_source:
                piece = protocol_filter.feed(piece)
                if protocol_filter.blocked:
                    raise HTTPException(status_code=502, detail="invalid_assistant_tool_protocol")
                if not piece:
                    continue
                if _t_first_delta_ts is None:
                    _t_first_delta_ts = time.monotonic()
                    _t_first_delta = _t_first_delta_ts - _t_stream_launch
                _chunks.append(piece)
                visible_piece = (
                    _stream_paragraph_enforcer.feed(piece)
                    if _stream_paragraph_enforcer is not None
                    else piece
                )
                if visible_piece:
                    from core.conversation_execution import prefix
                    prefix(visible_piece)
                    await _ui_push.push_stream_delta(
                        _stream_msg_id,
                        visible_piece,
                    )
            tail = protocol_filter.finish()
            if protocol_filter.blocked:
                raise HTTPException(status_code=502, detail="invalid_assistant_tool_protocol")
            if tail:
                _chunks.append(tail)
                visible_tail = (_stream_paragraph_enforcer.feed(tail)
                                if _stream_paragraph_enforcer is not None else tail)
                if visible_tail:
                    await _ui_push.push_stream_delta(_stream_msg_id, visible_tail)
            if _stream_paragraph_enforcer is not None:
                visible_tail = _stream_paragraph_enforcer.finish()
                if visible_tail:
                    await _ui_push.push_stream_delta(_stream_msg_id, visible_tail)
        except Exception as exc:
            # Newline lookahead can hold a short trailing segment when
            # the upstream disconnects. Flush only the
            # already-filtered text; do not complete the failed turn.
            if _chunks and _stream_paragraph_enforcer is not None:
                pending_text = _stream_paragraph_enforcer.finish()
                if pending_text:
                    await _ui_push.push_stream_delta(_stream_msg_id, pending_text)
            # Buffered gateway turns can fail before yielding their first
            # visible delta. Keep safe prose without claiming completion.
            partial = getattr(exc, "partial_text", "")
            if not _chunks and isinstance(partial, str) and partial.strip():
                from core.thinking import strip_think_tags
                failed_filter = ToolProtocolStreamFilter()
                visible = failed_filter.feed(strip_think_tags(partial))
                visible += failed_filter.finish()
                if not failed_filter.blocked and visible.strip():
                    await _ui_push.push_stream_delta(_stream_msg_id, visible)
            raise
        finally:
            close = getattr(_stream_source, "aclose", None)
            if close is not None:
                await close()
            await _ui_push.push_stream_end(_stream_msg_id)
        if _t_first_delta_ts is not None:
            _t_stream = time.monotonic() - _t_first_delta_ts
        reply = "".join(_chunks)
    elif not _pretool_direct_reply:
        _stream_msg_id = None
        _t0 = time.monotonic()
        if _loop_active:
            reply = await pipeline.tools.run(
                messages, uid=user_id, char_id=_frozen_scope.character_id,
                session_state=_loop_session_state, is_group=False,
                tool_event_observer=_tool_status_observer,
                exclude_tools=_fast_path_exclude_tools,
                tool_call_required=_tool_call_required,
                required_tool_names=_required_tool_names,
                media_refs=media_refs,
                allowed_tool_categories=allowed_tool_categories,
                allowed_tool_names=allowed_tool_names,
            )
        else:
            if provenance_source or turn_source != "user_chat":
                reply = await pipeline.generation.run_llm(
                    messages,
                    char_id=_frozen_scope.character_id,
                    is_proactive=turn_source != "user_chat",
                )
            else:
                # Production Pipeline accepts explicit char_id. Keep direct
                # test/extension seams with the historical one-argument
                # signature working without weakening the production route.
                import inspect as _inspect
                _params = _inspect.signature(pipeline.generation.run_llm).parameters
                if "char_id" in _params:
                    reply = await pipeline.generation.run_llm(
                        messages, char_id=_frozen_scope.character_id,
                    )
                else:
                    reply = await pipeline.generation.run_llm(messages)
        _t_llm = time.monotonic() - _t0
    from core.llm_reasoning_store import finish_turn_capture
    finish_turn_capture()
    if reply and tool_protocol_offset(reply) is not None:
        raise HTTPException(status_code=502, detail="invalid_assistant_tool_protocol")
    if not reply or not reply.strip():
        logger.warning(
            "[owner_chat] empty model reply channel=%s loop_active=%s",
            provenance_channel, _loop_active,
        )
        raise HTTPException(
            status_code=502,
            detail="模型服务未返回回复正文，请重试或切换模型预设。",
        )


    return GeneratedTurn(
        reply=reply,
        _stream_msg_id=_stream_msg_id,
        _use_stream=_use_stream,
        _t_first_delta=_t_first_delta,
        _t_stream=_t_stream,
        _t_llm=_t_llm,
        _t_prompt=_t_prompt,
    )
