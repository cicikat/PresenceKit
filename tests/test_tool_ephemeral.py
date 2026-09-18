"""Tool Ephemeral Status P0 contracts: live UI events must stay out of turns."""
from __future__ import annotations

import asyncio

import pytest

from core.llm_client import ChatTurn


def _make_pipeline():
    from core.pipeline import Pipeline
    return Pipeline.__new__(Pipeline)


def _configure_loop(monkeypatch):
    from core.tool_dispatcher import _TOOL_REGISTRY
    monkeypatch.setitem(_TOOL_REGISTRY, "mcp__demo__call", {"category": "mcp"})
    monkeypatch.setattr("core.model_registry._get_preset_config", lambda: {"presets": {}})
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"tool_loop": {"max_steps": 3, "total_timeout_s": 1, "categories": ["mcp"]}},
    )
    monkeypatch.setattr(
        "core.tool_dispatcher.get_tools_schema",
        lambda categories=None, **kwargs: [
            {"type": "function", "function": {"name": "mcp__demo__call", "parameters": {}}}
        ],
    )
    monkeypatch.setattr("core.character_name_provider.get_char_name", lambda char_id=None: "小星")


def _script_turns(monkeypatch, turns):
    iterator = iter(turns)

    async def _chat_turn(messages, tools, **kwargs):
        if any(t["function"]["name"] == "load_tools_mcp" for t in tools):
            return ChatTurn(content="", tool_calls=[{"id": "discover", "name": "load_tools_mcp", "arguments": {}}],
                            assistant_message={"role": "assistant", "content": None})
        return next(iterator)

    monkeypatch.setattr("core.llm_client.chat_turn", _chat_turn)


def _script_final(monkeypatch, text="自然收尾"):
    async def _chat(messages, **kwargs):
        return text

    monkeypatch.setattr("core.llm_client.chat", _chat)


def _one_tool_then_stop():
    return [
        ChatTurn(
            content="",
            tool_calls=[{"id": "call_1", "name": "mcp__demo__call", "arguments": {}}],
            assistant_message={"role": "assistant", "content": None},
        ),
        ChatTurn(content="done", tool_calls=[], assistant_message={"role": "assistant", "content": "done"}),
    ]


@pytest.mark.asyncio
async def test_confirmation_emits_no_prelude(monkeypatch):
    _configure_loop(monkeypatch)
    _script_turns(monkeypatch, _one_tool_then_stop())
    observed = []

    async def _execute(*args, tool_status_observer=None, **kwargs):
        from core.tool_dispatcher import ToolExecutionOutcome
        await tool_status_observer("pending_confirmation")
        return ToolExecutionOutcome(
            status="confirmation_required",
            confirmation_request="请确认",
        )

    monkeypatch.setattr("core.tool_dispatcher.execute_structured", _execute)

    result = await _make_pipeline().run_agentic_loop(
        [{"role": "user", "content": "执行"}], uid="u1", char_id="c1", session_state=object(),
        tool_event_observer=observed.append,
    )

    assert result == "请确认"
    assert [event.kind for event in observed] == ["pending_confirmation"]


@pytest.mark.asyncio
async def test_waiting_is_emitted_once_after_threshold(monkeypatch):
    _configure_loop(monkeypatch)
    _script_turns(monkeypatch, _one_tool_then_stop())
    _script_final(monkeypatch)
    monkeypatch.setattr("core.pipeline._TOOL_EPHEMERAL_WAITING_AFTER_S", 0.001)
    observed = []

    async def _execute(*args, tool_status_observer=None, **kwargs):
        from core.tool_dispatcher import ToolExecutionOutcome
        await tool_status_observer("queued")
        await asyncio.sleep(0.05)
        await tool_status_observer("finished")
        return ToolExecutionOutcome(status="tool_executed", result="结果")

    monkeypatch.setattr("core.tool_dispatcher.execute_structured", _execute)

    result = await _make_pipeline().run_agentic_loop(
        [{"role": "user", "content": "执行"}], uid="u1", char_id="c1", session_state=object(),
        tool_event_observer=observed.append,
    )

    assert result == "自然收尾"
    assert [event.kind for event in observed] == ["queued", "waiting", "finished"]
    assert observed[0].display_text == ""
    assert observed[0].tts_allowed is False


@pytest.mark.asyncio
async def test_retry_updates_one_status_instance(monkeypatch):
    _configure_loop(monkeypatch)
    _script_turns(monkeypatch, _one_tool_then_stop())
    _script_final(monkeypatch)
    observed = []

    async def _execute(*args, tool_status_observer=None, **kwargs):
        from core.tool_dispatcher import ToolExecutionOutcome
        await tool_status_observer("queued")
        await tool_status_observer("waiting", attempt=2)
        await tool_status_observer("finished", attempt=2)
        return ToolExecutionOutcome(status="tool_executed", result="结果")

    monkeypatch.setattr("core.tool_dispatcher.execute_structured", _execute)

    await _make_pipeline().run_agentic_loop(
        [{"role": "user", "content": "执行"}], uid="u1", char_id="c1", session_state=object(),
        tool_event_observer=observed.append,
    )

    assert {event.status_id for event in observed}.__len__() == 1
    assert [event.attempt for event in observed] == [1, 2, 2]


@pytest.mark.asyncio
async def test_outcome_unknown_never_becomes_finished(monkeypatch):
    _configure_loop(monkeypatch)
    _script_turns(monkeypatch, _one_tool_then_stop())
    _script_final(monkeypatch)
    observed = []

    async def _execute(*args, tool_status_observer=None, **kwargs):
        from core.tool_dispatcher import ToolExecutionOutcome
        await tool_status_observer("queued")
        await tool_status_observer("outcome_unknown")
        return ToolExecutionOutcome(status="outcome_unknown", result="动作可能已经送达")

    monkeypatch.setattr("core.tool_dispatcher.execute_structured", _execute)

    await _make_pipeline().run_agentic_loop(
        [{"role": "user", "content": "执行"}], uid="u1", char_id="c1", session_state=object(),
        tool_event_observer=observed.append,
    )

    assert [event.kind for event in observed] == ["queued", "outcome_unknown"]


@pytest.mark.asyncio
async def test_multiple_tools_keep_serial_index_and_final_reply(monkeypatch):
    _configure_loop(monkeypatch)
    _script_turns(monkeypatch, [
        ChatTurn(
            content="",
            tool_calls=[
                {"id": "call_1", "name": "mcp__demo__call", "arguments": {}},
                {"id": "call_2", "name": "mcp__demo__call", "arguments": {}},
            ],
            assistant_message={"role": "assistant", "content": None},
        ),
        ChatTurn(content="done", tool_calls=[], assistant_message={"role": "assistant", "content": "done"}),
    ])
    _script_final(monkeypatch, "最终自然收尾")
    observed = []

    async def _execute(*args, tool_status_observer=None, **kwargs):
        from core.tool_dispatcher import ToolExecutionOutcome
        await tool_status_observer("queued")
        await tool_status_observer("finished")
        return ToolExecutionOutcome(status="tool_executed", result="结果")

    monkeypatch.setattr("core.tool_dispatcher.execute_structured", _execute)
    memory_writes = []
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", lambda *args, **kwargs: memory_writes.append(args))

    result = await _make_pipeline().run_agentic_loop(
        [{"role": "user", "content": "执行两项"}], uid="u1", char_id="c1", session_state=object(),
        tool_event_observer=observed.append,
    )

    assert result == "最终自然收尾"
    assert [(event.kind, event.index, event.total) for event in observed] == [
        ("queued", 1, 2), ("finished", 1, 2),
        ("queued", 2, 2), ("finished", 2, 2),
    ]
    assert memory_writes == []


def test_expired_status_is_not_deliverable():
    from core.tool_ephemeral import ToolEphemeralEvent

    event = ToolEphemeralEvent(
        status_id="status", kind="waiting", tool_name="mcp__demo__call", ui_label="外部工具",
        index=1, total=1,
        emitted_at=100.0, ttl_s=2.0,
    )

    assert event.should_deliver(now=101.9) is True
    assert event.should_deliver(now=102.0) is False


@pytest.mark.asyncio
async def test_dispatcher_confirmation_and_invalid_args_do_not_queue(monkeypatch):
    from core import tool_dispatcher

    class _State:
        WAITING_CONFIRM = "waiting"
        status = "idle"

        def set_waiting_confirm(self, tool_name, tool_args):
            self.status = self.WAITING_CONFIRM

    async def _unused():
        raise AssertionError("must not execute")

    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "ephemeral_test", {
        "func": _unused,
        "dangerous": True,
        "category": "info",
        "parameters": {"type": "object", "properties": {"value": {}}, "required": ["value"]},
    })
    statuses = []

    invalid = await tool_dispatcher.execute_structured(
        "ephemeral_test", {}, "u1", "u1", False, _State(), origin="assistant_loop", char_id="c1",
        tool_status_observer=lambda kind, **kwargs: statuses.append(kind),
    )
    assert invalid.result.startswith("工具参数不完整")
    assert invalid.confirmation_request is None
    assert statuses == []

    result = await tool_dispatcher.execute_structured(
        "ephemeral_test", {"value": "x"}, "u1", "u1", False, _State(), origin="assistant_loop", char_id="c1",
        tool_status_observer=lambda kind, **kwargs: statuses.append(kind),
    )
    assert result.result is None
    assert result.confirmation_request
    assert statuses == ["pending_confirmation"]


@pytest.mark.asyncio
async def test_desktop_tool_status_uses_only_ephemeral_contract_fields(monkeypatch):
    from channels import desktop_ws
    from core.tool_ephemeral import ToolEphemeralEvent

    sent = []

    async def _send(payload):
        sent.append(payload)
        return True

    monkeypatch.setattr(desktop_ws, "_send_json", _send)
    event = ToolEphemeralEvent(
        status_id="status-1", kind="queued", tool_name="mcp__server__secret_remote_name",
        ui_label="查询设备状态", index=1, total=2, attempt=1, ttl_s=20,
    )

    assert await desktop_ws.push_tool_status(event) is True
    assert sent == [{
        "type": "tool_status", "status_id": "status-1", "kind": "queued",
        "label": "查询设备状态", "index": 1, "total": 2, "attempt": 1,
        "ttl_ms": 20_000,
    }]
