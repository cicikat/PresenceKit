"""Stable tool API and legacy dependency-injection seam.

Implementations/schema live with their domains in core.tools; shared execution
responsibilities live in core.tool_runtime. Registry extensions mutate one object.
"""

import inspect
import hashlib
import json
import logging
import platform
import re
import subprocess
import uuid
from dataclasses import dataclass
from typing import Callable
from core.config_loader import get_config
from core.character_name_provider import get_active_char_name, get_char_name
from core.control_markers import parse_tail_brace  # noqa: F401  — public re-export
from core.error_handler import log_error
from core.tools.garden_tools import water_garden
from core.tools.memory_dossiers import (
    get_memory_consolidation_status as _get_memory_consolidation_status_wrapper,
    read_memory_dossier as _read_memory_dossier_wrapper,
    request_memory_consolidation as _request_memory_consolidation_wrapper,
    search_dossier_events as _search_dossier_events_wrapper,
    search_memory_dossiers as _search_memory_dossiers_wrapper,
    update_memory_dossier as _update_memory_dossier_wrapper,
)
import json as _json
import time as _time
from pathlib import Path as _Path
from core.food_memory import read_food_preferences as _read_food_preferences
from core.tools import xiaohongshu_continuation as _xhs_continuation
from core import quote_notebook as _quote_notebook
from core import tool_result_pins as _tool_result_pins
from core.agent_runtime import computer_use as _computer_use
from core.agent_runtime import agent_sessions as _agent_sessions
from core.agent_runtime.models import TaskPrincipal as _TaskPrincipal
from core.tools.chat_artifacts import register_tools as _register_artifact_tools
from core.tools.drinking import register_tools as _register_drinking_tools
from core.tools.character_self import register_tools as _register_self_tools
from core.tools.reminder_tools import register_tools as _register_reminder_tools
from core.tools.qzone_tools import register_tools as _register_qzone_tools, _QZONE_TOOL_NAMES
from core.listening_tools import register_tools as _register_listening_tools
from core.perception.ambient_state import read_tool as _get_ambient_state
from core.tools.image_gen import register_tools as _register_image_gen_tools
from core.tools import gmail_tools as _gmail_tools
from core.tools import device_file_tools as _device_file_tools
from core.tools import character_intent_tools as _intent_tools
from core.conversation_stats import attributed as _stats_attributed
from core.tools.information_definitions import (
    _get_current_time,
    _weather_wrapper,
    _web_search_wrapper,
    _read_xiaohongshu_wrapper,
)
from core.tools.memory_definitions import (
    _read_diary_wrapper,
    _backfill_diary_wrapper,
    _read_watch_wrapper,
    _read_hds_heart_rate_wrapper,
    _search_diary_wrapper,
    _search_documents_wrapper,
    _read_document_wrapper,
    _search_character_notes_wrapper,
    _read_document_notes_wrapper,
    _write_document_note_wrapper,
    _get_profile_wrapper,
    _get_episodic_wrapper,
    _read_long_user_message_wrapper,
    _revise_memory_wrapper,
    _forget_episodic_wrapper,
    _clear_midterm_wrapper,
    _revise_user_profile_wrapper,
    _exit_yandere_wrapper,
)
from core.tools.devices_definitions import (
    _device_shutdown,
    _device_sleep,
    resolve_action_target,
    _is_desktop_active,
    _push_desktop_action,
    _desktop_minimize_wrapper,
    _desktop_open_url_wrapper,
    _desktop_play_pause_wrapper,
    _desktop_notify_wrapper,
    _toy_invite_wrapper,
    _dream_invite_wrapper,
    _phone_control_start_wrapper,
    _toy_vibrate_wrapper,
    _toy_stop_wrapper,
    _toy_pattern_wrapper,
    _toy_job_status_wrapper,
    _peek_screen_content_wrapper,
    _computer_use_start,
    _computer_use_status,
    _computer_use_cancel,
    _agent_session_start,
    _agent_session_status,
    _agent_session_control,
    _observe_user_screen_wrapper,
    _observe_video_call_camera_wrapper,
    _invite_video_call_wrapper,
)
from core.tools.files_agents_definitions import (
    _fs_list_wrapper,
    _fs_read_wrapper,
    _workspace_list_wrapper,
    _workspace_read_wrapper,
    _workspace_tool_request,
    _workspace_write_wrapper,
    _workspace_create_wrapper,
    _workspace_update_wrapper,
    _workspace_delete_wrapper,
    _workspace_undo_wrapper,
    _process_run_wrapper,
    _start_agent_task_wrapper,
    _get_agent_task_wrapper,
    _cancel_agent_task_wrapper,
    _browser_automation_wrapper,
    _manage_self_capability_wrapper,
    _list_self_capabilities_wrapper,
    _read_self_action_history_wrapper,
)
from core.tools.life_media_definitions import (
    _play_song_wrapper,
    _read_toy_file_wrapper,
    _write_toy_file_wrapper,
    _reread_image_wrapper,
    _life_records_wrapper,
)
from core.tool_runtime.exposure import (
    get_tools_schema,
    _has_open_object,
    format_mcp_opaque_params_note,
    _build_probe_prompt,
    get_probe_prompt,
    get_tool_loop_relay_prompt,
)
from core.tool_runtime.policy import (
    _require_memory_read_scope,
    get_tool_effect,
    is_side_effect_tool,
    tool_fast_path_risk,
    _is_tool_enabled,
    intiface_opted_in,
    mcp_domain_selector_allows,
    _effective_tool_loop_enabled,
    tool_loop_active,
)
from core.tool_runtime.validation import (
    _schema_value_matches,
    _schema_errors,
)
from core.tool_runtime.outcome import (
    _log_execute_failure_context,
    _mcp_server_reported_error,
    ToolExecutionOutcome,
    _execution_outcome,
)
from core.tool_runtime.execution import (
    _execute_structured_impl,
)

logger = logging.getLogger(__name__)


from core.tool_runtime.context import _active_char_id


from core.tool_runtime.registry import _TOOL_REGISTRY


_send_callback: Callable | None = None


_TOOL_FALLBACKS = {
    "weather": "天气信息暂时获取不到",
    "web_search": "网络暂时有些不稳定，没搜到",
    "get_time": "时间获取出了点问题",
    "add_reminder": "备忘录暂时写不进去，稍后再试",
    "list_reminders": "备忘录暂时读不到",
    "get_reminder": "备忘录暂时读不到",
    "update_reminder": "备忘录暂时改不了，稍后再试",
    "cancel_reminder": "备忘录暂时取消不了，稍后再试",
    "restore_reminder": "备忘录暂时恢复不了，稍后再试",
    "read_diary": "日记暂时读不到",
    "read_watch": "身体数据暂时读取不到",
    "read_hds_heart_rate": "HDS 心率状态暂时读取不到",
    "search_documents": "资料暂时检索不到",
    "read_document": "资料暂时读取不到",
    "search_character_notes": "角色手记暂时检索不到",
}


def register_send_callback(callback: Callable):
    """注入发送消息的回调函数（由 main.py 在初始化时调用）"""
    global _send_callback
    _send_callback = callback


_SCOPED_MEMORY_READ_TOOLS: frozenset[str] = frozenset({
    "get_profile", "get_episodic", "read_long_user_message", "search_events", "expand_event_window", "get_related_events",
    "search_documents", "read_document", "search_character_notes",
    "search_memory_dossiers", "read_memory_dossier", "search_dossier_events",
    "get_memory_consolidation_status",
})


from core.tools.devices_definitions import _ACTION_TARGETS


from core.tools.devices_definitions import _agent_session_id


_INTIFACE_TOOL_NAMES: frozenset[str] = frozenset({
    "toy_vibrate",
    "toy_stop",
    "toy_pattern",
    "toy_job_status",
})


_SIDE_EFFECT_TOOLS: frozenset[str] = frozenset({
    # desktop 控制类 —— 会向桌宠端推送外部动作
    "desktop_minimize",
    "desktop_open_url",
    "desktop_play_pause",
    "desktop_notify",
    "play_song",
    "toy_vibrate",
    "toy_stop",
    "toy_pattern",
    "write_toy_file",
    # 写状态的工具
    "add_reminder",
    "update_reminder",
    "cancel_reminder",
    "restore_reminder",
    "water_garden",
    "self_create",
    "self_update",
    "self_move",
    "self_delete",
    "self_restore",
    "write_track_note",
    "choose_next_track",
    "exit_yandere",
    "forget_episodic",
    "clear_midterm",
    # system 类（dangerous=True，冗余标注，保证兜底）
    "device_shutdown",
    "device_sleep",
})


_MCP_DOC_DESCRIPTION_HINTS: tuple[str, ...] = (
    "documentation", "document", "help", "usage", "schema", "parameter", "option", "describe",
    "说明", "文档", "参数", "选项", "玩法", "规则", "操作",
)


_EXECUTE_ALLOWED_ORIGINS: frozenset[str] = frozenset({
    "user_live", "assistant_loop", "assistant_loop_relay", "autonomy_loop", "admin_console",
    "assistant_self_management", "autonomy_self_management",
})


_OWNER_ONLY_HARDWARE_TOOLS: frozenset[str] = frozenset({
    "toy_vibrate",
    "toy_stop",
    "toy_pattern",
    "toy_job_status",
})


_MCP_SERVER_ERROR_PREFIX = "MCP 工具返回错误: "


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
        _execute_structured_impl,
        tool_name, tool_args, user_id, target_id, is_group, session_state,
        origin=origin,
        char_id=char_id,
        bypass_read_log=bypass_read_log,
        tool_status_observer=tool_status_observer,
        allowed_tool_names=allowed_tool_names,
        pin_reference_hashes=pin_reference_hashes,
    )
    if origin in _EXECUTE_ALLOWED_ORIGINS:
        from core.tool_audit import record as record_tool_audit
        record_tool_audit(user_id, char_id, tool=tool_name,
                          args={} if tool_name in _gmail_tools.NAMES | _intent_tools.NAMES | _device_file_tools.NAMES else tool_args,
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
