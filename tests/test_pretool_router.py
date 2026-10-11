from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


class _State:
    NORMAL = "normal"
    WAITING_CONFIRM = "waiting_confirm"
    WAITING_INPUT = "waiting_input"

    def __init__(self):
        self.status = self.NORMAL
        self.pending_tool = None
        self.pending_args = None
        self.pending_arg_key = None

    def set_waiting_confirm(self, name, arguments):
        self.status = self.WAITING_CONFIRM
        self.pending_tool = name
        self.pending_args = dict(arguments)

    def set_waiting_input(self, name, arguments, key):
        self.status = self.WAITING_INPUT
        self.pending_tool = name
        self.pending_args = dict(arguments)
        self.pending_arg_key = key

    def clear(self):
        self.__init__()


def _schema(name: str, *, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "",
            "parameters": {
                "type": "object",
                "properties": {
                    key: {"type": "string", "description": key}
                    for key in (required or [])
                },
                "required": required or [],
            },
        },
    }


def _patch_exposure(monkeypatch, schemas: list[dict]) -> None:
    from core import tool_dispatcher

    monkeypatch.setattr(tool_dispatcher, "get_tools_schema", lambda categories=None: schemas)
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *args, **kwargs: True)


@pytest.mark.asyncio
async def test_default_path_a_exposure_filters_schemas_without_channel_branching(monkeypatch):
    from core import tool_dispatcher
    from core.pretool_router import route_pretool

    seen_categories = []

    def _schemas(categories=None):
        seen_categories.append(categories)
        return [_schema("fs_list"), _schema("get_time")]

    monkeypatch.setattr(tool_dispatcher, "get_tools_schema", _schemas)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"tool_exposure": {"path_a": {
            "categories": ["fs"], "tools": ["fs_list"],
        }}},
    )
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *args, **kwargs: True)

    result = await route_pretool(
        "列一下文件", "u1", "c1", "qq", "u1", False, _State(),
        tool_loop_enabled=True,
    )

    assert seen_categories == [["fs"]]
    assert result.route == "skipped_for_tool_loop"
    assert result.tools_available == ["fs_list"]
    assert result.observation("列一下文件")["exposure_categories"] == ["fs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,categories", [("qq", ["info"]), ("desktop", ["info", "desktop"])])
async def test_get_time_fast_match_is_channel_consistent(monkeypatch, channel, categories):
    from core import tool_dispatcher
    from core.pretool_router import route_pretool

    _patch_exposure(monkeypatch, [_schema("get_time")])
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "get_time", {
        "keywords": ["几点"],
        "category": "info",
        "description": "",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "dangerous": False,
    })
    monkeypatch.setattr(tool_dispatcher, "is_side_effect_tool", lambda name: False)
    monkeypatch.setattr(tool_dispatcher, "execute_structured", AsyncMock(return_value=
        tool_dispatcher.ToolExecutionOutcome(
            status="tool_executed",
            result="工具已执行：get_time，结果：10:00",
        )
    ))

    result = await route_pretool(
        "现在几点了", "u1", "c1", channel, "u1", False, _State(),
        tool_loop_enabled=False, categories=categories,
    )

    assert result.route == "fast_match"
    assert result.selected_tool == "get_time"
    assert result.execution_status == "tool_executed"
    assert result.prompt_tool_result.endswith("10:00")


@pytest.mark.asyncio
async def test_path_c_skips_probe_but_fast_success_excludes_duplicate(monkeypatch):
    from core import llm_client, tool_dispatcher
    from core.pretool_router import route_pretool

    _patch_exposure(monkeypatch, [_schema("get_time")])
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "get_time", {
        "keywords": ["几点"], "category": "info", "description": "",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "dangerous": False,
    })
    monkeypatch.setattr(tool_dispatcher, "is_side_effect_tool", lambda name: False)
    monkeypatch.setattr(tool_dispatcher, "execute_structured", AsyncMock(return_value=
        tool_dispatcher.ToolExecutionOutcome("tool_executed", "time result")
    ))
    probe = AsyncMock(return_value="")
    monkeypatch.setattr(llm_client, "chat", probe)

    result = await route_pretool(
        "几点了", "u1", "c1", "desktop", "u1", False, _State(),
        tool_loop_enabled=True, categories=["info", "desktop"],
    )
    assert result.exclude_tools == {"get_time"}
    assert result.prompt_tool_result == "time result"
    probe.assert_not_awaited()

    skipped = await route_pretool(
        "你好", "u1", "c1", "desktop", "u1", False, _State(),
        tool_loop_enabled=True, categories=["info", "desktop"],
    )
    assert skipped.route == "skipped_for_tool_loop"
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_fast_match_allows_loop_retry_and_marks_observation(monkeypatch):
    from core import tool_dispatcher
    from core.pretool_router import route_pretool

    _patch_exposure(monkeypatch, [_schema("get_time")])
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "get_time", {
        "keywords": ["几点"], "category": "info", "description": "",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "dangerous": False,
    })
    monkeypatch.setattr(tool_dispatcher, "is_side_effect_tool", lambda name: False)
    monkeypatch.setattr(tool_dispatcher, "execute_structured", AsyncMock(return_value=
        tool_dispatcher.ToolExecutionOutcome("tool_failed", "unavailable")
    ))

    result = await route_pretool(
        "几点了", "u1", "c1", "qq", "u1", False, _State(),
        tool_loop_enabled=True, categories=["info"],
    )
    assert result.exclude_tools == set()
    assert result.fast_failed_then_loop_retry is True
    assert result.observation("几点了")["fast_failed_then_loop_retry"] is True


def test_strict_xml_probe_adapter_rejects_malformed_invalid_and_unknown():
    from core.llm_client import parse_probe_response

    valid = parse_probe_response(
        '<tool_call>{"name":"weather","arguments":{"city":"A"}}</tool_call>',
        allowed_tool_names={"weather"},
    )
    assert valid.status == "tool_selected"
    assert valid.encoding == "xml"
    assert valid.tool_calls == [{"name": "weather", "arguments": {"city": "A"}}]

    assert parse_probe_response(
        '<tool_call>{"name":"weather","arguments":{}}',
        allowed_tool_names={"weather"},
    ).status == "probe_parse_failed"
    assert parse_probe_response(
        '<tool_call>{bad json}</tool_call>',
        allowed_tool_names={"weather"},
    ).status == "probe_parse_failed"
    assert parse_probe_response(
        '<tool_call>{"name":"hidden","arguments":{}}</tool_call>',
        allowed_tool_names={"weather"},
    ).status == "tool_unknown"


@pytest.mark.asyncio
async def test_probe_parse_failure_is_fail_soft_and_raw_never_becomes_prompt_result(monkeypatch):
    from core import llm_client, tool_dispatcher
    from core.pretool_router import route_pretool

    _patch_exposure(monkeypatch, [_schema("weather", required=["city"])])
    monkeypatch.setattr("core.memory.user_profile.load", lambda *args, **kwargs: {})
    monkeypatch.setattr("core.memory.short_term.load", lambda *args, **kwargs: [])
    monkeypatch.setattr(tool_dispatcher, "get_probe_prompt", lambda *args, **kwargs: "probe")
    raw = '<tool_call>{"name":"weather","arguments":{}}'
    monkeypatch.setattr(llm_client, "chat", AsyncMock(return_value=raw))
    execute = AsyncMock()
    monkeypatch.setattr(tool_dispatcher, "execute_structured", execute)

    result = await route_pretool(
        "天气呢", "u1", "c1", "qq", "u1", False, _State(),
        tool_loop_enabled=False, categories=["info"],
    )
    assert result.route == "probe"
    assert result.execution_status == "probe_parse_failed"
    assert result.prompt_tool_result is None
    assert raw not in (result.prompt_tool_result or "")
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_calls_try_in_order_until_one_produces_a_result(monkeypatch):
    from core import llm_client, tool_dispatcher
    from core.pretool_router import route_pretool

    _patch_exposure(monkeypatch, [_schema("first"), _schema("second")])
    monkeypatch.setattr("core.memory.user_profile.load", lambda *args, **kwargs: {})
    monkeypatch.setattr("core.memory.short_term.load", lambda *args, **kwargs: [])
    monkeypatch.setattr(tool_dispatcher, "get_probe_prompt", lambda *args, **kwargs: "probe")
    monkeypatch.setattr(llm_client, "chat", AsyncMock(return_value=(
        '__TOOL_CALL__:[{"name":"first","arguments":{}},'
        '{"name":"second","arguments":{}}]'
    )))
    execute = AsyncMock(side_effect=[
        tool_dispatcher.ToolExecutionOutcome("tool_failed"),
        tool_dispatcher.ToolExecutionOutcome("tool_executed", "second result"),
    ])
    monkeypatch.setattr(tool_dispatcher, "execute_structured", execute)

    result = await route_pretool(
        "执行可用操作", "u1", "c1", "desktop", "u1", False, _State(),
        tool_loop_enabled=False, categories=["info", "desktop"],
    )

    assert execute.await_count == 2
    assert [item.name for item in result.tool_results] == ["first", "second"]
    assert result.prompt_tool_result == "second result"
    assert result.execution_status == "tool_executed"


@pytest.mark.asyncio
async def test_missing_parameters_and_confirmation_are_distinct(monkeypatch):
    from core import llm_client, tool_dispatcher
    from core.pretool_router import route_pretool

    _patch_exposure(monkeypatch, [_schema("weather", required=["city"])])
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "weather", {
        "description": "", "category": "info",
        "parameters": _schema("weather", required=["city"])["function"]["parameters"],
    })
    monkeypatch.setattr("core.memory.user_profile.load", lambda *args, **kwargs: {})
    monkeypatch.setattr("core.memory.short_term.load", lambda *args, **kwargs: [])
    monkeypatch.setattr(tool_dispatcher, "get_probe_prompt", lambda *args, **kwargs: "probe")
    monkeypatch.setattr(llm_client, "chat", AsyncMock(return_value=
        '__TOOL_CALL__:[{"name":"weather","arguments":{}}]'
    ))
    state = _State()
    monkeypatch.setattr(tool_dispatcher, "execute_structured", AsyncMock(return_value=
        tool_dispatcher.ToolExecutionOutcome(
            "missing_parameters", "missing", missing_parameters=("city",),
        )
    ))
    missing = await route_pretool(
        "查天气", "u1", "c1", "qq", "u1", False, state,
        tool_loop_enabled=False, categories=["info"],
    )
    assert missing.missing_parameter_request
    assert missing.confirmation_request is None
    assert state.status == state.WAITING_INPUT

    state = _State()
    async def _confirm(**kwargs):
        state.set_waiting_confirm("weather", {"city": "A"})
        return tool_dispatcher.ToolExecutionOutcome(
            "confirmation_required", confirmation_request="confirm?",
        )

    monkeypatch.setattr(tool_dispatcher, "execute_structured", _confirm)
    monkeypatch.setattr(llm_client, "chat", AsyncMock(return_value=
        '__TOOL_CALL__:[{"name":"weather","arguments":{"city":"A"}}]'
    ))
    confirmation = await route_pretool(
        "查 A 天气", "u1", "c1", "desktop", "u1", False, state,
        tool_loop_enabled=False, categories=["info"],
    )
    assert confirmation.confirmation_request == "confirm?"
    assert confirmation.missing_parameter_request is None
    assert state.status == state.WAITING_CONFIRM


def test_path_a_main_generation_calls_do_not_supply_tools_schema():
    from pathlib import Path

    main_src = Path("main.py").read_text(encoding="utf-8")
    chat_src = Path("admin/routers/chat.py").read_text(encoding="utf-8")
    assert "_pipeline.run_llm(messages, tools=" not in main_src
    assert "pipeline.run_llm(messages, tools=" not in chat_src
    assert "pipeline.run_llm_stream(messages, tools=" not in chat_src
