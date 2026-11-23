from tests.fixtures.public_assets import TEST_CHAR_ID
import json
import sys
import time
from types import SimpleNamespace

import pytest


class _FakePipeline:
    context = property(lambda self: self)
    character = type("_C", (), {"name": "TestChar"})()

    def _current_reality_scope(self, uid):
        return type("Scope", (), {"character_id": TEST_CHAR_ID})()

    async def fetch(self, uid, query, **kwargs):
        return {}

    def build_prompt(self, uid, prompt, context, **kwargs):
        return [{"role": "user", "content": prompt}], {}

    async def run_llm(self, messages, **kwargs):
        return "reply"


def _install_ui_transport_stubs(monkeypatch):
    """Keep this scheduler test outside the concrete WebSocket transports."""
    import channels

    async def noop(*args, **kwargs):
        return None

    desktop_ws = SimpleNamespace(
        is_connected=lambda: False,
        _new_msg_id=lambda: "test-stream-message",
        push_message=noop,
        push_segments=noop,
        push_tool_status=noop,
    )
    ui_push = SimpleNamespace(
        any_connected=lambda: False,
        push_stream_start=noop,
        push_stream_delta=noop,
        push_stream_end=noop,
    )

    monkeypatch.setitem(sys.modules, "channels.desktop_ws", desktop_ws)
    monkeypatch.setitem(sys.modules, "channels.ui_push", ui_push)
    # `from channels import ...` can reuse attributes cached on the package by
    # an earlier test, so replace those attributes as well as sys.modules.
    monkeypatch.setattr(channels, "desktop_ws", desktop_ws, raising=False)
    monkeypatch.setattr(channels, "ui_push", ui_push, raising=False)


@pytest.mark.asyncio
async def test_owner_chat_turn_marks_user_active(monkeypatch):
    _install_ui_transport_stubs(monkeypatch)
    from core.owner_chat.service import run_owner_chat_turn
    from core import conversation_activity

    turns = []

    async def fake_record_assistant_turn(**kwargs):
        turns.append(kwargs)
        return SimpleNamespace(
            fanout_failures={},
            emotion="neutral",
            turn_id="t1",
            written_to_memory=True,
            artifacts=[],
        )

    monkeypatch.setattr("core.conversation_activity._last_user_message_time", 0.0)
    monkeypatch.setattr("core.pipeline_registry.get", lambda: _FakePipeline())
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"owner_id": "u1"}})
    async def fake_probe(message, user_id, *, char_id=TEST_CHAR_ID, provenance_channel=None):
        return ""

    monkeypatch.setattr('core.owner_chat.prepare._probe_and_execute_tools', fake_probe)
    monkeypatch.setattr("channels.registry.get", lambda name: None)
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_record_assistant_turn)

    await run_owner_chat_turn("hello", "desktop")

    assert conversation_activity.user_active_recently()
    assert turns and turns[0]["user_text"] == "hello"


@pytest.mark.asyncio
async def test_execute_prompt_blocked_send_does_not_mark_or_after_send(monkeypatch):
    from core.scheduler import execution, loop

    marks = []
    after_send = []

    async def blocked_send(prompt, search_query="", trigger_name="", **kwargs):
        return None

    monkeypatch.setattr(loop, "_pipeline_send", blocked_send)
    monkeypatch.setattr(loop, "_mark", lambda name, **_kwargs: marks.append(name))

    result = await execution.execute_prompt(
        trigger_name="random_message",
        prompt_factory=lambda: "prompt",
        dry_run=False,
        would_mark=["random_message"],
        after_send=lambda: after_send.append("called"),
    )

    assert result.sent is False
    assert marks == []
    assert after_send == []


@pytest.mark.asyncio
async def test_execute_prompt_dry_run_still_records(monkeypatch, sandbox):
    from core.scheduler import execution

    result = await execution.execute_prompt(
        trigger_name="random_message",
        prompt_factory=lambda: "prompt",
        dry_run=True,
        would_mark=["random_message"],
        would_mark_done=["r1"],
    )

    assert result.sent is False
    rows = sandbox.execute_dryrun_log().read_text(encoding="utf-8").splitlines()
    assert rows
    row = json.loads(rows[-1])
    assert row["trigger_name"] == "random_message"
    assert row["would_mark_done"] == ["r1"]


@pytest.mark.asyncio
async def test_pipeline_send_non_migrated_compatibility_trigger_is_execution_only(monkeypatch):
    from core.scheduler import loop

    recorded = []

    async def fake_record_assistant_turn(**kwargs):
        recorded.append(kwargs)
        return SimpleNamespace(fanout_failures={})

    monkeypatch.setattr("core.pipeline_registry.get", lambda: _FakePipeline())
    monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
    monkeypatch.setattr("core.conversation_activity._last_user_message_time", time.time())
    monkeypatch.setattr("core.scheduler.triggers.birthday._is_birthday_period", lambda: False)
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_record_assistant_turn)

    result = await loop._pipeline_send("prompt", trigger_name="compatibility_trigger")

    assert result == "reply"
    assert recorded and recorded[0]["trigger_name"] == "compatibility_trigger"


@pytest.mark.asyncio
async def test_pipeline_send_migrated_trigger_queues_signal_without_llm(monkeypatch):
    from core.scheduler import loop

    captured = {}

    def emit(uid, char_id, trigger_name, **kwargs):
        captured.update(uid=uid, char_id=char_id, trigger_name=trigger_name, kwargs=kwargs)
        return True, "queued"

    monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: "char-a")
    monkeypatch.setattr("core.autonomy.signal_adapters.emit_trigger_signal", emit)
    monkeypatch.setattr(
        "core.pipeline_registry.get",
        lambda: (_ for _ in ()).throw(AssertionError("migrated trigger must not load pipeline")),
    )

    result = await loop._pipeline_send("legacy prompt", trigger_name="hr_critical")

    assert result is None
    assert captured["uid"] == "u1"
    assert captured["char_id"] == "char-a"
    assert captured["trigger_name"] == "hr_critical"


@pytest.mark.parametrize("window,elapsed,expected", [
    (120, 119.999, True), (120, 120.0, False), (120, 120.001, False),
    (30, 29.999, True), (30, 30.0, False),
])
def test_conversation_activity_window_boundary(monkeypatch, window, elapsed, expected):
    from core import conversation_activity

    monkeypatch.setattr("core.conversation_activity._last_user_message_time", 0.0)
    conversation_activity.mark_user_active(now=1000.0)
    assert conversation_activity.last_user_message_time() == 1000.0
    assert conversation_activity.user_active_recently(window, now=1000.0 + elapsed) is expected


@pytest.mark.parametrize("elapsed,blocked", [(119.999, True), (120.0, False)])
def test_activity_clock_controls_scheduler_gating(monkeypatch, elapsed, blocked):
    """A real activity write must block filler until the exact window expires."""
    from core import conversation_activity
    from core.memory import realtime_state
    from core.scheduler import gating
    from core.scheduler.state_machine import TriggerState

    monkeypatch.setattr(conversation_activity, "_last_user_message_time", 0.0)
    monkeypatch.setattr(conversation_activity, "time", SimpleNamespace(time=lambda: 1000.0 + elapsed))
    monkeypatch.setattr("core.character_loader.is_proactive_disabled", lambda: False)
    monkeypatch.setattr("core.scheduler.triggers.dnd.is_dnd", lambda uid: False)
    monkeypatch.setattr(realtime_state, "get_presence", lambda now: realtime_state.PRESENCE_UNKNOWN)
    monkeypatch.setattr(gating, "get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr(gating, "is_trigger_ready", lambda name: True)
    conversation_activity.mark_user_active(now=1000.0)
    proposal = gating.TriggerProposal(
        trigger_name="random_message", urgency=0.5, topic_source="test",
        requires_state=[TriggerState.QUIET],
    )
    picked, reason, candidates = gating._decide("u1", [proposal])
    assert (picked is None) is blocked
    if blocked:
        assert reason == "active_window_filtered"
    else:
        assert picked is proposal
