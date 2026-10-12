"""QQ fast path and native tool-loop duplication regressions."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


_USER_ID = "fast_path_owner"
_TIME_MESSAGE = {"user_id": _USER_ID, "content": "现在几点", "sender_name": "tester"}
_PLAIN_MESSAGE = {"user_id": _USER_ID, "content": "你好", "sender_name": "tester"}
_SUCCESS_RESULT = "工具已执行：get_time，结果：当前时间 10:00"


def _make_pipeline():
    from core.memory.scope import MemoryScope

    pipeline = MagicMock()
    pipeline.character = MagicMock()
    pipeline.character.name = "TestChar"
    pipeline.character.presence_ext = {}
    pipeline._current_reality_scope.return_value = MemoryScope.reality_scope(_USER_ID, "test_char")
    pipeline.fetch_context = AsyncMock(return_value={})
    pipeline.build_prompt.return_value = ([{"role": "user", "content": "现在几点"}], {"pending_paths": []})
    pipeline.run_agentic_loop = AsyncMock(return_value="自然语言回复")
    return pipeline


def _patch_handle_message_dependencies(monkeypatch, pipeline, execute_result):
    import core.config_loader as config_loader
    import core.memory.group_context as group_context
    import core.presence as presence
    import core.response_processor as response_processor
    import core.scheduler.loop as scheduler_loop
    import core.scheduler.state_machine as state_machine
    import core.tool_dispatcher as tool_dispatcher
    import main

    monkeypatch.setattr(config_loader, "get_config", lambda: {
        "scheduler": {"owner_id": _USER_ID},
        "tool_loop": {"enabled": True, "categories": ["info"], "exclude_tools": []},
    })
    monkeypatch.setattr(scheduler_loop, "mark_user_active", lambda: None)
    monkeypatch.setattr(state_machine, "notify_owner_turn", lambda uid: None)
    monkeypatch.setattr(presence, "update_last_message", lambda uid: None)
    monkeypatch.setattr(group_context, "append", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_dispatcher, "_TOOL_REGISTRY", {
        "get_time": {
            "keywords": ["几点"],
            "parameters": {"type": "object", "properties": {}},
            "category": "info",
            "dangerous": False,
            "description": "",
        },
    })
    monkeypatch.setattr(tool_dispatcher, "tool_loop_active", lambda uid: True)
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *args, **kwargs: True)
    status = "tool_executed" if execute_result == _SUCCESS_RESULT else "tool_failed"
    execute = AsyncMock(return_value=tool_dispatcher.ToolExecutionOutcome(status, execute_result))
    monkeypatch.setattr(tool_dispatcher, "execute_structured", execute)
    monkeypatch.setattr(response_processor, "process", lambda reply, name: [reply])
    monkeypatch.setattr(response_processor, "process_memory_copy", lambda reply, name: [reply])
    monkeypatch.setattr(main, "_pipeline", pipeline)
    monkeypatch.setattr(main, "_qq_reality_reply_adapter", AsyncMock())
    return execute


@pytest.mark.asyncio
async def test_successful_fast_path_excludes_get_time_from_native_loop(monkeypatch, caplog):
    import main

    caplog.set_level(logging.INFO)
    pipeline = _make_pipeline()
    execute = _patch_handle_message_dependencies(monkeypatch, pipeline, _SUCCESS_RESULT)

    await main.handle_message(_TIME_MESSAGE)

    execute.assert_awaited_once()
    assert pipeline.build_prompt.call_args.kwargs["tool_result"] == _SUCCESS_RESULT
    assert pipeline.run_agentic_loop.call_args.kwargs["exclude_tools"] == {"get_time"}
    assert "fast_path_matched=True" in caplog.text
    assert "fast_path_tool_excluded_from_loop=True" in caplog.text


@pytest.mark.asyncio
async def test_fast_path_miss_leaves_get_time_available_to_native_fc(monkeypatch):
    import main

    pipeline = _make_pipeline()
    execute = _patch_handle_message_dependencies(monkeypatch, pipeline, None)

    await main.handle_message(_PLAIN_MESSAGE)

    execute.assert_not_awaited()
    assert pipeline.run_agentic_loop.call_args.kwargs["exclude_tools"] == set()


@pytest.mark.asyncio
async def test_failed_fast_path_does_not_exclude_get_time(monkeypatch):
    import main

    pipeline = _make_pipeline()
    execute = _patch_handle_message_dependencies(monkeypatch, pipeline, "工具暂时不可用")

    await main.handle_message(_TIME_MESSAGE)

    execute.assert_awaited_once()
    assert pipeline.run_agentic_loop.call_args.kwargs["exclude_tools"] == set()


@pytest.mark.asyncio
async def test_successful_fast_path_executes_get_time_once(monkeypatch):
    import main

    pipeline = _make_pipeline()
    execute = _patch_handle_message_dependencies(monkeypatch, pipeline, _SUCCESS_RESULT)

    await main.handle_message(_TIME_MESSAGE)

    assert execute.await_count == 1


@pytest.fixture(autouse=True)
def admission_config(monkeypatch):
    from core import config_loader
    monkeypatch.setattr("core.qq_admission.get_config", lambda: config_loader.get_config())
