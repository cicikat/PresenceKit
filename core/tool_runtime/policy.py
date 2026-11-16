"""Tool policy."""

from core.tools.qzone_tools import _QZONE_TOOL_NAMES
from core.tool_runtime.state import _TOOL_REGISTRY
from core.tools import gmail_tools as _gmail_tools, device_file_tools as _device_file_tools
from core.agent_runtime import computer_use as _computer_use
from core import config_loader
import logging
logger = logging.getLogger("core.tool_dispatcher")

_EXECUTE_ALLOWED_ORIGINS: frozenset[str] = frozenset({
    "user_live", "assistant_loop", "assistant_loop_relay", "autonomy_loop", "admin_console",
    "assistant_self_management", "autonomy_self_management",
})

_INTIFACE_TOOL_NAMES: frozenset[str] = frozenset({
    "toy_vibrate",
    "toy_stop",
    "toy_pattern",
    "toy_job_status",
})

_OWNER_ONLY_HARDWARE_TOOLS: frozenset[str] = frozenset({
    "toy_vibrate",
    "toy_stop",
    "toy_pattern",
    "toy_job_status",
})

_SCOPED_MEMORY_READ_TOOLS: frozenset[str] = frozenset({
    "get_profile", "get_episodic", "read_long_user_message", "search_events", "expand_event_window", "get_related_events",
    "search_documents", "read_document", "search_character_notes",
    "search_memory_dossiers", "read_memory_dossier", "search_dossier_events",
    "get_memory_consolidation_status",
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


def _require_memory_read_scope(user_id: str, char_id: str) -> None:
    """Reject a memory read before a wrapper can fall back to another bucket."""
    from core.memory.scope import MemoryScope
    from core.sandbox import safe_user_id

    MemoryScope.reality_scope(safe_user_id(user_id), char_id)


def get_tool_effect(tool_name: str) -> str:
    """Return an explicitly registered external effect, if this tool has one."""
    effect = _TOOL_REGISTRY.get(tool_name, {}).get("effect")
    return effect if effect in {"read", "write", "actuate", "emergency"} else ""


def is_side_effect_tool(tool_name: str) -> bool:
    """返回该工具是否有副作用（会写状态或向外部发动作）。

    优先复用 registry 中的 dangerous=True 标记，
    再对照 _SIDE_EFFECT_TOOLS 白名单兜底。
    """
    effect = get_tool_effect(tool_name)
    if effect:
        return effect != "read"
    from core.deployment_capabilities import tool_allowed
    allowed, reason = tool_allowed(tool_name)
    if not allowed:
        return reason
    spec = _TOOL_REGISTRY.get(tool_name, {})
    if spec.get("dangerous", False):
        return True
    return tool_name in _SIDE_EFFECT_TOOLS


def tool_fast_path_risk(tool_name: str) -> str:
    """返回工具在快速路径下的风险等级字符串。

    "high" — 有副作用（写状态 / 控制外部 / 媒体控制 / 提醒 / 花园等）
    "low"  — 纯读类（时间 / 天气 / 搜索 / 日记阅读等）
    """
    return "high" if is_side_effect_tool(tool_name) else "low"


def _is_tool_enabled(tool_name: str) -> bool:
    """检查 config.yaml tools 配置中工具是否启用（默认启用）。
    优先查 tools.<tool_name>.enabled，再回退到旧的 group 键。
    """
    if tool_name in _QZONE_TOOL_NAMES:
        from core.qzone_service import settings as qzone_settings
        try:
            cfg = qzone_settings()
            if not cfg.enabled or not cfg.account_id or not cfg.allowed_char_ids:
                return False
            if _TOOL_REGISTRY[tool_name].get("effect") == "write" and not cfg.write_enabled:
                return False
        except (ValueError, TypeError):
            return False
    if tool_name in _computer_use.OWNER_TOOL_NAMES and not _computer_use.enabled():
        return False
    if tool_name == "start_agent_task":
        from core.agent_runtime.agent_tasks import capability_snapshot
        state = capability_snapshot()
        return bool(state["configured"] and state["workspace_status"] == "enabled")
    if tool_name == "read_xiaohongshu":
        from core.tools.xiaohongshu import settings
        return settings(config_loader.get_config())["effective"]
    if tool_name == "observe_video_call_camera":
        from core.video_call import TOOL_PURPOSE, connection_state
        if not connection_state(config_loader.get_config(), TOOL_PURPOSE)["effective"]:
            return False
    if tool_name == "generate_image":
        from core.image_gen import tools_enabled
        if not tools_enabled():
            return False
    if tool_name in ("observe_user_screen",):
        from core.perception.screen_observation import enabled
        if not enabled():
            return False
    if tool_name == "read_hds_heart_rate":
        from core.hds_local import character_read_enabled
        if not character_read_enabled():
            return False
    if tool_name in _INTIFACE_TOOL_NAMES and not intiface_opted_in():
        return False
    if tool_name in (
        "get_listening_state", "get_listening_queue", "get_listening_history",
        "get_track_note", "write_track_note", "choose_next_track",
    ):
        from core.player_adapter import music_control_enabled
        if not music_control_enabled():
            return False
    cfg = config_loader.get_config().get("tools", {})
    if tool_name in {'continue_xiaohongshu_comments','read_xiaohongshu_images','read_xiaohongshu_author_posts'} and not _is_tool_enabled('read_xiaohongshu'):
        return False
    if tool_name in cfg:
        v = cfg[tool_name]
        if isinstance(v, dict):
            return v.get("enabled", True)
        return bool(v)
    group = tool_name
    if tool_name in ("device_shutdown", "device_sleep"):
        from core import user_relation
        group = "device_control"
    elif tool_name == "set_timer":
        group = "timer"
    elif tool_name in (
        "add_reminder", "list_reminders", "get_reminder",
        "update_reminder", "cancel_reminder", "restore_reminder",
    ):
        group = "reminder"
    return cfg.get(group, {}).get("enabled", True)


def intiface_opted_in() -> bool:
    """Return whether the dormant Intiface reserve line was explicitly opted in."""
    hardware = (config_loader.get_config() or {}).get("hardware", {})
    return bool(isinstance(hardware, dict) and hardware.get("intiface_opt_in") is True)


def mcp_domain_selector_allows(info: dict) -> bool:
    """Apply an optional per-server MCP domain selector as a narrowing-only gate."""
    if info.get("category") != "mcp":
        return True
    server_name = str(info.get("mcp_server") or "")
    cfg = config_loader.get_config() or {}
    mcp_cfg = cfg.get("mcp_servers", {}) or {}
    server_cfg = next(
        (
            item for item in (mcp_cfg.get("servers") or [])
            if isinstance(item, dict) and str(item.get("name") or "") == server_name
        ),
        None,
    )
    if server_cfg is None or server_cfg.get("domain_selector") is None:
        return True
    try:
        from core.mcp_client import normalize_domain_selector

        selector = normalize_domain_selector(server_cfg.get("domain_selector"))
    except (ImportError, ValueError) as exc:
        logger.warning(
            "[tool_dispatcher] MCP domain_selector 无效，保持兼容暴露: server=%s error=%s",
            server_name, exc,
        )
        return True
    if selector is None:
        return True
    domains = {
        domain for domain in (info.get("mcp_domains") or []) if isinstance(domain, str)
    }
    if not domains:
        return bool(selector["include_unclassified"])
    return bool(domains.intersection(selector["domains"]))


def _effective_tool_loop_enabled(global_enabled: bool) -> bool:
    """返回活跃角色卡覆盖后的 Path C 开关。

    presence_ext.tool_loop 只接受 ``on`` / ``off``。字段缺失、角色尚未加载、或卡内容
    非法时都回落全局值，避免一张损坏的角色卡阻断正常对话。
    """
    try:
        from core import pipeline_registry

        pipeline = pipeline_registry.get()
        character = getattr(pipeline, "character", None) if pipeline is not None else None
        override = (getattr(character, "presence_ext", None) or {}).get("tool_loop")
        if override == "on":
            return True
        if override == "off":
            return False
    except Exception:
        pass
    return global_enabled


def tool_loop_active(uid: str) -> bool:
    """Brief 28/109 · Path C 总闸：有效 tool-loop 开关、owner 真实私聊与 chat preset
    的 function_calling 模式三者同时成立才为真。

    群聊 / scheduler trigger / 梦境 / Stage 的调用点在到达这个判断之前就已经走了别的分支
    （群聊在 main.py 里提前 return），本函数只需要核对 uid 是否为 owner。
    main.py 与 admin/routers/chat.py 用同一个 helper 判断"是否跳过探针"和"是否走
    run_agentic_loop"，两处判断必须一致，故抽在这里而不是各自内联。
    """
    cfg = config_loader.get_config().get("tool_loop", {})
    if not _effective_tool_loop_enabled(bool(cfg.get("enabled", False))):
        return False
    owner_id = str(config_loader.get_config().get("scheduler", {}).get("owner_id") or "")
    if not owner_id or str(uid) != owner_id:
        return False
    from core.model_registry import get_model_client
    try:
        mc = get_model_client("chat")
    except Exception:
        return False
    return mc.tool_call_mode == "function_calling"


from dataclasses import dataclass
from core.tool_runtime.outcome import _execution_outcome


@dataclass(frozen=True)
class InvocationScope:
    user_id: str
    char_id: str
    origin: str
    is_group: bool


def early_admission(tool_name, tool_args, tool_info, scope: InvocationScope, _trace):
    user_id, char_id, origin, is_group = scope.user_id, scope.char_id, scope.origin, scope.is_group
    if tool_name in {"list_self_capabilities", "read_self_action_history"}:
        from core.self_management.policy import feature_enabled
        if is_group or origin not in {"assistant_loop", "autonomy_loop"} or not feature_enabled():
            return _execution_outcome("tool_failed", "Self Capability history is unavailable in this context.")

    if tool_name in {"get_ambient_state", "generate_image", "read_long_user_message", "search_events", "expand_event_window", "get_related_events", "search_memory_dossiers", "read_memory_dossier", "search_dossier_events", "update_memory_dossier", "get_memory_consolidation_status", "request_memory_consolidation", "read_life_records", "reread_image", "write_artifact", "update_artifact", "read_artifact", "list_artifacts", "self_db_tables", "self_db_create_table", "self_db_insert", "self_db_query", "self_db_update", "self_db_delete", "self_db_drop_table", "self_tool_define", "self_tool_list", "self_tool_run"} and is_group:
        _trace("failed", "reality_event_tools_forbidden_in_group")
        return _execution_outcome("tool_failed")

    if tool_name == "backfill_diary":
        owner_id = str(config_loader.get_config().get("scheduler", {}).get("owner_id") or "")
        if (
            is_group
            or origin not in {"user_live", "assistant_loop", "assistant_loop_relay"}
            or not owner_id
            or str(user_id) != owner_id
        ):
            return _execution_outcome("tool_failed", "补写日记仅允许在用户私聊请求中执行。")

    if tool_name in _QZONE_TOOL_NAMES:
        from core.qzone_service import allowed, autonomy_tool_allowed
        if any(key in tool_args for key in ("user_id", "uid", "char_id", "owner", "realm")):
            _trace("failed", "grant_principal_mismatch")
            return _execution_outcome("tool_failed", "grant_principal_mismatch")
        if (is_group or origin not in {"user_live", "assistant_loop", "assistant_loop_relay", "autonomy_loop"}
                or (origin == "autonomy_loop" and not autonomy_tool_allowed(tool_name, user_id, char_id))
                or not principal_allowed(tool_name, tool_info, user_id, char_id)):
            _trace("failed", "qzone_not_authorized")
            return _execution_outcome("tool_failed", "QQ 空间未授权给当前会话/角色。")

    return None


def execution_admission(tool_name, tool_args, tool_info, scope: InvocationScope, _trace):
    user_id, char_id, origin, is_group = scope.user_id, scope.char_id, scope.origin, scope.is_group
    if tool_info.get("autonomy_only") and (is_group or origin != "autonomy_loop"):
        return _execution_outcome("tool_failed", "intent_context_required")
    if tool_name in _device_file_tools.NAMES and (
        is_group or origin not in {"assistant_loop", "assistant_loop_relay"}
        or not principal_allowed(tool_name, tool_info, user_id, char_id)
    ):
        return _execution_outcome("tool_failed", "device_file_denied")
    if tool_name in _gmail_tools.NAMES and (
        is_group or origin not in {"assistant_loop", "assistant_loop_relay", "autonomy_loop"}
        or not principal_allowed(tool_name, tool_info, user_id, char_id)
    ):
        return _execution_outcome("tool_failed", "gmail_not_authorized")
    if tool_name in _computer_use.OWNER_TOOL_NAMES:
        if (is_group or origin not in {"user_live", "assistant_loop", "assistant_loop_relay"}
                or not principal_allowed(tool_name, tool_info, user_id, char_id)):
            return _execution_outcome("tool_failed", "当前会话无法操作桌面。")
    if tool_name in _INTIFACE_TOOL_NAMES and not intiface_opted_in():
        _msg = "Intiface 硬件能力当前处于冻结状态，需要显式 opt-in"
        _trace("failed", _msg)
        return _execution_outcome("tool_failed", _msg)
    if tool_info.get("self_management"):
        if origin not in {"assistant_self_management", "autonomy_self_management"}:
            _trace("failed", "self-management origin rejected")
            return _execution_outcome("tool_failed", "这项自主管理能力不能在当前上下文中调用。")
    elif tool_info.get("self_management_read"):
        if origin not in {"assistant_loop", "autonomy_loop"}:
            return _execution_outcome("tool_failed", "Self Capability read is unavailable in this context.")
    else:
        if origin in {"assistant_self_management", "autonomy_self_management"}:
            _trace("failed", "self-management origin may not execute a business tool")
            return _execution_outcome("tool_failed", "该管理调用只能修改自身能力，不能执行普通工具。")
        from core.self_management.policy import tool_allowed
        if not tool_allowed(user_id, char_id, tool_name):
            _trace("failed", "self capability disabled")
            return _execution_outcome("tool_failed", "这项能力尚未对当前角色开放。")

    if not _is_tool_enabled(tool_name):
        _fail = get_tool_fail_response()
        _trace("failed", _fail)
        return _execution_outcome("tool_failed", _fail)

    return None


def parameter_principal_admission(tool_name, tool_args, tool_info, scope: InvocationScope, _trace):
    user_id, char_id, origin, is_group = scope.user_id, scope.char_id, scope.origin, scope.is_group
    if tool_name in {
        "get_ambient_state", "get_listening_state", "get_listening_queue", "get_listening_history",
        "get_track_note", "write_track_note", "choose_next_track",
    }:
        for key in ("user_id", "uid", "char_id", "owner"):
            if key in tool_args:
                _trace("failed", "grant_principal_mismatch")
                return _execution_outcome("tool_failed", "grant_principal_mismatch")
    return None


def permission_admission(tool_name, tool_args, tool_info, scope: InvocationScope, _trace):
    user_id, char_id, origin, is_group = scope.user_id, scope.char_id, scope.origin, scope.is_group
    if tool_name in _OWNER_ONLY_HARDWARE_TOOLS:
        owner_id = str(config_loader.get_config().get("scheduler", {}).get("owner_id") or "")
        if is_group or not owner_id or str(user_id) != owner_id:
            logger.warning(
                "[tool_dispatcher.execute] 拒绝硬件控制: 非 owner 私聊, user_id=%s is_group=%s tool=%s",
                user_id, is_group, tool_name,
            )
            _msg = "硬件控制只允许 owner 私聊触发"
            _trace("failed", _msg)
            return _execution_outcome("tool_failed", _msg)

    if tool_name in ("device_shutdown", "device_sleep"):
        from core import user_relation
        if not user_relation.has_permission(user_id, "agent_control"):
            _msg = "你没有执行此操作的权限哦"
            _trace("failed", _msg)
            return _execution_outcome("tool_failed", _msg)

    return None


def principal_allowed(tool_name, info, uid, char_id, *, autonomy=False):
    """Shared principal/grant boundary; origin/group restrictions are additional."""
    if tool_name in _computer_use.OWNER_TOOL_NAMES:
        return _computer_use.owner_scope(uid, char_id)
    if tool_name in _device_file_tools.NAMES:
        return not autonomy and _device_file_tools.service.allowed(uid, char_id)
    if tool_name in _gmail_tools.NAMES:
        return _gmail_tools.allowed(tool_name, uid, char_id)
    if tool_name in _QZONE_TOOL_NAMES:
        from core.qzone_service import allowed
        return allowed(uid, char_id, write=info.get("effect") == "write")
    return True
