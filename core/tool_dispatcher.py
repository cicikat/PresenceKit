"""Stable outward tool API; the runtime never imports this facade.

Registry aliases retain identity. Private domain helpers are imported from their
owners; the few compatibility exports below have repository production callers.
"""

from typing import Callable

from core.control_markers import parse_tail_brace
from core.tool_runtime.registry import _TOOL_REGISTRY
from core.tool_runtime import execution as _execution, policy as _policy, outcome as _outcome
from core.tool_runtime.exposure import (
    get_tools_schema, format_mcp_opaque_params_note,
    get_probe_prompt, get_tool_loop_relay_prompt,
)
from core.tool_runtime.policy import (
    get_tool_effect, is_side_effect_tool, tool_fast_path_risk,
    intiface_opted_in, tool_loop_active,
)
from core.tool_runtime.outcome import ToolExecutionOutcome
from core.tools.devices_definitions import resolve_action_target

_send_callback: Callable | None = None


def register_send_callback(callback: Callable):
    """注入发送消息的回调函数（由 main.py 在初始化时调用）"""
    global _send_callback
    _send_callback = callback


async def execute_structured(
    tool_name: str,
    tool_args: dict,
    user_id: str,
    target_id: str,
    is_group: bool,
    session_state,
    *,
    origin: str,
    char_id: str,
    bypass_read_log: bool = False,
    tool_status_observer=None,
    allowed_tool_names: frozenset[str] | None = None,
    pin_reference_hashes: frozenset[str] = frozenset(),
) -> ToolExecutionOutcome:
    from core.input_provenance import assert_production_allowed
    assert_production_allowed()
    from core.tool_activity import execute_visible
    outcome = await execute_visible(
        _execution._execute_structured_impl,
        tool_name, tool_args, user_id, target_id, is_group, session_state,
        origin=origin,
        char_id=char_id,
        bypass_read_log=bypass_read_log,
        tool_status_observer=tool_status_observer,
        allowed_tool_names=allowed_tool_names,
        pin_reference_hashes=pin_reference_hashes,
    )
    if origin in _policy._EXECUTE_ALLOWED_ORIGINS:
        from core.tool_audit import record as record_tool_audit
        record_tool_audit(user_id, char_id, tool=tool_name,
                          args={} if _outcome.private_receipt(tool_name) else tool_args,
                          origin=origin, outcome=outcome)
    return outcome


class ToolDispatcher:
    """工具调度类封装，供外部按类方式导入使用"""

    def register_send_callback(self, callback):
        register_send_callback(callback)

    def get_tools_schema(self, categories: list[str] | None = None, *, char_id: str | None = None, uid: str | None = None) -> list:
        return get_tools_schema(categories=categories, char_id=char_id, uid=uid)

    async def execute_structured(self, tool_name, tool_args, user_id, target_id, is_group, session_state, *, origin: str, char_id: str, bypass_read_log: bool = False, tool_status_observer=None, allowed_tool_names: frozenset[str] | None = None):
        return await execute_structured(
            tool_name=tool_name,
            tool_args=tool_args,
            user_id=user_id,
            target_id=target_id,
            is_group=is_group,
            session_state=session_state,
            origin=origin,
            char_id=char_id,
            bypass_read_log=bypass_read_log,
            tool_status_observer=tool_status_observer,
            allowed_tool_names=allowed_tool_names,
        )
