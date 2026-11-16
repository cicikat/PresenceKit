"""Tool execution; called through the stable dispatcher facade."""

from core.conversation_stats import attributed as _stats_attributed
from core.tool_runtime.outcome import ToolExecutionOutcome
@_stats_attributed
async def _execute_structured_impl(
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
    """
    执行工具，返回 ToolExecutionOutcome。

    origin 必填，不在白名单则 fail-closed：status=tool_failed（result / confirmation_request
    均为 None）+ 记 warning。
    白名单：user_live（Path A 用户发起）/ assistant_loop（Path C tool loop 自主多步调用，
    Brief 28）/ assistant_loop_relay（Path C
    尾部花括号二次调用兜底，Brief 120——与 assistant_loop 区分开，是为了让 action_trace /
    error.log 里能分清一次工具执行到底来自模型的原生结构化 tool_calls，还是来自
    {true:...} 标记走的中转解析；此前两者共用同一个 origin，出故障时完全没法从落痕
    或日志反推是哪条路径触发的）。
    漏传 → TypeError，杜绝静默绕过。
    char_id: 当前活跃角色桶 id，用于 persist=True 工具的已读指纹检查和 short_term 回写。
    bypass_read_log: 本轮用户消息命中显式重读短语时由调用方传 True（Brief 82 · 决策 7），
    放行 persist 工具的已读指纹拦截，但指纹仍照常记录/刷新。
    """
    from core import tool_dispatcher as _runtime
    if origin not in _runtime._EXECUTE_ALLOWED_ORIGINS:
        _runtime.logger.warning(
            "[tool_dispatcher.execute] 拒绝执行: origin=%r 不在白名单, tool=%s",
            origin, tool_name,
        )
        # 闸门拒绝不落痕迹——这不是角色做过的事（Brief 27 · 2.2）。
        return _runtime._execution_outcome("tool_failed")

    # B4：出口把无 properties 的 object 参数降级成了 JSON 字符串，这里还原（dict 原样通过）。
    _spec = _runtime._TOOL_REGISTRY.get(tool_name)
    if isinstance(_spec, dict) and isinstance(tool_args, dict):
        from core.llm_protocol import coerce_tool_args
        tool_args = coerce_tool_args(_spec.get("parameters"), tool_args)

    _trace_started = _runtime._time.perf_counter()
    _trace_truncated = False
    _trace_failure_reason = ""

    def _trace(status: str, digest_source=None) -> None:
        if tool_name in _runtime._gmail_tools.NAMES | _runtime._intent_tools.NAMES | _runtime._device_file_tools.NAMES:
            return  # Private tools have content-free dispatcher receipts.
        try:
            from core.memory import action_trace
            trace_kwargs = {}
            if tool_name in {"search_events", "expand_event_window", "get_related_events"}:
                trace_kwargs = {
                    "scope": {"uid": user_id, "char_id": char_id, "realm": "reality"},
                    "truncated": _trace_truncated,
                    "failure_reason": _trace_failure_reason or (status if status != "ok" else ""),
                    "duration_ms": int(max(0.0, (_runtime._time.perf_counter() - _trace_started) * 1000)),
                }
            action_trace.record(
                user_id, char_id,
                tool=tool_name, origin=origin, status=status,
                args_digest=action_trace.build_args_digest(tool_name, tool_args),
                result_digest=action_trace.build_result_digest(tool_name, digest_source),
                echo_event_log=bool(_runtime._TOOL_REGISTRY.get(tool_name, {}).get("echo_event_log", True)),
                **trace_kwargs,
            )
        except Exception as _at_err:
            _runtime.logger.debug("[tool_dispatcher] action_trace record error: %s", _at_err)

    if tool_name in {"list_self_capabilities", "read_self_action_history"}:
        from core.self_management.policy import feature_enabled
        if is_group or origin not in {"assistant_loop", "autonomy_loop"} or not feature_enabled():
            return _runtime._execution_outcome("tool_failed", "Self Capability history is unavailable in this context.")

    if tool_name in {"get_ambient_state", "generate_image", "read_long_user_message", "search_events", "expand_event_window", "get_related_events", "search_memory_dossiers", "read_memory_dossier", "search_dossier_events", "update_memory_dossier", "get_memory_consolidation_status", "request_memory_consolidation", "read_life_records", "reread_image", "write_artifact", "update_artifact", "read_artifact", "list_artifacts", "self_db_tables", "self_db_create_table", "self_db_insert", "self_db_query", "self_db_update", "self_db_delete", "self_db_drop_table", "self_tool_define", "self_tool_list", "self_tool_run"} and is_group:
        _trace("failed", "reality_event_tools_forbidden_in_group")
        return _runtime._execution_outcome("tool_failed")

    if tool_name == "backfill_diary":
        owner_id = str(_runtime.get_config().get("scheduler", {}).get("owner_id") or "")
        if (
            is_group
            or origin not in {"user_live", "assistant_loop", "assistant_loop_relay"}
            or not owner_id
            or str(user_id) != owner_id
        ):
            return _runtime._execution_outcome("tool_failed", "补写日记仅允许在用户私聊请求中执行。")

    if tool_name in _runtime._QZONE_TOOL_NAMES:
        from core.qzone_service import allowed, autonomy_tool_allowed
        if any(key in tool_args for key in ("user_id", "uid", "char_id", "owner", "realm")):
            _trace("failed", "grant_principal_mismatch")
            return _runtime._execution_outcome("tool_failed", "grant_principal_mismatch")
        if (is_group or origin not in {"user_live", "assistant_loop", "assistant_loop_relay", "autonomy_loop"}
                or (origin == "autonomy_loop" and not autonomy_tool_allowed(tool_name, user_id, char_id))
                or not allowed(user_id, char_id, write=_runtime._TOOL_REGISTRY[tool_name].get("effect") == "write")):
            _trace("failed", "qzone_not_authorized")
            return _runtime._execution_outcome("tool_failed", "QQ 空间未授权给当前会话/角色。")

    async def _notify_status(kind: str, *, attempt: int = 1) -> None:
        """UI-only hook; it runs after dispatcher gates and never affects execution."""
        if tool_status_observer is None:
            return
        try:
            value = tool_status_observer(
                kind,
                attempt=attempt,
                ui_label=str(tool_info.get("ui_label") or "工具"),
            )
            if _runtime.inspect.isawaitable(value):
                await value
        except Exception:
            _runtime.logger.warning(
                "[tool_dispatcher] tool status observer failed: tool=%s kind=%s",
                tool_name,
                kind,
                exc_info=True,
            )

    from core import user_relation

    from core.error_handler import get_tool_fail_response

    if tool_name not in _runtime._TOOL_REGISTRY:
        _fail = get_tool_fail_response()
        _trace("failed", _fail)
        return _runtime._execution_outcome("tool_unknown", _fail)

    if allowed_tool_names is not None and tool_name not in allowed_tool_names:
        return _runtime._execution_outcome("tool_failed", "这项能力不在本次调用范围内。")

    # A video call belongs to the desktop invite flow, even if the model picked
    # the similarly named phone automation tool. Re-enter the normal gates for
    # the actual tool; never enqueue this task on the mobile device.
    if tool_name == "phone_control_start" and isinstance(tool_args, dict):
        task = tool_args.get("task")
        if isinstance(task, str) and any(word in task for word in ("视频通话", "视频电话", "视频聊天", "打视频")) and any(
            word in task for word in ("邀请", "发起", "拨打", "打给", "来电", "打开视频通话")
        ):
            if is_group or (allowed_tool_names is not None and "invite_video_call" not in allowed_tool_names):
                return _runtime._execution_outcome("tool_failed", "视频电话只能在已授权的桌面私聊中发起。")
            _runtime.logger.info("[tool_dispatcher] 将误选的手机自动化视频邀请转到桌面来电工具")
            return await _runtime._execute_structured_impl(
                "invite_video_call", {}, user_id, target_id, is_group, session_state,
                origin=origin, char_id=char_id, bypass_read_log=bypass_read_log,
                tool_status_observer=tool_status_observer, allowed_tool_names=allowed_tool_names,
            )

    tool_info = _runtime._TOOL_REGISTRY[tool_name]
    if tool_info.get("autonomy_only") and (is_group or origin != "autonomy_loop"):
        return _runtime._execution_outcome("tool_failed", "intent_context_required")
    if tool_name in _runtime._device_file_tools.NAMES and (
        is_group or origin not in {"assistant_loop", "assistant_loop_relay"}
        or not _runtime._device_file_tools.service.allowed(user_id, char_id)
    ):
        return _runtime._execution_outcome("tool_failed", "device_file_denied")
    if tool_name in _runtime._gmail_tools.NAMES and (
        is_group or origin not in {"assistant_loop", "assistant_loop_relay", "autonomy_loop"}
        or not _runtime._gmail_tools.allowed(tool_name, user_id, char_id)
    ):
        return _runtime._execution_outcome("tool_failed", "gmail_not_authorized")
    if tool_name in _runtime._computer_use.OWNER_TOOL_NAMES:
        if (is_group or origin not in {"user_live", "assistant_loop", "assistant_loop_relay"}
                or not _runtime._computer_use.owner_scope(user_id, char_id)):
            return _runtime._execution_outcome("tool_failed", "当前会话无法操作桌面。")
    if tool_name in _runtime._INTIFACE_TOOL_NAMES and not _runtime.intiface_opted_in():
        _msg = "Intiface 硬件能力当前处于冻结状态，需要显式 opt-in"
        _trace("failed", _msg)
        return _runtime._execution_outcome("tool_failed", _msg)
    if tool_info.get("self_management"):
        if origin not in {"assistant_self_management", "autonomy_self_management"}:
            _trace("failed", "self-management origin rejected")
            return _runtime._execution_outcome("tool_failed", "这项自主管理能力不能在当前上下文中调用。")
    elif tool_info.get("self_management_read"):
        if origin not in {"assistant_loop", "autonomy_loop"}:
            return _runtime._execution_outcome("tool_failed", "Self Capability read is unavailable in this context.")
    else:
        if origin in {"assistant_self_management", "autonomy_self_management"}:
            _trace("failed", "self-management origin may not execute a business tool")
            return _runtime._execution_outcome("tool_failed", "该管理调用只能修改自身能力，不能执行普通工具。")
        from core.self_management.policy import tool_allowed
        if not tool_allowed(user_id, char_id, tool_name):
            _trace("failed", "self capability disabled")
            return _runtime._execution_outcome("tool_failed", "这项能力尚未对当前角色开放。")

    if not _runtime._is_tool_enabled(tool_name):
        _fail = get_tool_fail_response()
        _trace("failed", _fail)
        return _runtime._execution_outcome("tool_failed", _fail)

    # Keep the prelude truthful: an observer is not told the tool is queued
    # until its basic local argument shape has been checked as well.
    if not isinstance(tool_args, dict):
        _msg = "工具参数格式不正确"
        _trace("failed", _msg)
        return _runtime._execution_outcome("arguments_invalid", _msg)
    if tool_name in {
        "get_ambient_state", "get_listening_state", "get_listening_queue", "get_listening_history",
        "get_track_note", "write_track_note", "choose_next_track",
    }:
        for key in ("user_id", "uid", "char_id", "owner"):
            if key in tool_args:
                _trace("failed", "grant_principal_mismatch")
                return _runtime._execution_outcome("tool_failed", "grant_principal_mismatch")
    _schema = tool_info.get("parameters") or {}
    _required = _schema.get("required") if isinstance(_schema, dict) else None
    if isinstance(_required, list):
        _missing = [key for key in _required if isinstance(key, str) and key not in tool_args]
        if _missing:
            _msg = f"工具参数不完整：缺少{_missing[0]}"
            _trace("failed", _msg)
            return _runtime._execution_outcome("missing_parameters", _msg, missing_parameters=_missing)

    _schema_validation_errors = _runtime._schema_errors(tool_args, _schema)
    if _schema_validation_errors:
        _msg = f"工具参数格式不正确：{_schema_validation_errors[0]}"
        _trace("failed", _msg)
        return _runtime._execution_outcome("arguments_invalid", _msg)

    # 权限校验
    if tool_name in _runtime._OWNER_ONLY_HARDWARE_TOOLS:
        owner_id = str(_runtime.get_config().get("scheduler", {}).get("owner_id") or "")
        if is_group or not owner_id or str(user_id) != owner_id:
            _runtime.logger.warning(
                "[tool_dispatcher.execute] 拒绝硬件控制: 非 owner 私聊, user_id=%s is_group=%s tool=%s",
                user_id, is_group, tool_name,
            )
            _msg = "硬件控制只允许 owner 私聊触发"
            _trace("failed", _msg)
            return _runtime._execution_outcome("tool_failed", _msg)

    if tool_name in ("device_shutdown", "device_sleep"):
        if not user_relation.has_permission(user_id, "agent_control"):
            _msg = "你没有执行此操作的权限哦"
            _trace("failed", _msg)
            return _runtime._execution_outcome("tool_failed", _msg)

    # ── persist 工具：指纹去重检查 ────────────────────────────────────────────
    _is_persist = bool(tool_info.get("persist", False))
    _fingerprint: str | None = None
    if _is_persist:
        try:
            from core.memory.tool_read_log import build_fingerprint, is_recently_read
            _fingerprint = build_fingerprint(tool_name, tool_args)
            if _fingerprint and is_recently_read(user_id, char_id, _fingerprint, bypass=bypass_read_log):
                _runtime.logger.info(
                    "[tool_dispatcher] persist 工具已读，跳过: tool=%s fp=%s",
                    tool_name, _fingerprint,
                )
                _skip = "（刚读过这个，这次跳过）"
                _trace("ok", _skip)
                return _runtime._execution_outcome("tool_executed", _skip)
        except Exception as _fp_err:
            _runtime.logger.debug("[tool_dispatcher] fingerprint check error: %s", _fp_err)

    # 执行工具
    try:
        func = tool_info["func"]
        # All local policy/permission/confirmation gates have passed. This is
        # "handling it", not a claim that a remote service or device started.
        await _notify_status("queued")
        from core.conversation_stats import record
        record("tool_call", uid=user_id, char_id=char_id)
        from core.tool_runtime.invoker import Invocation, invoke
        result = await invoke(tool_info, Invocation(
            func=func, tool_name=tool_name, tool_args=tool_args,
            user_id=user_id, char_id=char_id, origin=origin,
            target_id=target_id, is_group=is_group, session_state=session_state,
            allowed_tool_names=allowed_tool_names, pin_reference_hashes=pin_reference_hashes,
            notify_status=_notify_status, has_status_observer=tool_status_observer is not None,
        ))
        # Tool implementations may return a ToolResult.  Keep raw_data inside
        # that object: all outward-facing execution paths consume only its
        # safe_summary, never the dataclass repr or its raw payload.
        from core.tools.tool_result import ToolResult, to_tool_result
        tool_result = to_tool_result(result)
        safe_summary = tool_result.safe_summary
        _trace_truncated = bool((tool_result.meta or {}).get("truncated"))
        _trace_failure_reason = str((tool_result.meta or {}).get("failure_reason") or "")[:128]
        if tool_name in _runtime._gmail_tools.NAMES | _runtime._intent_tools.NAMES | _runtime._device_file_tools.NAMES or (
            tool_name == "web_search" and isinstance(result, ToolResult)
        ):
            validity = tool_result.meta.get("validity")
            if validity == "current_turn":
                _trace("ok", safe_summary)
                await _notify_status("finished")
                return _runtime._execution_outcome("tool_executed", safe_summary)
            if validity == "outcome_unknown":
                await _notify_status("outcome_unknown")
                return _runtime._execution_outcome("outcome_unknown", safe_summary)
            _trace("failed", safe_summary)
            await _notify_status("failed")
            return _runtime._execution_outcome("tool_failed", safe_summary)
        if tool_name in _runtime._QZONE_TOOL_NAMES and tool_result.meta.get("validity") != "current_turn":
            status = "outcome_unknown" if tool_result.meta.get("validity") == "outcome_unknown" else "tool_failed"
            _trace("failed", safe_summary)
            return _runtime._execution_outcome(status, safe_summary)
        _runtime.logger.info(
            "[tool_dispatcher] 工具执行完成: tool=%s result_len=%d safe_len=%d",
            tool_name,
            len(tool_result.raw_data),
            len(safe_summary),
        )

        # ── persist 工具：执行成功后记录指纹 + 回写 short_term ───────────────
        if _is_persist and _fingerprint:
            try:
                from core.memory.tool_read_log import record_read, format_read_memo
                record_read(user_id, char_id, _fingerprint)
                memo = format_read_memo(tool_name, tool_args)
                if memo and _runtime._TOOL_REGISTRY.get(tool_name, {}).get("write_short_term", True):
                    from core.memory.short_term import append as _st_append, _sanitize_assistant_message
                    sanitized = _sanitize_assistant_message(memo, uid=user_id)
                    _st_append(user_id, "assistant", sanitized, char_id=char_id)
            except Exception as _rec_err:
                _runtime.logger.warning("[tool_dispatcher] persist record error: %s", _rec_err)

        execution_status = str((tool_result.meta or {}).get("execution_status") or "")
        if execution_status == "outcome_unknown":
            _trace("outcome_unknown", tool_name if tool_name in {"search_events", "expand_event_window", "get_related_events"} else safe_summary)
            await _notify_status("outcome_unknown")
            return _runtime._execution_outcome("outcome_unknown", safe_summary)
        if origin == 'autonomy_loop':
            from core.context_continuity import retain_result
            retain_result(user_id, char_id, tool_name, tool_result)
        _trace("ok", tool_name if tool_name in {"search_events", "expand_event_window", "get_related_events"} else safe_summary)
        await _notify_status("finished")
        outward = f"工具已执行：{tool_name}，结果：{safe_summary}"
        if not is_group and origin in {'user_live','assistant_loop','assistant_loop_relay','autonomy_loop'}:
            try:
                from core.tool_result_pins import retain as retain_pin_candidate
                _result_id=retain_pin_candidate(user_id,char_id,tool_name,tool_result)
                if _result_id:
                    # Keep the real candidate ID ahead of bounded result text so
                    # downstream prompt clipping cannot remove the pin handle.
                    outward = (
                        f'工具已执行：{tool_name}，result_id={_result_id}'
                        f'（需要后续多轮复用时可调用 pin_tool_result，rounds=1..10）\n结果：{safe_summary}'
                    )
            except Exception:
                _runtime.logger.warning('[tool_result_pins] candidate unavailable')
        if tool_result.meta.get('continuity_receipt'):
            from core.context_continuity import ReceiptText
            outward = ReceiptText(outward, tool_result.meta['continuity_receipt'])
        return _runtime._execution_outcome("tool_executed", outward)
    except TypeError as e:
        _runtime.log_error("tool_dispatcher.execute", e)
        _runtime._log_execute_failure_context(tool_name, tool_args, origin)
        fallback = _runtime._TOOL_FALLBACKS.get(tool_name, "工具暂时不可用")
        _trace("failed", fallback)
        await _notify_status("failed")
        return _runtime._execution_outcome("tool_failed", fallback)
    except Exception as e:
        from core.character_image_reference import ReferenceError
        from core.image_gen import ImagePresetError, discovery_note
        from core.mcp_client import McpOutcomeUnknown
        if isinstance(e, McpOutcomeUnknown):
            result_text = e.safe_summary()
            _runtime.logger.warning("[tool_dispatcher.execute] MCP 动作结果不明: tool=%s", tool_name)
            _trace("outcome_unknown", result_text)
            await _notify_status("outcome_unknown")
            return _runtime._execution_outcome("tool_failed", result_text)
        _runtime.log_error("tool_dispatcher.execute", e)
        _runtime._log_execute_failure_context(tool_name, tool_args, origin)
        if isinstance(e, _runtime._tool_result_pins.PinError):
            server_reason = e.hint
        elif tool_name in {"generate_image", "pin_character_image_reference"} and isinstance(e, ReferenceError):
            server_reason = str(e)
        elif tool_name == "generate_image" and isinstance(e, ImagePresetError):
            server_reason = "无法使用指定的生图连接。" + discovery_note(char_id)
        elif tool_name == 'generate_image' and getattr(e, 'error_category', '') == 'runware_thirdPartyInsufficientCredits':
            server_reason = 'Runware 的第三方模型付费额度不足。不要重试同一连接；可使用工具定义中另一个确切的连接名。'
        else:
            server_reason = _runtime._mcp_server_reported_error(e)
        if server_reason:
            # Brief 122：服务器业务层明确说了缺什么/为什么拒绝（如"command 参数
            # 必填"），把原话给模型而不是套通用兜底——模型大概率能在下一步自己
            # 带上缺的参数重试，尤其是在 120 号的工具循环里。
            result_text = f"工具调用未成功：{server_reason}"
            _trace("failed", result_text)
            await _notify_status("failed")
            return _runtime._execution_outcome("tool_failed", result_text)
        fallback = _runtime._TOOL_FALLBACKS.get(tool_name, "工具暂时不可用")
        _trace("failed", fallback)
        await _notify_status("failed")
        return _runtime._execution_outcome("tool_failed", fallback)
