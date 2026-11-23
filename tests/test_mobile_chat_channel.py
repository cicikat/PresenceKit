"""Contract tests for the separate mobile reality-chat endpoint."""

from core.owner_chat.service import run_owner_chat_turn
import inspect
from types import SimpleNamespace

import pytest


async def test_mobile_chat_uses_mobile_provenance_and_durable_mirror(monkeypatch):
    from admin.routers import chat, mobile

    calls = []
    notified = []

    async def fake_owner_turn(message, provenance_channel, **kwargs):
        calls.append((message, provenance_channel, kwargs))
        return {"reply": "收到", "turn_id": "turn-mobile", "msg_id": "turn-mobile"}

    monkeypatch.setattr('core.owner_chat.service.run_owner_chat_turn', fake_owner_turn)
    monkeypatch.setattr('admin.reality_admission.require_reality_turn', lambda uid: None)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner"}},
    )
    monkeypatch.setattr(
        "core.scheduler.sensor_events.notify_chat_happened",
        lambda: notified.append(True),
    )

    result = await mobile.mobile_chat(
        {"message": "你好", "reply_to": {"text": "上一条", "ts": 1}},
        _auth=True,
    )

    assert result["msg_id"] == "turn-mobile"
    assert notified == [True]
    assert calls == [
        (
            "你好",
            "mobile",
            {
                "live_origin_channel": "mobile",
                "durable_mobile_mirror": True,
                "reply_to": {"text": "上一条", "ts": 1},
            },
        )
    ]


async def test_desktop_chat_keeps_desktop_provenance(monkeypatch):
    from admin.routers import chat

    calls = []

    async def fake_owner_turn(message, provenance_channel, **kwargs):
        calls.append((message, provenance_channel, kwargs))
        return {"reply": "收到"}

    monkeypatch.setattr('core.owner_chat.service.run_owner_chat_turn', fake_owner_turn)
    monkeypatch.setattr('admin.reality_admission.require_reality_turn', lambda uid: None)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner"}},
    )
    monkeypatch.setattr("core.scheduler.sensor_events.notify_chat_happened", lambda: None)

    await chat.desktop_chat({"message": "你好"}, _auth=True)

    assert calls == [("你好", "desktop", {"reply_to": None, "trusted_user_text": "你好"})]


def test_mobile_probe_prompt_excludes_desktop_tools(monkeypatch):
    from core import tool_dispatcher

    monkeypatch.setattr('core.character_name_provider.get_active_char_name', lambda: "Companion")
    prompt = tool_dispatcher.get_probe_prompt("测试位置", categories=["info"])

    desktop_tools = [
        name
        for name, spec in tool_dispatcher._TOOL_REGISTRY.items()
        if spec.get("category") in {"desktop", "system", "phone_control"}
    ]
    assert desktop_tools
    assert all(name not in prompt for name in desktop_tools)


async def test_mobile_owner_turn_uses_mobile_context_without_desktop_stream(monkeypatch):
    from admin.routers import chat

    class _Character:
        name = "Companion"

    class _Pipeline:
        character = _Character()

        def _current_reality_scope(self, _uid):
            return SimpleNamespace(character_id="companion")

        async def fetch_context(self, *_args, **_kwargs):
            return {}

        def build_prompt(self, _uid, _message, _context, **kwargs):
            prompt_channels.append(kwargs["channel"])
            return [], {}

        async def run_llm(self, _messages):
            return "<say><hl>手机</hl><big>回复</big></say>"

        async def run_llm_stream(self, _messages, **_kwargs):
            raise AssertionError("mobile provenance must not enable desktop stream")

    class _Channel:
        def __init__(self):
            self.active = False

        def set_active(self, value):
            self.active = value

    prompt_channels = []
    probe_channels = []
    capture_origins = []
    sink_calls = []
    mobile_channel = _Channel()
    desktop_channel = _Channel()

    async def fake_probe(_message, _uid, *, char_id, provenance_channel):
        assert char_id == "companion"
        probe_channels.append(provenance_channel)
        return None

    async def fake_sink(**kwargs):
        sink_calls.append(kwargs)
        return SimpleNamespace(
            turn_id="turn-mobile",
            written_to_memory=True,
            artifacts=[],
            emotion="neutral",
        )

    monkeypatch.setattr("core.pipeline_registry.get", lambda: _Pipeline())
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner"}},
    )
    monkeypatch.setattr("core.conversation_activity.mark_user_active", lambda: None)
    monkeypatch.setattr("core.scheduler.state_machine.notify_owner_turn", lambda _uid: None)
    monkeypatch.setattr("core.tool_dispatcher.tool_loop_active", lambda _uid: False)
    monkeypatch.setattr('core.owner_chat.prepare._probe_and_execute_tools', fake_probe)
    monkeypatch.setattr(
        "core.observe.prompt_capture.set_capture_origin",
        lambda origin: capture_origins.append(origin),
    )
    monkeypatch.setattr("channels.ui_push.any_connected", lambda: True)
    monkeypatch.setattr(
        "channels.registry.get",
        lambda name: {"mobile": mobile_channel, "desktop": desktop_channel}.get(name),
    )
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_sink)
    monkeypatch.setattr("core.coplay.session.is_active", lambda *_args, **_kwargs: False)
    result = await run_owner_chat_turn(
        "你好",
        "mobile",
        live_origin_channel="mobile",
        durable_mobile_mirror=True,
    )

    assert result["turn_id"] == result["msg_id"] == "turn-mobile"
    assert result["reply"] == "手机回复"
    assert result["display_text"] == "<hl>手机</hl><big>回复</big>"
    assert "affection" not in result
    assert "level" not in result
    assert prompt_channels == ["mobile"]
    assert probe_channels == ["mobile"]
    assert len(capture_origins) == 1
    assert capture_origins[0]["origin"] == "mobile"
    assert capture_origins[0]["user_authored"] is True
    assert mobile_channel.active is True
    assert desktop_channel.active is False
    assert sink_calls[0]["exclude_origin_channel"] == "mobile"
    assert sink_calls[0]["durable_mobile_mirror"] is True


async def test_mobile_chat_dream_guard_blocks_before_owner_turn(sandbox, monkeypatch):
    from core.dream.dream_state import DreamStatus
    from fastapi import HTTPException

    from core.dream.dream_state import write_state
    write_state("owner", {"status": DreamStatus.DREAM_ACTIVE.value, "user_id": "owner"})
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner"}},
    )

    from admin.routers import mobile

    with pytest.raises(HTTPException) as exc:
        await mobile.mobile_chat({"message": "你好"}, _auth=True)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("empty_reply", [None, "", " \n\t", "__buffered_failure__", "__stream_failure__"])
@pytest.mark.parametrize("origin", ["mobile", "desktop"])
@pytest.mark.parametrize("loop_active", [False, True])
async def test_owner_chat_recovers_after_empty_reply(monkeypatch, empty_reply, origin, loop_active):
    """An empty generation must not write memory or block the next owner turn."""
    from admin.routers import chat, mobile
    from core.memory.scope import MemoryScope
    from core.pipeline import Pipeline

    critical_calls = []
    if empty_reply == "__stream_failure__":
        monkeypatch.setattr("core.output.segment_enforcer.get_segment_enforce_settings", lambda: (True, 4096))
    replies = iter([empty_reply, "mobile reply"])
    stream_ends = []

    failed_generation = False

    def next_reply():
        nonlocal failed_generation
        value = next(replies)
        if value == "__buffered_failure__":
            failed_generation = True
            from core.llm_protocol import UpstreamResponseFormatError
            error = UpstreamResponseFormatError("fixture stream interrupted")
            error.partial_text = "<think>private reasoning</think>Reply before failure"
            raise error
        return value

    class _Character:
        name = "Companion"

    class _Pipeline:
        character = _Character()
        _active_character_id = "companion"

        def _current_reality_scope(self, uid):
            return MemoryScope.reality_scope(uid, "companion")

        async def fetch_context(self, *_args, **_kwargs):
            return {}

        def build_prompt(self, *_args, **_kwargs):
            return [], {}

        async def run_llm(self, _messages):
            value = next_reply()
            if value == "__stream_failure__":
                from core.llm_protocol import UpstreamResponseFormatError
                raise UpstreamResponseFormatError("fixture disconnected")
            return value

        async def run_llm_stream(self, _messages, **_kwargs):
            value = next_reply()
            if value == "__stream_failure__":
                from core.llm_protocol import UpstreamResponseFormatError
                yield "Visible prefix\nShort tail"
                raise UpstreamResponseFormatError("fixture disconnected")
            yield value or ""

        async def run_agentic_loop(self, _messages, *, stream=False, **_kwargs):
            if stream:
                if empty_reply == "__buffered_failure__" and not failed_generation:
                    return next_reply()
                return self.run_llm_stream(_messages)
            return await self.run_llm(_messages)

        async def post_process_critical(self, uid, content, reply, **kwargs):
            # Bind against the production method instead of accepting arbitrary
            # kwargs silently. This catches turn_sink/Pipeline signature drift.
            inspect.signature(Pipeline.post_process_critical).bind(
                None, uid, content, reply, **kwargs
            )
            critical_calls.append(kwargs)
            return {
                "turn_id": "turn-mobile-real-chain",
                "critical_written": True,
                "emotion": "neutral",
                "char_id": "companion",
                "scope_payload": {},
                "should_update_profile": False,
                "profile_recent": [],
            }

        async def post_process_slow(self, *_args, **_kwargs):
            return {"emotion": "neutral", "turn_id": "turn-mobile-real-chain"}

    async def fake_probe(*_args, **_kwargs):
        return None

    monkeypatch.setattr("core.pipeline_registry.get", lambda: _Pipeline())
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner"}},
    )
    monkeypatch.setattr('admin.reality_admission.require_reality_turn', lambda _uid: None)
    monkeypatch.setattr("core.conversation_activity.mark_user_active", lambda: None)
    monkeypatch.setattr("core.scheduler.state_machine.notify_owner_turn", lambda _uid: None)
    monkeypatch.setattr("core.scheduler.proactive_ledger.record_user_message", lambda _uid: None)
    monkeypatch.setattr("core.scheduler.sensor_events.notify_chat_happened", lambda: None)
    monkeypatch.setattr("core.tool_dispatcher.tool_loop_active", lambda _uid: loop_active)
    monkeypatch.setattr('core.owner_chat.prepare._probe_and_execute_tools', fake_probe)
    monkeypatch.setattr("core.coplay.session.is_active", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("channels.registry._channels", {})

    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from core.conversation_gate import conversation_lock

    monkeypatch.setattr("channels.ui_push.any_connected", lambda: True)
    monkeypatch.setattr("channels.ui_push.push_stream_start", AsyncMock())
    monkeypatch.setattr("channels.ui_push.push_stream_delta", AsyncMock())
    monkeypatch.setattr("channels.ui_push.push_stream_end", AsyncMock(side_effect=lambda mid: stream_ends.append(mid)))
    monkeypatch.setattr("channels.desktop_ws.push_message", AsyncMock())
    endpoint = mobile.mobile_chat if origin == "mobile" else chat.desktop_chat
    from core.llm_protocol import UpstreamResponseFormatError
    expected_error = UpstreamResponseFormatError if origin == "mobile" and empty_reply in ("__buffered_failure__", "__stream_failure__") else HTTPException
    with pytest.raises(expected_error) as exc:
        await endpoint({"message": "first attempt"}, _auth=True)
    if isinstance(exc.value, HTTPException):
        assert exc.value.status_code == 502
    else:
        assert exc.value.category == "protocol_incompatible"
    if empty_reply not in ("__buffered_failure__", "__stream_failure__"):
        assert "回复正文" in exc.value.detail
    assert critical_calls == []
    deltas = __import__('channels.ui_push', fromlist=['push_stream_delta']).push_stream_delta
    if empty_reply == "__buffered_failure__" and origin == "desktop":
        assert deltas.await_count == 1
        assert deltas.call_args.args[1] == "Reply before failure"
    elif empty_reply == "__stream_failure__" and origin == "desktop":
        assert deltas.await_count == 2
        assert "".join(call.args[1] for call in deltas.await_args_list) == "Visible prefix Short tail"
    elif origin == "desktop" and empty_reply and empty_reply != "__buffered_failure__":
        assert deltas.call_args.args[1] == empty_reply
    else:
        assert deltas.await_count == 0
    assert not conversation_lock("owner").locked()
    assert len(stream_ends) == (1 if origin == "desktop" else 0)

    import asyncio
    result = await asyncio.wait_for(endpoint({"message": "hello"}, _auth=True), timeout=5)

    assert result["turn_id"] == "turn-mobile-real-chain"
    assert result["msg_id"] == (stream_ends[-1] if origin == "desktop" else result["turn_id"])
    assert len(critical_calls) == 1
    assert critical_calls[0]["provenance_source"] == ""
    assert critical_calls[0]["event_channel"] == origin
