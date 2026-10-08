"""Channel-neutral Path A pre-tool routing and probe contract."""

from __future__ import annotations

import inspect
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from core import llm_client, tool_dispatcher

logger = logging.getLogger(__name__)

FAST_PATH_TOOL_ALLOWLIST: frozenset[str] = frozenset({"get_time"})
_PROBE_RAW_LIMIT = 2000
_OBS_TEXT_LIMIT = 500


@dataclass(frozen=True)
class RoutedToolResult:
    name: str
    status: str
    result: str | None = None
    arguments: dict = field(default_factory=dict)
    generated_at: float = field(default_factory=time.time)
    validity: str = "current_turn"


@dataclass
class PreToolRouteResult:
    route: str
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[RoutedToolResult] = field(default_factory=list)
    prompt_tool_result: str | None = None
    confirmation_request: str | None = None
    missing_parameter_request: str | None = None
    direct_response: str | None = None
    tools_available: list[str] = field(default_factory=list)
    channel: str = ""
    fast_path_matched: bool = False
    matched_keyword: str | None = None
    probe_used: bool = False
    probe_encoding: str | None = None
    selected_tool: str | None = None
    execution_status: str = "no_tool_selected"
    fast_failed_then_loop_retry: bool = False
    exclude_tools: set[str] = field(default_factory=set)
    probe_response_raw: str = ""
    probe_system: str = ""
    probe_context: str = ""
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    must_call_tool: bool = False
    required_tool_names: set[str] = field(default_factory=set)
    tool_result_generated_at: float | None = None
    exposure_path: str = "path_a"
    exposure_source: str = ""
    exposure_categories: list[str] = field(default_factory=list)

    @property
    def has_successful_tool_call(self) -> bool:
        return any(item.status == "tool_executed" for item in self.tool_results)

    @property
    def should_stop_for_user_input(self) -> bool:
        return bool(
            self.confirmation_request
            or self.missing_parameter_request
            or self.direct_response
        )

    def observation(self, trusted_user_text: str) -> dict:
        return {
            "channel": self.channel,
            "exposure_path": self.exposure_path,
            "exposure_source": self.exposure_source,
            "exposure_categories": list(self.exposure_categories),
            "route": self.route,
            "fast_path_matched": self.fast_path_matched,
            "matched_keyword": self.matched_keyword,
            "probe_used": self.probe_used,
            "probe_encoding": self.probe_encoding,
            "tools_available_count": len(self.tools_available),
            "tools_available": list(self.tools_available),
            "selected_tool": self.selected_tool,
            "execution_status": self.execution_status,
            "fast_failed_then_loop_retry": self.fast_failed_then_loop_retry,
            "confirmation_required": bool(self.confirmation_request),
            "missing_parameters": bool(self.missing_parameter_request),
            "user_message": trusted_user_text[:_OBS_TEXT_LIMIT],
            "probe_system": self.probe_system[:_PROBE_RAW_LIMIT],
            "probe_context": self.probe_context[:_PROBE_RAW_LIMIT],
            "probe_response_raw": self.probe_response_raw[:_PROBE_RAW_LIMIT],
            "tool_calls": [
                {
                    "name": call.get("name", ""),
                    "argument_keys": sorted((call.get("arguments") or {}).keys()),
                }
                for call in self.tool_calls
            ],
            "tool_results": [
                {
                    "name": item.name,
                    "status": item.status,
                    "generated_at": item.generated_at,
                    "validity": item.validity,
                    "result_preview": (item.result or "")[:_OBS_TEXT_LIMIT],
                }
                for item in self.tool_results
            ],
        }


def fast_path_match(
    user_text: str,
    *,
    available_tool_names: set[str] | None = None,
) -> tuple[str, str] | None:
    """Return an explicitly allowlisted, zero-required, side-effect-free match."""
    for name in FAST_PATH_TOOL_ALLOWLIST:
        if available_tool_names is not None and name not in available_tool_names:
            continue
        spec = tool_dispatcher._TOOL_REGISTRY.get(name, {})
        required = (spec.get("parameters") or {}).get("required") or []
        if required or tool_dispatcher.is_side_effect_tool(name):
            continue
        for keyword in spec.get("keywords", []):
            if keyword and keyword in user_text:
                return name, keyword
    return None


def _required_tool_matches(
    user_text: str,
) -> set[str]:
    """Find explicit registry keyword intents without executing side effects."""
    matches: set[str] = set()
    for name, spec in tool_dispatcher._TOOL_REGISTRY.items():
        keywords = spec.get("keywords") or []
        if any(keyword and keyword in user_text for keyword in keywords):
            # Keep the intent even when exposure filtered this tool out.  The
            # output guard must still prevent a false completion claim.
            matches.add(name)
    return matches


def _probe_reference_block(uid: str, char_id: str) -> str:
    from core.character_name_provider import get_char_name
    from core.memory import short_term

    try:
        char_name = get_char_name(char_id)
    except Exception:
        # Probe context is optional reference data. A display-name lookup must
        # not make ordinary chat unavailable.
        char_name = "助手"
    lines: list[str] = []
    for message in short_term.load(uid, char_id=char_id)[-4:]:
        if message.get("_source") == "trigger_stub":
            continue
        text = str(message.get("content") or "").strip()
        if message.get("role") == "assistant":
            text = re.sub(r"（[^）]*）|\([^)]*\)", "", text).strip()
            if text:
                lines.append(f"{char_name}：{text}")
        elif text:
            lines.append(f"用户：{text}")
    return "\n".join(lines)


def _configured_probe_encoding(char_id: str) -> str:
    try:
        from core.model_registry import get_model_client

        mode = get_model_client("probe", char_id=char_id).tool_call_mode
        return "xml" if mode == "xml_fallback" else "function_calling"
    except Exception:
        return "function_calling"


async def _maybe_call(callback: Callable[[], Awaitable[None] | None] | None) -> None:
    if callback is None:
        return
    value = callback()
    if inspect.isawaitable(value):
        await value


def _capture(uid: str, result: PreToolRouteResult, trusted_user_text: str) -> None:
    try:
        from core.observe.probe_capture import capture_probe

        capture_probe(uid, result.observation(trusted_user_text))
    except Exception:
        logger.debug("[pretool_router] probe capture failed", exc_info=True)


def _missing_request(name: str, missing: tuple[str, ...]) -> str:
    key = missing[0] if missing else "参数"
    spec = tool_dispatcher._TOOL_REGISTRY.get(name, {})
    prop = ((spec.get("parameters") or {}).get("properties") or {}).get(key, {})
    label = str(prop.get("description") or key)
    return f"还需要你补充：{label}"


async def _execute_selected(
    result: PreToolRouteResult,
    call: dict,
    *,
    uid: str,
    char_id: str,
    target_id: str,
    is_group: bool,
    session_state,
    trusted_user_text: str,
    before_execute: Callable[[], Awaitable[None] | None] | None,
    allowed_tool_names: frozenset[str] | None = None,
) -> None:
    from core.memory.tool_read_log import detect_bypass_intent

    await _maybe_call(before_execute)
    name = call.get("name", "")
    arguments = call.get("arguments", {})
    if allowed_tool_names is not None and name not in allowed_tool_names:
        outcome = tool_dispatcher.ToolExecutionOutcome(
            status="tool_failed", result="tool capability unavailable for this caller",
        )
    else:
        outcome = await tool_dispatcher.execute_structured(
            tool_name=name,
            tool_args=arguments,
            user_id=uid,
            target_id=target_id,
            is_group=is_group,
            session_state=session_state,
            origin="user_live",
            char_id=char_id,
            bypass_read_log=detect_bypass_intent(trusted_user_text),
            allowed_tool_names=allowed_tool_names,
        )
    result.selected_tool = name
    result.execution_status = outcome.status
    result.tool_result_generated_at = time.time()
    result.tool_results.append(
        RoutedToolResult(
            name=name,
            status=outcome.status,
            result=outcome.result,
            arguments=dict(arguments),
            generated_at=result.tool_result_generated_at,
            validity=(
                "current_turn" if outcome.status == "tool_executed"
                else "outcome_unknown" if outcome.status == "outcome_unknown"
                else "execution_failed"
            ),
        )
    )
    if outcome.status == "confirmation_required":
        result.confirmation_request = outcome.confirmation_request
    elif outcome.status == "missing_parameters":
        request = _missing_request(name, outcome.missing_parameters)
        result.missing_parameter_request = request
        if outcome.missing_parameters:
            session_state.set_waiting_input(name, dict(arguments), outcome.missing_parameters[0])
    elif outcome.result:
        result.prompt_tool_result = outcome.result


async def route_pretool(
    trusted_user_text: str,
    uid: str,
    char_id: str,
    channel: str,
    target_id: str,
    is_group: bool,
    session_state,
    *,
    tool_loop_enabled: bool,
    categories: list[str] | None = None,
    exposure_path: str = "path_a",
    provenance_channel: str | None = None,
    before_execute: Callable[[], Awaitable[None] | None] | None = None,
    allowed_tool_names: frozenset[str] | None = None,
) -> PreToolRouteResult:
    """Run the shared fast-match/probe/execute pre-tool contract."""
    effective_channel = provenance_channel or channel
    from core.tool_exposure import (
        filter_schemas as filter_exposure_schemas,
        resolve as resolve_exposure,
    )
    exposure = resolve_exposure(exposure_path, char_id=char_id)
    effective_categories = list(categories) if categories is not None else list(exposure.categories)
    # Preserve the registry helper's long-standing call shape for local test
    # and plugin compatibility, then apply the same character gates explicitly.
    schemas = tool_dispatcher.get_tools_schema(categories=effective_categories)
    from core.growth.mcp_proficiency import filter_schemas as filter_growth_schemas
    from core.self_management.policy import tool_allowed

    schemas = [
        schema
        for schema in filter_growth_schemas(schemas, char_id=char_id)
        if tool_allowed(
            uid,
            char_id,
            str((schema.get("function") or schema).get("name") or ""),
        )
    ]
    if allowed_tool_names is not None:
        schemas = [
            schema for schema in schemas
            if str((schema.get("function") or schema).get("name") or "") in allowed_tool_names
        ]
    schemas = filter_exposure_schemas(schemas, exposure)
    available = [
        str((schema.get("function") or schema).get("name") or "")
        for schema in schemas
    ]
    available = [name for name in available if name]
    result = PreToolRouteResult(
        route="no_tool", channel=effective_channel, tools_available=available,
        exposure_path=exposure_path,
        exposure_source=exposure.source,
        exposure_categories=effective_categories,
    )
    result.required_tool_names = _required_tool_matches(trusted_user_text)
    result.must_call_tool = bool(result.required_tool_names)

    # Pending input is part of the same channel-neutral contract.  Clear the
    # old state before re-execution so execute() can establish a new confirm or
    # missing-input state without the caller erasing it afterwards.
    if session_state.status == session_state.WAITING_CONFIRM:
        if trusted_user_text.strip() != "确认":
            session_state.clear()
            result.direct_response = "好的，已取消。"
            _capture(uid, result, trusted_user_text)
            return result
        call = {
            "name": session_state.pending_tool or "",
            "arguments": dict(session_state.pending_args or {}),
        }
        result.tool_calls = [call]
        await _execute_selected(
            result, call, uid=uid, char_id=char_id, target_id=target_id,
            is_group=is_group, session_state=session_state,
            trusted_user_text=trusted_user_text, before_execute=before_execute,
            allowed_tool_names=allowed_tool_names,
        )
        session_state.clear()
        _capture(uid, result, trusted_user_text)
        return result

    if session_state.status == session_state.WAITING_INPUT:
        pending_tool = session_state.pending_tool or ""
        pending_args = dict(session_state.pending_args or {})
        if session_state.pending_arg_key:
            pending_args[session_state.pending_arg_key] = trusted_user_text
        session_state.clear()
        call = {"name": pending_tool, "arguments": pending_args}
        result.tool_calls = [call]
        await _execute_selected(
            result, call, uid=uid, char_id=char_id, target_id=target_id,
            is_group=is_group, session_state=session_state,
            trusted_user_text=trusted_user_text, before_execute=before_execute,
            allowed_tool_names=allowed_tool_names,
        )
        _capture(uid, result, trusted_user_text)
        return result

    available_set = set(available)
    matched = fast_path_match(
        trusted_user_text, available_tool_names=available_set,
    )
    if matched:
        name, keyword = matched
        result.route = "fast_match"
        result.fast_path_matched = True
        result.matched_keyword = keyword
        result.tool_calls = [{"name": name, "arguments": {}}]
        logger.info(
            "[pretool_route] channel=%s route=fast_match fast_path_matched=True "
            "uid=%s selected_tool=%s matched_keyword=%r",
            effective_channel,
            uid,
            name,
            keyword,
        )
        await _execute_selected(
            result, result.tool_calls[0], uid=uid, char_id=char_id,
            target_id=target_id, is_group=is_group, session_state=session_state,
            trusted_user_text=trusted_user_text, before_execute=before_execute,
            allowed_tool_names=allowed_tool_names,
        )
        if result.execution_status == "tool_executed" and tool_loop_enabled:
            result.exclude_tools.add(name)
            logger.info(
                "[pretool_route] fast_path_tool_excluded_from_loop=True uid=%s tool=%s",
                uid,
                name,
            )
        elif result.execution_status == "tool_failed" and tool_loop_enabled:
            result.fast_failed_then_loop_retry = True
            logger.info(
                "[pretool_route] fast_failed_then_loop_retry=True uid=%s tool=%s",
                uid,
                name,
            )
        _capture(uid, result, trusted_user_text)
        return result

    if tool_loop_enabled:
        result.route = "skipped_for_tool_loop"
        _capture(uid, result, trusted_user_text)
        return result
    if not schemas:
        _capture(uid, result, trusted_user_text)
        return result

    result.route = "probe"
    result.probe_used = True
    result.probe_encoding = _configured_probe_encoding(char_id)
    from core.memory import user_profile

    location = user_profile.load(uid, char_id=char_id).get("location") or "杭州"
    result.probe_context = _probe_reference_block(uid, char_id)
    result.probe_system = tool_dispatcher.get_probe_prompt(
        location,
        categories=effective_categories,
        allowed_tool_names=available_set,
    )
    if result.probe_context:
        result.probe_system += (
            "\n\n【最近对话（仅供解析指代词等，不要续写、不要表演、不要进入角色）】\n"
            + result.probe_context
        )
    try:
        from core.decision_probe import probe
        probe_raw = await probe(
            [
                {"role": "system", "content": result.probe_system},
                {"role": "user", "content": trusted_user_text},
            ],
            schemas=schemas,
            char_id=char_id,
        )
    except Exception:
        logger.warning("[pretool_router] probe unavailable", exc_info=True)
        result.execution_status = "probe_unavailable"
        _capture(uid, result, trusted_user_text)
        return result

    result.probe_response_raw = probe_raw if isinstance(probe_raw, str) else ""
    parsed = llm_client.parse_probe_response(
        probe_raw, allowed_tool_names=available_set,
    )
    result.probe_encoding = parsed.encoding or result.probe_encoding
    result.execution_status = parsed.status
    result.tool_calls = parsed.tool_calls
    if parsed.status != "tool_selected":
        _capture(uid, result, trusted_user_text)
        return result

    for call in parsed.tool_calls:
        await _execute_selected(
            result, call, uid=uid, char_id=char_id,
            target_id=target_id, is_group=is_group, session_state=session_state,
            trusted_user_text=trusted_user_text, before_execute=before_execute,
            allowed_tool_names=allowed_tool_names,
        )
        if result.should_stop_for_user_input or result.prompt_tool_result:
            break
    _capture(uid, result, trusted_user_text)
    return result
