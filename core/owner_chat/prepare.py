"""Concurrent pretool/context preparation with sibling cancellation."""

import asyncio
import time

from core.owner_chat.models import OwnerChatTurn, PreparedTurn


async def prepare_turn(turn: OwnerChatTurn, *, probe=None) -> PreparedTurn:
    """Prepare using the core probe, or an explicitly supplied request-local probe."""
    probe = probe if probe is not None else _probe_and_execute_tools
    allowed_tool_categories = turn.allowed_tool_categories
    allowed_tool_names = turn.allowed_tool_names
    _probe_text = turn._probe_text
    user_id = turn.user_id
    message = turn.message
    provenance_channel = turn.provenance_channel
    pipeline = turn.pipeline
    frozen_scope = turn.frozen_scope
    tool_execution_enabled = turn.tool_execution_enabled
    prompt_capture_origin = turn.prompt_capture_origin
    turn_source = turn.turn_source
    _frozen_scope = frozen_scope
    # probe（探针自读 short_term 最近 4 条，不吃 context）与 fetch_context
    # 互不依赖，并行跑掉其中较短的一段；两者都在 conversation_lock 内，
    # gather 不改变锁语义。各自计时供 [owner_chat/timing] 打点使用。
    _t_probe = 0.0
    _t_ctx = 0.0

    async def _timed_probe():
        nonlocal _t_probe
        _t0 = time.monotonic()
        probe_kwargs = {
            "char_id": _frozen_scope.character_id,
            "provenance_channel": provenance_channel,
        }
        if allowed_tool_categories is not None:
            probe_kwargs["categories"] = allowed_tool_categories
        if allowed_tool_names is not None:
            probe_kwargs["allowed_tool_names"] = allowed_tool_names
        result = await probe(_probe_text, user_id, **probe_kwargs)
        _t_probe = time.monotonic() - _t0
        return result

    async def _timed_ctx():
        nonlocal _t_ctx
        _t0 = time.monotonic()
        result = await pipeline.fetch_context(user_id, message, frozen_scope=_frozen_scope)
        _t_ctx = time.monotonic() - _t0
        return result

    if tool_execution_enabled:
        probe_task = asyncio.create_task(_timed_probe())
        context_task = asyncio.create_task(_timed_ctx())
        try:
            pretool_result, context = await asyncio.gather(probe_task, context_task)
        except BaseException:
            # gather does not stop its sibling after one fails. A failed
            # request must not leave a tool probe executing behind a retry.
            probe_task.cancel()
            context_task.cancel()
            await asyncio.gather(probe_task, context_task, return_exceptions=True)
            raise
    else:
        pretool_result, context = None, await _timed_ctx()
    _turn_char_name = getattr(
        context.get("_scoped_character"), "name", getattr(pipeline.character, "name", "AI")
    )
    from core.pretool_router import PreToolRouteResult
    if isinstance(pretool_result, PreToolRouteResult):
        tool_result_text = pretool_result.prompt_tool_result
        _pretool_direct_reply = (
            pretool_result.confirmation_request
            or pretool_result.missing_parameter_request
            or pretool_result.direct_response
        )
        _fast_path_exclude_tools = pretool_result.exclude_tools
        _tool_result_status = pretool_result.execution_status if pretool_result.tool_results else None
        _tool_result_generated_at = pretool_result.tool_result_generated_at
        _tool_call_required = pretool_result.must_call_tool
        _required_tool_names = pretool_result.required_tool_names
    else:
        # Compatibility for tests and older local extensions that patch the
        # former helper to return only a prompt tool-result string.
        tool_result_text = pretool_result
        _pretool_direct_reply = None
        _fast_path_exclude_tools = set()
        _tool_result_status = None
        _tool_result_generated_at = None
        _tool_call_required = False
        _required_tool_names = set()
    try:
        from core.observe.prompt_capture import set_capture_origin as _set_capture_origin
        _set_capture_origin({
            "origin": prompt_capture_origin or provenance_channel,
            "user_authored": turn_source == "user_chat",
        })
    except Exception:
        pass

    return PreparedTurn(
        pretool_result=pretool_result,
        context=context,
        _turn_char_name=_turn_char_name,
        tool_result_text=tool_result_text,
        _pretool_direct_reply=_pretool_direct_reply,
        _fast_path_exclude_tools=_fast_path_exclude_tools,
        _tool_result_status=_tool_result_status,
        _tool_result_generated_at=_tool_result_generated_at,
        _tool_call_required=_tool_call_required,
        _required_tool_names=_required_tool_names,
        _t_probe=_t_probe,
        _t_ctx=_t_ctx,
    )


async def _probe_and_execute_tools(
    message: str,
    user_id: str,
    *,
    char_id: str,
    provenance_channel: str,
    categories: frozenset[str] | None = None,
    allowed_tool_names: frozenset[str] | None = None,
):
    """Sole owner-chat adapter to the channel-neutral pre-tool router."""
    from core import tool_dispatcher
    from core.pretool_router import route_pretool
    from core.session_state import get as _get_state

    return await route_pretool(
        trusted_user_text=message,
        uid=user_id,
        char_id=char_id,
        channel=provenance_channel,
        provenance_channel=provenance_channel,
        target_id=user_id,
        is_group=False,
        session_state=_get_state(f"user_{user_id}"),
        tool_loop_enabled=tool_dispatcher.tool_loop_active(user_id),
        # Path A exposure is channel-neutral; resolve it from the shared
        # path configuration and character override.
        categories=list(categories) if categories is not None else None,
        allowed_tool_names=allowed_tool_names,
    )
