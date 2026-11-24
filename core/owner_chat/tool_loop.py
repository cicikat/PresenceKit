"""Orchestrate a tool-enabled generation turn through existing model and tool adapters."""

import asyncio
import json
import logging
import time
import uuid

from core.prompt_language import text as _pt

logger = logging.getLogger("core.pipeline")
_TOOL_EPHEMERAL_WAITING_AFTER_S = 3.0


def _drop_relay_nudge(messages: list[dict]) -> list[dict]:
    """Closing, tool-free generation must not still ask for `{true}` / `{false}`."""
    return [m for m in messages if m.get("_layer") != "11.5_tool_nudge"]


def _voice_reanchor(char_id: str) -> str:
    """Brief 28 · tool loop 收尾锚定：工具轮之后主生成容易滑进"报告腔"，
    用这条静态 system 提示把声音收回角色本身。char_name 走 get_char_name()，
    禁字面角色名（硬性规则8）。"""
    from core.character_name_provider import get_char_name
    char_name = get_char_name(char_id)
    return (
        f"工具用完了。接下来只以{char_name}的声音回复，"
        "把查到的东西揉进你自己的话里，不要报告腔、不要罗列。"
    )


from core.tool_activity import display_chain
from core.llm_turn_route import scoped_tool_turn


class ToolTurnRunner:
    def __init__(self, *, run_llm, run_llm_stream, guard_tool_meta_leak,
                 guard_unverified_claim, anti_collapse_prefix_retry):
        callbacks = (run_llm, run_llm_stream, guard_tool_meta_leak,
                     guard_unverified_claim, anti_collapse_prefix_retry)
        if not all(callable(callback) for callback in callbacks):
            raise TypeError("ToolTurnRunner requires all five generation capabilities")
        self._run_llm = run_llm
        self._run_llm_stream = run_llm_stream
        self._guard_tool_meta_leak = guard_tool_meta_leak
        self._guard_unverified_claim = guard_unverified_claim
        self._anti_collapse_prefix_retry = anti_collapse_prefix_retry
        self.waiting_after_s = _TOOL_EPHEMERAL_WAITING_AFTER_S

    @display_chain
    @scoped_tool_turn
    async def run(
        self,
        messages: list[dict],
        *,
        uid: str,
        char_id: str,
        session_state,
        is_group: bool = False,
        stream: bool = False,
        is_proactive: bool = False,
        exclude_tools: set[str] | None = None,
        tool_event_observer=None,
        tool_call_required: bool = False,
        required_tool_names: list[str] | set[str] | tuple[str, ...] = (),
        allowed_tool_categories: frozenset[str] | None = None,
        allowed_tool_names: frozenset[str] | None = None,
        media_refs: list[dict] | None = None,
    ):
        """主生成多步调用工具再回答，只在 tool_dispatcher.tool_loop_active(uid) 为真时被调用。

        stream=False 时返回 str；stream=True 时返回 async generator（仅最终答案流式，
        工具决策步骤全程非流式——chat_turn 需要 tools 参数，与 chat_stream 的既有约束冲突）。

        origin 固定传 "assistant_loop"（tool_dispatcher._EXECUTE_ALLOWED_ORIGINS 白名单项）。
        is_proactive: 本次是否 scheduler 主动消息（Brief 32）。
        tool_event_observer: 可选 UI-only 瞬态状态观察者，不进入 turn sink 或任何记忆路径。
        """
        run_llm = self._run_llm
        run_llm_stream = self._run_llm_stream
        guard_tool_meta_leak = self._guard_tool_meta_leak
        guard_unverified_claim = self._guard_unverified_claim
        anti_collapse_prefix_retry = self._anti_collapse_prefix_retry
        waiting_after_s = self.waiting_after_s
        from core import llm_client, thinking
        from core.config_loader import get_config
        from core.error_handler import log_error
        from core.tools.chat_artifacts import begin_turn_collection
        # Owner chat already starts collection; QQ / scheduler Path C still needs it.
        begin_turn_collection()
        from core.tool_dispatcher import (
            ToolExecutionOutcome,
            execute_structured,
            format_mcp_opaque_params_note,
            get_tools_schema,
            get_tool_loop_relay_prompt,
            parse_tail_brace,
            _TOOL_REGISTRY,
        )
        from core.tools.tool_result import frame_tool_message
        from core.tool_grounding import grounding_message, required_from_messages
        from core.tool_ephemeral import DEFAULT_TTL_S, ToolEphemeralEvent, notify as _notify_tool_event

        # Brief 82 · 决策 7：本轮最后一条用户消息命中显式重读短语时，放行本轮全部
        # persist 工具已读指纹（多步调用复用同一次探测结果，短语与工具目标同轮）。
        from core.memory.tool_read_log import detect_bypass_intent as _detect_bypass_reread
        _last_user_text = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""
        )
        from core.tools.drinking import bind_turn as _bind_drinking_turn
        _drinking_invited = _bind_drinking_turn(
            _last_user_text, uid=uid, char_id=char_id, is_group=is_group, is_proactive=is_proactive,
        )
        _bypass_read_log = _detect_bypass_reread(
            _last_user_text if isinstance(_last_user_text, str) else ""
        )

        # Brief 32：monologue 注入只做一次（首步前），loop 内逐步复用同一份 loop_msgs，
        # 之后每步的 chat_turn() 不会重复触发（native 路线则每步都在 chat_turn 内部叠加）。
        messages = await thinking.maybe_apply(
            messages, call_category="chat", char_id=char_id, is_proactive=is_proactive,
        )

        cfg = get_config().get("tool_loop", {})
        max_steps = int(cfg.get("max_steps", 5))
        total_timeout_s = float(cfg.get("total_timeout_s", 300))
        # Path C exposure is shared across all owner-facing channels.  The
        # resolver preserves the legacy presence_ext.tool_categories and
        # tool_loop.categories fallbacks while adding explicit path-local
        # categories/tools/excludes.
        from core.tool_exposure import filter_schemas as _filter_exposure, resolve as _resolve_exposure
        from core.agent_runtime import computer_use as _computer_use
        from core.tools.device_file_tools import NAMES as device_file_tools
        _exposure = _resolve_exposure("path_c", char_id=char_id)
        from core.tool_exposure import expand_legacy_categories as _expand_cats
        categories = _expand_cats(allowed_tool_categories) if allowed_tool_categories is not None else list(_exposure.categories)
        excluded_tool_names = set(_exposure.exclude_tools)
        excluded_tool_names.update(exclude_tools or ())
        pin_tool = "pin_character_image_reference"
        pin_hashes = frozenset(
            ref["sha256"] for ref in (media_refs or ())
            if isinstance(ref, dict) and ref.get("kind") == "image"
            and isinstance(ref.get("sha256"), str) and ref["sha256"]
        ) if not (is_group or is_proactive) else frozenset()
        if not pin_hashes:
            excluded_tool_names.add(pin_tool)
        if is_group or is_proactive:
            excluded_tool_names.update(_computer_use.OWNER_TOOL_NAMES | device_file_tools)
        if not _drinking_invited:
            excluded_tool_names.add("drink_with_user")

        # Keep the registry helper's long-standing call shape for test/plugin
        # compatibility; the existing exposure filters are applied here.
        try:
            _raw_tools = get_tools_schema(categories=categories, char_id=char_id, uid=uid)
        except TypeError:
            # Older local extensions/tests still expose the categories-only
            # helper.  The character and uid gates remain enforced by the
            # filters below when the richer signature is available.
            _raw_tools = get_tools_schema(categories=categories)
        tools = [
            t for t in _filter_exposure(_raw_tools, _exposure)
            if (t.get("function") or t).get("name") not in excluded_tool_names
        ]
        from core.tools.gmail_tools import NAMES as gmail_tools
        if is_group:
            tools = [tool for tool in tools if (tool.get("function") or tool).get("name") not in gmail_tools]
        caller_allowed_tool_names = allowed_tool_names
        if caller_allowed_tool_names is not None:
            tools = [
                tool for tool in tools
                if (tool.get("function") or tool).get("name") in caller_allowed_tool_names
            ]
        # This internal gateway is not part of the ordinary registry exposure.
        # It is appended only when this character has at least one mutable,
        # currently available user grant.
        self_management_context = None
        try:
            from core.self_management.service import view as _self_management_view
            from core.self_management.policy import can_agent_manage as _can_agent_manage, feature_enabled as _self_management_enabled
            _self_management_state = _self_management_view(uid, char_id) if _self_management_enabled() else {"capabilities": []}
            _rows = _self_management_state.get("capabilities", [])
            _mutable_rows = [row for row in _rows if _can_agent_manage(uid, char_id, row["capability_id"])[0]]
            _gateway_name = "manage_self_capability"
            _gateway_allowed_by_call = (
                _gateway_name not in excluded_tool_names
                and (
                    caller_allowed_tool_names is None
                    or _gateway_name in caller_allowed_tool_names
                )
            )
            if _mutable_rows and _gateway_allowed_by_call:
                _info = _TOOL_REGISTRY[_gateway_name]
                tools.append({"type": "function", "function": {"name": "manage_self_capability", "description": _info["description"], "parameters": _info["parameters"]}})
                self_management_context = {
                    "revision": _self_management_state.get("revision", 0),
                    "capabilities": [{"id": row["capability_id"], "current": row.get("agent_selected_state"), "effective": row.get("effective"), "effective_value": row.get("effective_value"), "constraints": (row.get("grant") or {}).get("constraints", {})} for row in _mutable_rows],
                }
            if _self_management_enabled():
                for _read_name in ("list_self_capabilities", "read_self_action_history"):
                    if (_read_name not in excluded_tool_names
                            and (caller_allowed_tool_names is None or _read_name in caller_allowed_tool_names)):
                        _info = _TOOL_REGISTRY[_read_name]
                        tools.append({"type": "function", "function": {
                            "name": _read_name, "description": _info["description"],
                            "parameters": _info["parameters"],
                        }})
        except Exception:
            logger.warning("[pipeline.run_agentic_loop] self-management schema unavailable", exc_info=True)
        # A named tool preset is a final, model-specific *exposure* filter.  It
        # cannot bypass category, per-character, MCP policy, or global
        # execution gates above; it only makes this model receive fewer schemas.
        from core.model_registry import _get_preset_config, _resolve_preset_name
        from core.tool_presets import resolve_tool_allowlist

        model_presets = _get_preset_config()
        chat_preset_name = _resolve_preset_name("chat", char_id=char_id)
        model_preset = model_presets.get("presets", {}).get(chat_preset_name, {})
        preset_allowed_tool_names, applied_tool_preset = resolve_tool_allowlist(cfg, model_preset)
        if preset_allowed_tool_names is not None:
            tools = [
                tool for tool in tools
                # MCP is configured by its own server lifecycle/allowlist.
                # The built-in tool page must not turn currently connected
                # dynamic entries into an incomplete, persistent MCP catalogue.
                if _TOOL_REGISTRY.get((tool.get("function") or tool).get("name", ""), {}).get("category") == "mcp"
                or (tool.get("function") or tool).get("name") in preset_allowed_tool_names
            ]
            logger.info(
                "[pipeline.run_agentic_loop] applied tool preset=%r model_preset=%r exposed=%d",
                applied_tool_preset, chat_preset_name, len(tools),
            )
        from core.tool_discovery import ToolDiscovery, PREFIX as _discovery_prefix
        _final_tool_names = {(tool.get("function") or tool).get("name") for tool in tools}
        if "manage_self_capability" not in _final_tool_names:
            self_management_context = None
        from core.runtime_signal_observability import record as _record_discovery
        _discovery_notes: dict[str, str] = {}
        try:
            from core.self_tool_recipes import LISTED_RECIPE_NAMES, recipe_names
            _own_tools = recipe_names(uid, char_id, limit=LISTED_RECIPE_NAMES)
            if _own_tools:
                _discovery_notes["self"] = "你做过的工具：" + "、".join(_own_tools) + "（用 self_tool_run 调用）。"
        except Exception:
            logger.debug("[pipeline.run_agentic_loop] self tool names unavailable", exc_info=True)
        try:
            from core.image_gen import discovery_note as _image_gen_note
            _media_note = _image_gen_note(char_id)
            if _media_note:
                _discovery_notes["media"] = _media_note
        except Exception:
            logger.debug("[pipeline.run_agentic_loop] image gen note unavailable", exc_info=True)
        discovery = ToolDiscovery(tools, _TOOL_REGISTRY, notes=_discovery_notes, direct=_computer_use.OWNER_TOOL_NAMES | device_file_tools | {pin_tool})
        image_surface = "absent"
        image_hint = ""
        if "generate_image" in _final_tool_names and _discovery_notes.get("media") and not is_group and not is_proactive:
            from core.image_gen import looks_like_image_request
            image_surface = "direct" if looks_like_image_request(_last_user_text) else "discoverable"
            if image_surface == "direct":
                # This is presentation only, after every caller/model/character filter.
                discovery.loaded.add("media")
                image_hint = _pt('本轮已提供 generate_image，可直接调用。用户明确要求画图或改图时使用；只询问能力时说明即可，不自动生图。')
            else:
                image_hint = _pt('当前可用生图：先调用 load_tools_media，再使用返回的 generate_image 定义。')
            image_hint += _discovery_notes["media"]
        tools = discovery.schemas()
        _record_discovery(category="tool_loop_discovery", code="initial_surface", status="ok",
                          context={"category_count": len(discovery.groups), "schema_count": len(tools),
                                   "image_surface": image_surface})
        mcp_opaque_params_note = format_mcp_opaque_params_note(tools)

        loop_msgs = list(messages)
        if any((tool.get("function") or tool).get("name") in device_file_tools for tool in tools):
            loop_msgs.insert(0, {"role": "system", "_layer": "11.6_device_file_agent", "content": (
                "For local desktop files, device_workspace_list and device_file_* tools are directly available. "
                "Use device_file_list_dir/stat/find_name/search_text/read for independent read-only operations. "
                "Name search never reads contents; search_text requires content_search=true in options. "
                "Follow next_cursor and truncated; incomplete results are not all files. "
                "Use device_file_task_start for compound work and set operation_intent explicitly: inspect/search/read/edit/create. "
                "Skip tool discovery and Computer Use for supported file primitives. "
                "Pass the exact filename or path, location hints and requested change in the goal; the worker searches "
                "within the selected grant. If no grant covers the target, ask the owner for its location or access. "
                "Check the same task ID once after starting. For queued or running work, report that it is in progress. "
                "On follow-up, use active_task, the known task ID or recent_task from the workspace tool. "
                "Steer an active task when the owner corrects it. "
                "Report a saved edit only if the receipt confirms saved_and_verified. Zero writes is valid for read-only intent. "
                "request_status=accepted is submission only; task_status is the work result. "
                "Tests/scripts use an authorized process executor or existing MCP, not the file planner; "
                "GUI goals use Computer Use. Server copies are handled by chat artifact tools."
            )})
        from core.agent_runtime.models import TaskPrincipal

        computer_use_hint = _computer_use.role_prompt(
            _final_tool_names, actor=TaskPrincipal.reality(uid, char_id) if uid and char_id else None,
        )
        if computer_use_hint:
            loop_msgs.insert(max(0, len(loop_msgs) - 1), computer_use_hint)
        from core.tool_audit import is_recap_request, query as query_tool_audit
        latest_user_text = next((item.get("content") for item in reversed(messages)
                                 if item.get("role") == "user" and isinstance(item.get("content"), str)), "")
        if is_recap_request(latest_user_text):
            from core.self_management.store import query_audit
            tool_receipts = query_tool_audit(uid, char_id, time_range="7d", limit=30)
            self_receipts = query_audit(uid, char_id, time_range="7d")[:20]
            recap = json.dumps({"tool_calls": tool_receipts, "self_changes": self_receipts}, ensure_ascii=False)
            loop_msgs.insert(0, {"role": "system", "_layer": "11.4_tool_audit_grounding", "content": (
                "Recorded actions for this owner and character follow. Use these receipts before claiming what you did. "
                "The list is bounded; if a specific action is absent, say the available audit does not show it. "
                "Do not invent capability names, outcomes, or parameters. " + recap[:6000]
            )})
        loop_msgs.insert(0, {"role": "system", "_layer": "11.6_tool_discovery", "content": (
            _pt('工具按分类呈现。已经提供具体定义的工具可直接调用；只看到 load_tools_ 分类入口时，先加载分类，再在下一轮使用具体定义。每个入口列出了当前可用工具名与用途，不要把未加载误判为不可用。加载只提供定义，不是业务执行或成功证据。未提供定义的工具不得猜测参数。')
            + ((_pt('找工具的方向：') + discovery.routing_hint()) if discovery.routing_hint() else "")
            + ("\n" + image_hint if image_hint else "")
        )})
        image_refs = [
            ref for ref in (media_refs or [])
            if isinstance(ref, dict) and ref.get("kind") == "image" and ref.get("sha256")
        ]
        if image_refs:
            loop_msgs.insert(0, {"role": "system", "content": (
                _pt('本轮收到的图片可再次读取。需要更仔细查看时调用 reread_image；若本轮提供了固定角色形象参考图的工具，可根据对话判断图片是否用于今后生成自己的形象，合适时用下列指纹固定。不要猜测或编造指纹：\n') + "\n".join(
                    f"- {ref.get('filename', 'image')}: {ref['sha256']}" for ref in image_refs[-8:]
                )
            ), "_layer": "11.3_image_references"})
        if tool_call_required and not required_from_messages(loop_msgs):
            _grounding = grounding_message(
                required=True,
                tool_names=required_tool_names,
            )
            if _grounding:
                loop_msgs.insert(max(0, len(loop_msgs) - 1), _grounding)
        if self_management_context is not None:
            loop_msgs.insert(0, {"role": "system", "content": (_pt('可自主管理的功能状态：') + f'{json.dumps(self_management_context, ensure_ascii=False)}' + _pt('。需要时先加载self_management，再用manage_self_capability调整这些ID；携带当前revision和新的action_id，执行后读取有效状态确认结果。冻结、未授权或不在清单中的开关不能自行开启。')), "_layer": "11.4_self_management"})
        if "read_self_action_history" in _final_tool_names:
            loop_msgs.insert(0, {"role": "system", "content": (
                "When asked what Self Capability changes you made or which failed, read_self_action_history first. "
                "State only recorded capability IDs and outcomes; if the audit has no matching entry, say so. "
                "A successful operation can leave current_value=false."
            ), "_layer": "11.4_self_action_grounding"})
        # 工具意愿软提示（Brief 29 · 5，Brief 28 补丁；Brief 120 补充尾部花括号约定，
        # Brief 122 补充"调用过程本身不入台词"）：利用 recency 位置，插在用户消息
        # 之前；只在 loop 首步注入一次，不进 short_term history（loop_msgs 本就是
        # 一次性副本）。
        #
        # Brief 249：builder 的工具事实规则适用于后续每一步实际返回结果；
        # 不再用整轮“无工具结果”的禁令，再依赖本软提示提供例外。
        # nudge 只负责调用意愿/relay 表达，不授予能力，也不构成完成证据。
        #
        # Brief 122：用户说"去调用工具玩一下"这类话时，模型容易把"调用工具"本身
        # 当成一个可以叙述的动作——尤其 roleplay 风格本来就鼓励把动作写进（）——
        # 于是把工具名/参数/调用格式当台词或动作描写念出来，而不是真的走结构化
        # tool_calls。工具的 description/参数说明是写给"决策该不该调、传什么参数"
        # 用的内部资料，不是要转述给对方的台词，必须显式划清。
        if cfg.get("nudge_hint", True) and loop_msgs:
            loop_msgs.insert(len(loop_msgs) - 1, {
                "role": "system",
                "content": (
                    _pt('工具是你的手脚。需要查询、阅读、记录、修改或操作时，主动使用本轮可用工具，不要等对方反复催促，也不要凭记忆编造。先加载相应分类，再调用具体工具；拿到中间结果后继续完成必要步骤，直到实际完成或遇到明确阻碍。口头承诺、角色台词和动作描写不能代替执行；角色口吻用来回应真实结果。对方纠正目标、对象或来源时，立即按最新要求调整行动，停止为之前的理解辩解。修改以前交付的文件用 artifacts：定位原工件、读取正文、更新并核对结果；自己的私有笔记用 self；用户上传的资料用 search_documents/read_document；用户长消息原文才用 read_long_user_message，不能拿它代替你写过的文件。不知道文件位置就先查列表；只有真实缺少必要参数、工具未提供或执行被拒绝时才说明具体阻碍，不凭印象宣称没有权限，也不声称记得正文而省略读取。优先发起结构化工具调用；只有确实没法走结构化调用时，才在自然语言回复末尾附加「{true: 具体要做什么，参数是什么}」请求执行，不只用文字暗示。不需要再调用工具时什么都不加，不要输出内部协议标记。工具调用这个动作本身是系统内部静默完成的，不是说给对方听、也不是演给对方看的内容：禁止把工具名、参数、调用语法当成台词念出来或写进（）动作描写里；对方说「去调用工具玩一下」只是在推动你去做这件事，不是要你复述你打算怎么调。你的回复永远只是你这个角色本身的自然语言反应，调用细节只对系统可见。')
                    + ("\n\n" + mcp_opaque_params_note if mcp_opaque_params_note else "")
                ),
                "_layer": "11.5_tool_nudge",
            })
        from core.tools.file_path_hints import prompt_hint as _file_path_hint
        _path_hint = _file_path_hint(_last_user_text, uid=uid, is_group=is_group, is_proactive=is_proactive)
        if _path_hint:
            loop_msgs.insert(max(0, len(loop_msgs) - 1), _path_hint)
        used_tool = False
        successful_tool_call = False
        # ("natural"/"exhausted"/"confirm", text) — 收尾结果种类 + 文本
        outcome: tuple[str, str] | None = None

        async def _execute_with_ephemeral_status(
            call_id: str,
            tool_name: str,
            tool_args: dict,
            *,
            index: int,
            total: int,
            origin: str,
        ) -> ToolExecutionOutcome:
            """Bridge trusted dispatcher lifecycle events into one UI-only status."""
            execute_kwargs = {
                "origin": origin,
                "char_id": char_id,
                "bypass_read_log": _bypass_read_log,
            }
            if tool_name == pin_tool:
                execute_kwargs["pin_reference_hashes"] = pin_hashes
            if caller_allowed_tool_names is not None:
                execute_kwargs["allowed_tool_names"] = caller_allowed_tool_names
            if tool_event_observer is None:
                return await execute_structured(
                    tool_name, tool_args, uid, uid, is_group, session_state,
                    **execute_kwargs,
                )

            # call_id is model-provided, so never expose it as the public status
            # identifier. The generated id persists only through this live call.
            del call_id
            status_id = f"tool_{uuid.uuid4().hex}"
            waiting_task: asyncio.Task | None = None
            terminal = False

            async def _emit(kind: str, *, attempt: int = 1, ui_label: str = "工具") -> None:
                nonlocal terminal, waiting_task
                if terminal:
                    return
                if kind in {"finished", "failed", "outcome_unknown", "cancelled"}:
                    terminal = True
                    if waiting_task is not None:
                        waiting_task.cancel()
                        waiting_task = None
                event = ToolEphemeralEvent(
                    status_id=status_id,
                    kind=kind,
                    tool_name=tool_name,
                    ui_label=ui_label,
                    index=index,
                    total=total,
                    attempt=attempt,
                    ttl_s=DEFAULT_TTL_S,
                )
                await _notify_tool_event(tool_event_observer, event)

            async def _delayed_waiting() -> None:
                try:
                    await asyncio.sleep(waiting_after_s)
                    if not terminal:
                        await _emit("waiting")
                except asyncio.CancelledError:
                    return

            async def _dispatcher_status(
                kind: str,
                *,
                attempt: int = 1,
                ui_label: str = "工具",
            ) -> None:
                nonlocal waiting_task
                await _emit(kind, attempt=attempt, ui_label=ui_label)
                if kind == "queued" and waiting_task is None:
                    waiting_task = asyncio.create_task(_delayed_waiting())
                elif kind == "waiting" and waiting_task is not None:
                    # A reconnect has already produced a truthful wait update.
                    # Do not later create a second threshold-driven status bubble.
                    waiting_task.cancel()
                    waiting_task = None

            try:
                return await execute_structured(
                    tool_name, tool_args, uid, uid, is_group, session_state,
                    **execute_kwargs,
                    tool_status_observer=_dispatcher_status,
                )
            finally:
                if waiting_task is not None:
                    waiting_task.cancel()

        def _tool_message_content(
            result: str | None,
            ask_confirm: str | None,
            *,
            generated_at: float | None = None,
            discovery_result: bool = False,
            status: str | None = None,
        ) -> str:
            """Keep confirmation state outside the untrusted tool-data frame."""
            if discovery_result:
                # Local protocol receipt; never mark schema discovery as business
                # success (or as a failed business execution).
                return result or _pt('分类未加载；未执行任何业务操作。')
            if ask_confirm:
                return ask_confirm
            if result:
                from core.tool_grounding import classify_tool_outcome
                kind = classify_tool_outcome(status)
                validity = (
                    "current_turn" if kind == "ok"
                    else "outcome_unknown" if status == "outcome_unknown"
                    else "unconfirmed" if kind == "info"
                    else "execution_failed"
                )
                return frame_tool_message(result, generated_at=generated_at, validity=validity)
            return _pt('（工具无结果或执行失败）')

        async def _resolve_relay_intent(intent_text: str) -> list[dict]:
            """Brief 120·工具循环二次调用兜底：把 {true:...} 里模型自标注的干净意图
            文本，交给探针范式解析出工具名+参数。复用本轮已过滤的 tools schema（含
            mcp），覆盖 run_agentic_loop 当前实际暴露的 categories，而不是探针默认
            的 info/desktop 两类。
            """
            exposed_tool_names = {
                str((tool.get("function") or tool).get("name") or "")
                for tool in tools
            }
            exposed_tool_names.discard("")
            relay_system = get_tool_loop_relay_prompt(
                categories,
                allowed_tool_names=exposed_tool_names,
            )
            relay_system += _pt('\n未加载的分类只能调用当前提供的 load_tools_ 入口（参数为 {}），不能直接选择具体工具。')
            try:
                relay_response = await llm_client.chat(
                    [
                        {"role": "system", "content": relay_system},
                        {"role": "user", "content": intent_text},
                    ],
                    tools=tools,
                    call_category="probe",
                    char_id=char_id,
                    is_proactive=is_proactive,
                )
            except Exception as e:
                log_error("pipeline.run_agentic_loop.relay_resolve", e)
                return []
            parsed = llm_client.parse_probe_response(
                relay_response,
                allowed_tool_names=exposed_tool_names,
            )
            raw_calls = parsed.tool_calls if parsed.status == "tool_selected" else []
            resolved = []
            for rc in raw_calls:
                name = rc.get("name")
                if not name:
                    continue
                resolved.append({
                    "id": f"relay_{uuid.uuid4().hex[:8]}",
                    "name": name,
                    "arguments": rc.get("arguments") or {},
                })
            return resolved

        async def _run_steps() -> None:
            nonlocal used_tool, successful_tool_call, outcome, tools
            business_steps = 0
            discovery_steps = 0
            from core.tool_runtime.read_cache import ReadCache
            life_record_reads = ReadCache(uid, char_id, origin="owner_chat")
            action_receipts: dict[str, ToolExecutionOutcome] = {}
            while business_steps < max_steps:
                tools = discovery.schemas()
                _record_discovery(category="tool_loop_discovery", code="request_surface", status="ok",
                                  context={"schema_count": len(tools), "loaded_count": len(discovery.loaded)})
                if len(tools) > 20:
                    _record_discovery(category="tool_loop_capacity", code="tool_schema_over_budget", status="attention",
                                      context={"tool_count": len(tools), "category_count": len(discovery.loaded)})
                offered_names = {(t.get("function") or t)["name"] for t in tools}
                # Freeze this request's surface: loading a category cannot authorize
                # a guessed business call in the same model response.
                async def _call(tc, index, total, origin):
                    nonlocal used_tool
                    name = tc["name"]
                    if name not in offered_names:
                        _record_discovery(category="tool_loop_discovery", code="unoffered_call", status="attention")
                        return ToolExecutionOutcome(
                            status="tool_failed",
                            result=_pt('工具调用被拒绝：本轮未提供该工具。请先加载分类。'),
                        )
                    if name.startswith(_discovery_prefix):
                        result, loaded = discovery.load(name, tc["arguments"])
                        _record_discovery(category="tool_loop_discovery", code="category_loaded" if loaded else "invalid_discovery",
                                          status="ok" if loaded else "attention",
                                          context={"loaded_count": len(discovery.loaded), "schema_count": len(discovery.schemas())})
                        if loaded:
                            note = format_mcp_opaque_params_note(discovery.schemas())
                            if note:
                                result += "\n" + note
                        # Local protocol receipt; not a dispatcher execute.
                        return ToolExecutionOutcome(status="discovery", result=result)
                    used_tool = True
                    from core.llm_turn_route import current_route
                    from core.tool_dispatcher import is_side_effect_tool
                    action_key = json.dumps([name, tc["arguments"]], sort_keys=True, ensure_ascii=False)
                    route = current_route()
                    # A newly selected model must consume existing receipts, not
                    # reissue an identical mutation (even under a fresh call ID).
                    if (route is not None and route.effective is not None
                            and is_side_effect_tool(name) and action_key in action_receipts):
                        _record_discovery(category="tool_loop_dedupe", code="action_receipt_reused", status="ok")
                        return action_receipts[action_key]
                    read_key = life_record_reads.key(name, tc["arguments"])
                    previous = life_record_reads.get(read_key)
                    if previous is not None:
                        _record_discovery(category="tool_loop_dedupe", code="same_read_reused", status="ok",
                                          context={"tool": name})
                        return ToolExecutionOutcome(status=previous.status, result=(previous.result or "") + _pt(
                            "\n同一查询本轮已经读取，数据版本未变，没有新增证据。需要继续时使用返回的记录 ID、分页位置或新的筛选条件。"))
                    tool_outcome = await _execute_with_ephemeral_status(
                        tc["id"], name, tc["arguments"], index=index, total=total, origin=origin,
                    )
                    action_receipts[action_key] = tool_outcome
                    if read_key is not None:
                        life_record_reads.put(name, tc["arguments"], tool_outcome, successful=tool_outcome.status == "tool_executed", expected_key=read_key)
                    else:
                        # Any intervening action can change data or grants.
                        life_record_reads.clear()
                    return tool_outcome

                def _charge(calls):
                    nonlocal business_steps, discovery_steps
                    if (discovery_steps < len(discovery.groups) and calls
                            and all(c["name"].startswith(_discovery_prefix) for c in calls)):
                        discovery_steps += 1
                    else:
                        business_steps += 1

                turn = await llm_client.chat_turn(
                    loop_msgs, tools, char_id=char_id, is_proactive=is_proactive,
                )
                from core.context_continuity import acknowledge
                acknowledge(loop_msgs)
                if not turn.tool_calls:
                    if not turn.content.strip():
                        # Some OpenAI-compatible gateways occasionally return a
                        # successful tool-loop response whose assistant message
                        # contains neither content nor tool_calls.  Treating that
                        # as a natural stop leaks an empty reply into turn_sink,
                        # where USER_CHAT correctly rejects it and /desktop/chat
                        # becomes a local 500.  Reuse the existing tool-free
                        # closing path instead; it is also the safest fallback
                        # when the gateway loses a function-call translation.
                        logger.warning(
                            "[pipeline.run_agentic_loop] empty assistant completion "
                            "without tool_calls; falling back to tool-free final generation"
                        )
                        outcome = ("empty", "")
                        return
                    display_text, intent_text = parse_tail_brace(turn.content)
                    if intent_text:
                        relay_calls = await _resolve_relay_intent(intent_text)
                        if relay_calls:
                            # 可观测性：relay 分支和原生 tool_calls 分支最终都调用同一个
                            # execute_structured()，仅凭 error.log 里的报错完全分不清故障来自哪条路径
                            # （Brief 120 事后排查时吃过这个亏）。这里显式记一条 info，
                            # 且下面用独立 origin=assistant_loop_relay。
                            logger.info(
                                "[pipeline.run_agentic_loop] tail-brace relay 触发: "
                                "intent=%r resolved=%s",
                                intent_text, [rc["name"] for rc in relay_calls],
                            )
                            loop_msgs.append({
                                "role": "assistant",
                                "content": display_text,
                                "tool_calls": [
                                    {
                                        "id": rc["id"],
                                        "type": "function",
                                        "function": {
                                            "name": rc["name"],
                                            "arguments": json.dumps(rc["arguments"], ensure_ascii=False),
                                        },
                                    }
                                    for rc in relay_calls
                                ],
                            })
                            for _index, rc in enumerate(relay_calls, start=1):
                                if rc["name"] == "manage_self_capability":
                                    # Relay parsing cannot safely supply an action id or a
                                    # revision, so this narrow operation is native-call only.
                                    loop_msgs.append({"role": "tool", "tool_call_id": rc["id"],
                                                      "content": "该操作必须使用原生工具调用。未执行。"})
                                    continue
                                try:
                                    tool_outcome = await _call(
                                        rc,
                                        index=_index,
                                        total=len(relay_calls),
                                        origin="assistant_loop_relay",
                                    )
                                except Exception as e:
                                    log_error("pipeline.run_agentic_loop.relay_execute", e)
                                    tool_outcome = ToolExecutionOutcome(status="tool_failed")
                                result, ask_confirm = tool_outcome.result, tool_outcome.confirmation_request
                                if tool_outcome.status == "outcome_unknown":
                                    outcome = ("unknown", "")
                                    return
                                if tool_outcome.status == "tool_executed" and not ask_confirm:
                                    successful_tool_call = True
                                loop_msgs.append({
                                    "role": "tool",
                                    "tool_call_id": rc["id"],
                                    "_private_gmail_result": rc["name"] in gmail_tools,
                                    "_private_device_file": rc["name"] in device_file_tools,
                                    "content": _tool_message_content(result, ask_confirm, generated_at=time.time(),
                                                                     discovery_result=rc["name"].startswith(_discovery_prefix),
                                                                     status=tool_outcome.status),
                                })
                                if ask_confirm:
                                    outcome = ("confirm", ask_confirm)
                                    return
                            _charge(relay_calls)
                            # 不 return：复用现有多步机制，让 for 循环自然进入下一轮，
                            # 而不是新开一层循环（Brief 120 §2.3）。
                            continue
                        # 标了 {true...} 但解析不出工具：退回自然结束，用剥离后的
                        # 展示文本收尾，不残留花括号、不吞掉内容。
                        outcome = ("natural", display_text)
                        return
                    outcome = ("natural", display_text)
                    return
                # Normalize provider continuations before storing loop history.
                # Each target protocol reconstructs its wire input from receipts.
                from core.llm_protocol import canonical_tool_history
                loop_msgs.extend(canonical_tool_history(
                    turn.continuation_items or [turn.assistant_message], validate=False,
                ))
                for _index, tc in enumerate(turn.tool_calls, start=1):
                    try:
                        tool_outcome = await _call(
                            tc,
                            index=_index,
                            total=len(turn.tool_calls),
                            origin=("assistant_self_management" if tc["name"] == "manage_self_capability" else "assistant_loop"),
                        )
                    except Exception as e:
                        log_error("pipeline.run_agentic_loop.execute", e)
                        tool_outcome = ToolExecutionOutcome(status="tool_failed")
                    result, ask_confirm = tool_outcome.result, tool_outcome.confirmation_request
                    if tool_outcome.status == "outcome_unknown":
                        outcome = ("unknown", "")
                        return
                    if tool_outcome.status == "tool_executed" and not ask_confirm:
                        successful_tool_call = True
                    loop_msgs.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "_private_gmail_result": tc["name"] in gmail_tools,
                        "_private_device_file": tc["name"] in device_file_tools,
                        "_continuity_receipt": getattr(result, 'continuity_receipt', None),
                        "content": _tool_message_content(result, ask_confirm, generated_at=time.time(),
                                                         discovery_result=tc["name"].startswith(_discovery_prefix),
                                                         status=tool_outcome.status),
                    })
                    if ask_confirm:
                        outcome = ("confirm", ask_confirm)
                        return
                _charge(turn.tool_calls)
            _record_discovery(category="tool_loop_discovery", code="budget_exhausted", status="attention",
                              context={"business_steps": business_steps, "discovery_steps": discovery_steps})
            outcome = ("exhausted", "")

        # wait_for() cannot distinguish its own deadline from an inner
        # TimeoutError (Python 3.11+ aliases asyncio.TimeoutError to TimeoutError).
        # A chat_turn 90s timeout would otherwise be logged as a 720s budget miss.
        _loop_started = time.monotonic()
        try:
            await asyncio.wait_for(_run_steps(), timeout=total_timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            elapsed = time.monotonic() - _loop_started
            remaining = total_timeout_s - elapsed
            if remaining > 0.05:
                logger.warning(
                    "[pipeline.run_agentic_loop] 内层调用超时 elapsed=%.1fs remaining=%.1fs，按步数耗尽处理",
                    elapsed, remaining,
                )
                _record_discovery(
                    category="tool_loop_discovery",
                    code="inner_timeout",
                    status="attention",
                    context={"elapsed_s": round(elapsed, 1), "remaining_s": round(remaining, 1)},
                )
            else:
                logger.warning(
                    "[pipeline.run_agentic_loop] 总预算 %.0fs 超时，按步数耗尽处理", total_timeout_s
                )
                _record_discovery(category="tool_loop_discovery", code="timeout", status="attention")
            outcome = ("exhausted", "")
        finally:
            from core.tools.drinking import end_turn as _end_drinking_turn
            _end_drinking_turn()

        async def _single_chunk(text: str):
            if text:
                yield text

        kind, text = outcome
        from core.control_markers import strip_control_markers
        if kind == "unknown":
            from core.prompt_language import choose
            text = choose(
                "这次操作的结果还无法确认。请先核实实际结果，再决定下一步。",
                "The outcome of this operation is still unknown. Please verify it before taking another action.",
            )
            return _single_chunk(text) if stream else text
        if kind == "confirm":
            text = strip_control_markers(text)
            return _single_chunk(text) if stream else text

        if kind == "natural" and not used_tool:
            # 从未调用过工具：等价于原有单发生成，反坍缩检查照旧过一遍。
            final_text = await anti_collapse_prefix_retry(
                loop_msgs, text, char_id=char_id, is_proactive=is_proactive,
            )
            from core.control_markers import strip_control_markers
            final_text = await guard_unverified_claim(
                loop_msgs, strip_control_markers(final_text),
                successful_tool_call=successful_tool_call,
                char_id=char_id, is_proactive=is_proactive,
            )
            final_text = await guard_tool_meta_leak(
                loop_msgs, final_text, char_id=char_id, is_proactive=is_proactive,
            )
            return _single_chunk(final_text) if stream else final_text

        # natural（用过工具）或 exhausted：强制收尾，注入声音锚定，走不带 tools 的出口。
        # 工具已关闭，收尾生成不得再携带鼓励输出 relay 标记的专用指令。
        loop_msgs[:] = _drop_relay_nudge(loop_msgs)
        if kind == "exhausted":
            loop_msgs.append({"role": "system", "content": _pt(
                "检索执行状态：本轮预算已耗尽。请围绕用户原始问题，基于已返回的证据回答；"
                "能估算则说明假设，不能得出结论则说明已查到什么、还缺什么。不得将未查到等同于不存在，也不要替换用户的任务目标。"),
                "_layer": "11_tool_grounding"})
        loop_msgs.append({"role": "system", "content": _voice_reanchor(char_id)})

        def _closing_candidate(closing: str) -> str:
            candidate = strip_control_markers(closing)
            if not candidate.strip() and kind == 'natural':
                retained = strip_control_markers(text)
                if retained.strip():
                    logger.warning(
                        '[pipeline.run_agentic_loop] stage=closing empty tool-free reply; '
                        'validating retained natural answer chars=%d', len(retained),
                    )
                    return retained
            return candidate

        if stream:
            async def _grounded_stream():
                _pieces: list[str] = []
                async for _piece in run_llm_stream(
                    loop_msgs, char_id=char_id, is_proactive=is_proactive, user_id=uid, require_complete=True,
                ):
                    _pieces.append(_piece)
                from core.control_markers import strip_control_markers
                _guarded = await guard_unverified_claim(
                    loop_msgs, _closing_candidate("".join(_pieces)),
                    successful_tool_call=successful_tool_call,
                    char_id=char_id, is_proactive=is_proactive,
                )
                _guarded = await guard_tool_meta_leak(
                    loop_msgs, _guarded, char_id=char_id, is_proactive=is_proactive,
                )
                if _guarded:
                    yield _guarded
            return _grounded_stream()
        final_text = await run_llm(
            loop_msgs, is_proactive=is_proactive, char_id=char_id, require_complete=True,
        )
        final_text = await guard_unverified_claim(
            loop_msgs, _closing_candidate(final_text),
            successful_tool_call=successful_tool_call,
            char_id=char_id, is_proactive=is_proactive,
        )
        return await guard_tool_meta_leak(
            loop_msgs, final_text, char_id=char_id, is_proactive=is_proactive,
        )
