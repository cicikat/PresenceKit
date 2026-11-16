"""Tool exposure; called through the stable dispatcher facade."""

def get_tools_schema(
    categories: list[str] | None = None,
    *,
    char_id: str | None = None,
    uid: str | None = None,
    only_names: frozenset[str] | None = None,
    autonomy: bool = False,
) -> list[dict]:
    """返回已启用工具的 OpenAI function_calling 格式 schema。
    categories: 若提供，仅返回该分类内的工具；None 返回全部。
    """
    from core import tool_dispatcher as _runtime
    char_name = _runtime.get_active_char_name()
    from core.prompt_language import tool_description, tool_parameters
    schemas = []
    for name, info in _runtime._TOOL_REGISTRY.items():
        if only_names is not None and name not in only_names:
            continue
        if info.get('autonomy_only') and not autonomy:
            continue
        if name in _runtime._computer_use.OWNER_TOOL_NAMES and (not _runtime._computer_use.enabled() or not _runtime._computer_use.owner_scope(uid, char_id)):
            continue
        if name in _runtime._device_file_tools.NAMES and (autonomy or not _runtime._device_file_tools.service.allowed(uid, char_id)):
            continue
        if name in _runtime._gmail_tools.NAMES and not _runtime._gmail_tools.allowed(name, uid, char_id):
            continue
        if name in _runtime._QZONE_TOOL_NAMES:
            from core.qzone_service import allowed
            if not allowed(uid, char_id, write=info.get("effect") == "write"):
                continue
        if name == "invite_video_call" and char_id is not None:
            settings = _runtime.get_config().get("tools", {}).get(name) or {}
            allowed = settings.get("allowed_char_ids") or [] if isinstance(settings, dict) else []
            if char_id not in allowed:
                continue
        if info.get("self_management") or info.get("self_management_read"):
            # The gateway is added only by trusted agent loops.
            continue
        from core.deployment_capabilities import tool_allowed
        if not tool_allowed(name)[0]:
            continue
        if not _runtime._is_tool_enabled(name):
            continue
        if categories is not None and info.get("category") not in categories:
            continue
        if uid is not None and char_id is not None:
            from core.self_management.policy import tool_allowed
            if not tool_allowed(uid, char_id, name):
                continue
        parameters = tool_parameters(info.get("parameters") or {"type": "object", "properties": {}}, tool_name=name)
        if name == "generate_image":
            from core.image_gen import exposed_preset_names
            image_presets = exposed_preset_names(char_id)
            if not image_presets:
                continue
            parameters["properties"]["preset"]["enum"] = [preset["name"] for preset in image_presets]
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool_description(name, str(info.get("description") or "")).replace("{char}", char_name),
                "parameters": parameters,
            },
        })
    schemas = [
        schema
        for schema in schemas
        if _runtime.mcp_domain_selector_allows(
            _runtime._TOOL_REGISTRY.get(schema.get("function", {}).get("name", ""), {})
        )
    ]
    return schemas


def _has_open_object(schema) -> bool:
    """Whether JSON Schema contains an object with arbitrary nested keys."""
    from core import tool_dispatcher as _runtime
    if not isinstance(schema, dict):
        return False
    additional = schema.get("additionalProperties")
    if additional is True or isinstance(additional, dict):
        return True
    for child in (schema.get("properties") or {}).values():
        if _runtime._has_open_object(child):
            return True
    return any(_runtime._has_open_object(child) for key in ("allOf", "anyOf", "oneOf") for child in (schema.get(key) or []))


def format_mcp_opaque_params_note(tool_schemas: list[dict]) -> str:
    """Return a schema-driven tool-loop hint without assuming any MCP tool names.

    Some MCP servers expose an action-specific freeform object (often named
    ``params``) whose required nested keys are not present in JSON Schema.  The
    only safe cross-server behaviour is to avoid guessing, inspect a same-server
    *declared read-only* documentation tool when one advertises that purpose,
    and otherwise use the server's concrete error feedback.
    """
    from core import tool_dispatcher as _runtime
    opaque_by_server: dict[str, list[str]] = {}
    for schema in tool_schemas:
        function = schema.get("function") or schema
        name = function.get("name")
        info = _runtime._TOOL_REGISTRY.get(name or "", {})
        if info.get("category") != "mcp" or not _runtime._has_open_object(function.get("parameters", {})):
            continue
        server = str(info.get("mcp_server") or "")
        if server:
            opaque_by_server.setdefault(server, []).append(str(name))
    if not opaque_by_server:
        return ""

    lines = [
        "【MCP 自由参数】有些 MCP 工具的嵌套对象允许任意字段，schema 没有列出某个动作所需的全部字段。"
        "不要编造嵌套字段。若同一 server 有明确标为只读、且描述说明可解释参数/选项/操作规则的工具，"
        "先按它自己的描述查询；不要根据工具名猜测。若没有这类说明工具，就只传 schema 已知字段，"
        "并根据服务器返回的具体缺参/格式错误在下一步修正。"
    ]
    for server, names in opaque_by_server.items():
        exposed_names = {(schema.get("function") or schema).get("name") for schema in tool_schemas}
        doc_tools = [
            candidate_name
            for candidate_name, info in _runtime._TOOL_REGISTRY.items()
            if candidate_name in exposed_names and info.get("category") == "mcp"
            and info.get("mcp_server") == server
            and info.get("mcp_read_only") is True
            and any(hint in str(info.get("description") or "").casefold() for hint in _runtime._MCP_DOC_DESCRIPTION_HINTS)
        ]
        if doc_tools:
            lines.append(
                f"server {server} 的自由参数工具：{'、'.join(names)}；可查阅的只读说明工具：{'、'.join(doc_tools)}。"
            )
        else:
            lines.append(f"server {server} 的自由参数工具：{'、'.join(names)}；未发现明确标注的只读说明工具。")
    return "\n".join(lines)


def _build_probe_prompt(
    categories,
    *,
    location: str | None = None,
    relay: bool = False,
    allowed_tool_names: set[str] | frozenset[str] | None = None,
) -> str:
    """探针类 prompt 的共享构建逻辑。

    被 get_probe_prompt()（Path A，默认 info/desktop）与
    get_tool_loop_relay_prompt()（Brief 120·工具循环二次调用兜底，categories 参数化）
    共同调用。不要在 get_probe_prompt 里加 if 分支——两者未来措辞可能分叉，分开维护
    避免探针本身的 prompt 被这次改动污染。
    """
    from core import tool_dispatcher as _runtime
    lines = ["只输出工具调用或空字符串，不要任何其他文字或思考过程。"]
    if relay:
        lines.append(
            "你作为工具调度器。下面这段话是模型自己已标注为需要调用工具的干净意图文本，"
            "判断它对应哪个工具、什么参数。"
        )
    else:
        lines.append("你作为工具调度器。根据用户消息判断是否调用工具。")
    if location:
        lines.append(f"用户位置：{location}。")
    lines.append(
        "【工具使用原则】"
        "工具是获取事实或执行操作的手段，不是表达情绪的方式。"
        "只有当用户明确提出需要读取、查询、搜索、打开、提醒、发送、生成、修改、读日记相关等与工具有关的操作时，才考虑调用工具。"
        "不要为了显得主动而调用工具。"
        "不要用工具结果替代陪伴回应；工具结果只能补充事实。"
        "\n可用工具："
    )
    char_name = _runtime.get_active_char_name()
    for name, spec in _runtime._TOOL_REGISTRY.items():
        if spec.get("probe") is False:
            continue
        if name in _runtime._device_file_tools.NAMES:
            continue
        if name in _runtime._computer_use.OWNER_TOOL_NAMES and allowed_tool_names is None:
            continue
        if allowed_tool_names is not None and name not in allowed_tool_names:
            continue
        from core.deployment_capabilities import tool_allowed
        if not tool_allowed(name)[0]:
            continue
        if spec.get("category") not in categories:
            continue
        examples = spec.get("examples", [])
        desc = spec.get("description", "").replace("{char}", char_name)
        example_str = " / ".join(examples) if examples else "（无示例）"
        lines.append(f"- {name}: {desc}\n  触发例句: {example_str}")
    lines.append("\n以上都不符合 → 输出空字符串，不调用任何工具")
    return "\n".join(lines)


def get_probe_prompt(
    location: str,
    *,
    categories: tuple[str, ...] | list[str] = ("info", "desktop"),
    allowed_tool_names: set[str] | frozenset[str] | None = None,
) -> str:
    """动态从注册表构建探针 prompt，默认保持 desktop 的 info/desktop 暴露面。"""
    from core import tool_dispatcher as _runtime
    return _runtime._build_probe_prompt(
        categories,
        location=location,
        allowed_tool_names=allowed_tool_names,
    )


def get_tool_loop_relay_prompt(
    categories: list[str],
    *,
    allowed_tool_names: set[str] | frozenset[str] | None = None,
) -> str:
    """Brief 120·工具循环二次调用兜底专用 prompt。

    解析模型自己在自然语言回复末尾用 ``{true: 意图}`` 标注过的干净意图文本，判断对应
    哪个工具、什么参数。categories 覆盖 run_agentic_loop 当前实际暴露给这一轮的
    tool_loop.categories（含 mcp），而不是探针默认的 info/desktop 两类——否则钓鱼/
    海龟汤这类 mcp 工具还是够不着。
    """
    from core import tool_dispatcher as _runtime
    return _runtime._build_probe_prompt(
        categories,
        relay=True,
        allowed_tool_names=allowed_tool_names,
    )
