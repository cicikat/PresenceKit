"""Tool policy; called through the stable dispatcher facade."""

def _require_memory_read_scope(user_id: str, char_id: str) -> None:
    """Reject a memory read before a wrapper can fall back to another bucket."""
    from core import tool_dispatcher as _runtime
    from core.memory.scope import MemoryScope
    from core.sandbox import safe_user_id

    MemoryScope.reality_scope(safe_user_id(user_id), char_id)


def get_tool_effect(tool_name: str) -> str:
    """Return an explicitly registered external effect, if this tool has one."""
    from core import tool_dispatcher as _runtime
    effect = _runtime._TOOL_REGISTRY.get(tool_name, {}).get("effect")
    return effect if effect in {"read", "write", "actuate", "emergency"} else ""


def is_side_effect_tool(tool_name: str) -> bool:
    """返回该工具是否有副作用（会写状态或向外部发动作）。

    优先复用 registry 中的 dangerous=True 标记，
    再对照 _SIDE_EFFECT_TOOLS 白名单兜底。
    """
    from core import tool_dispatcher as _runtime
    effect = _runtime.get_tool_effect(tool_name)
    if effect:
        return effect != "read"
    from core.deployment_capabilities import tool_allowed
    allowed, reason = tool_allowed(tool_name)
    if not allowed:
        return reason
    spec = _runtime._TOOL_REGISTRY.get(tool_name, {})
    if spec.get("dangerous", False):
        return True
    return tool_name in _runtime._SIDE_EFFECT_TOOLS


def tool_fast_path_risk(tool_name: str) -> str:
    """返回工具在快速路径下的风险等级字符串。

    "high" — 有副作用（写状态 / 控制外部 / 媒体控制 / 提醒 / 花园等）
    "low"  — 纯读类（时间 / 天气 / 搜索 / 日记阅读等）
    """
    from core import tool_dispatcher as _runtime
    return "high" if _runtime.is_side_effect_tool(tool_name) else "low"


def _is_tool_enabled(tool_name: str) -> bool:
    """检查 config.yaml tools 配置中工具是否启用（默认启用）。
    优先查 tools.<tool_name>.enabled，再回退到旧的 group 键。
    """
    from core import tool_dispatcher as _runtime
    if tool_name in _runtime._QZONE_TOOL_NAMES:
        from core.qzone_service import settings as qzone_settings
        try:
            cfg = qzone_settings()
            if not cfg.enabled or not cfg.account_id or not cfg.allowed_char_ids:
                return False
            if _runtime._TOOL_REGISTRY[tool_name].get("effect") == "write" and not cfg.write_enabled:
                return False
        except (ValueError, TypeError):
            return False
    if tool_name in _runtime._computer_use.OWNER_TOOL_NAMES and not _runtime._computer_use.enabled():
        return False
    if tool_name == "start_agent_task":
        from core.agent_runtime.agent_tasks import capability_snapshot
        state = capability_snapshot()
        return bool(state["configured"] and state["workspace_status"] == "enabled")
    if tool_name == "read_xiaohongshu":
        from core.tools.xiaohongshu import settings
        return settings(_runtime.get_config())["effective"]
    if tool_name == "observe_video_call_camera":
        from core.video_call import TOOL_PURPOSE, connection_state
        if not connection_state(_runtime.get_config(), TOOL_PURPOSE)["effective"]:
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
    if tool_name in _runtime._INTIFACE_TOOL_NAMES and not _runtime.intiface_opted_in():
        return False
    if tool_name in (
        "get_listening_state", "get_listening_queue", "get_listening_history",
        "get_track_note", "write_track_note", "choose_next_track",
    ):
        from core.player_adapter import music_control_enabled
        if not music_control_enabled():
            return False
    cfg = _runtime.get_config().get("tools", {})
    if tool_name in {'continue_xiaohongshu_comments','read_xiaohongshu_images','read_xiaohongshu_author_posts'} and not _runtime._is_tool_enabled('read_xiaohongshu'):
        return False
    if tool_name in cfg:
        v = cfg[tool_name]
        if isinstance(v, dict):
            return v.get("enabled", True)
        return bool(v)
    group = tool_name
    if tool_name in ("device_shutdown", "device_sleep"):
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
    from core import tool_dispatcher as _runtime
    hardware = (_runtime.get_config() or {}).get("hardware", {})
    return bool(isinstance(hardware, dict) and hardware.get("intiface_opt_in") is True)


def mcp_domain_selector_allows(info: dict) -> bool:
    """Apply an optional per-server MCP domain selector as a narrowing-only gate."""
    from core import tool_dispatcher as _runtime
    if info.get("category") != "mcp":
        return True
    server_name = str(info.get("mcp_server") or "")
    cfg = _runtime.get_config() or {}
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
        _runtime.logger.warning(
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
    from core import tool_dispatcher as _runtime
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
    from core import tool_dispatcher as _runtime
    cfg = _runtime.get_config().get("tool_loop", {})
    if not _runtime._effective_tool_loop_enabled(bool(cfg.get("enabled", False))):
        return False
    owner_id = str(_runtime.get_config().get("scheduler", {}).get("owner_id") or "")
    if not owner_id or str(uid) != owner_id:
        return False
    from core.model_registry import get_model_client
    try:
        mc = get_model_client("chat")
    except Exception:
        return False
    return mc.tool_call_mode == "function_calling"
