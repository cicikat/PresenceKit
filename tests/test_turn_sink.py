import asyncio

import pytest


class _MockCharacter:
    name = "Companion"


class _FakePipeline:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def post_process(self, uid, content, reply, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return {
                "turn_id": f"turn-{content}",
                "critical_written": True,
                "emotion": "gentle",
            }
        finally:
            self.active -= 1


class _Channel:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.is_active = True
        self.sent = []
        self.msg_ids = []

    async def send(self, content, user_id, behavior=None, msg_id=None):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((content, user_id, behavior))
        self.msg_ids.append(msg_id)


async def _reset_channels():
    from channels import registry

    registry._channels = {}


@pytest.mark.parametrize(
    ("source", "trigger_name", "user_text"),
    [
        ("user_chat", None, "你好"),
        ("trigger", "morning_greeting", None),
        ("sensor", "sensor_aware", None),
        ("watch", "hr_high", None),
    ],
)
async def test_record_assistant_turn_sources_success(monkeypatch, source, trigger_name, user_text):
    from channels import registry
    from core.turn_sink import record_assistant_turn

    await _reset_channels()
    channel = _Channel("desktop")
    registry.register(channel)

    result = await record_assistant_turn(
        assistant_text="在。",
        uid="uid1",
        source=source,
        trigger_name=trigger_name,
        user_text=user_text,
        fanout="all",
        pipeline=_FakePipeline(),
    )

    assert result.written_to_memory is True
    assert result.emotion == "gentle"
    assert result.fanout_targets == ["desktop"]
    assert channel.sent == [("在。", "uid1", None)]


async def test_fanout_failure_does_not_block_other_channels():
    from channels import registry
    from core.turn_sink import TurnSource, record_assistant_turn

    await _reset_channels()
    broken = _Channel("broken", fail=True)
    ok = _Channel("ok")
    registry.register(broken)
    registry.register(ok)

    result = await record_assistant_turn(
        assistant_text="小心一点。",
        uid="uid2",
        source=TurnSource.TRIGGER,
        trigger_name="weather_alert",
        fanout="all",
        payload={"behavior": {"action_type": "notify", "params": {"text": "x"}}},
        pipeline=_FakePipeline(),
    )

    assert ok.sent == [
        ("小心一点。", "uid2", {"action_type": "notify", "params": {"text": "x"}})
    ]
    assert "broken" in result.fanout_failures


async def test_capture_turn_failure_enqueues_retry(sandbox, monkeypatch):
    from core.pipeline import Pipeline
    from core.turn_sink import TurnSource, record_assistant_turn

    enqueued = []

    def fake_enqueue(task_type, payload):
        enqueued.append((task_type, payload))

    def fail_capture(*args, **kwargs):
        raise RuntimeError("capture failed")

    async def detect_emotion(_):
        return "neutral"

    monkeypatch.setattr("core.post_process.slow_queue.enqueue", fake_enqueue)
    monkeypatch.setattr("core.memory.fixation_pipeline.capture_turn", fail_capture)
    monkeypatch.setattr("core.llm_client.detect_emotion", detect_emotion)
    monkeypatch.setattr("core.memory.mood_state.update", lambda *args, **kwargs: None)

    from core.write_envelope import stamp_trigger
    pipeline = Pipeline(_MockCharacter(), lore_engine=None)
    result = await record_assistant_turn(
        assistant_text="嗯。",
        uid="uid3",
        source=TurnSource.TRIGGER,
        trigger_name="night_reminder",
        fanout=[],
        pipeline=pipeline,
        envelope=stamp_trigger(),
    )

    assert result.written_to_memory is False
    retry = [item for item in enqueued if item[0] == "capture_turn_retry"]
    assert retry, f"expected capture_turn_retry in {enqueued}"
    assert retry[0][1]["trigger_name"] == "night_reminder"


async def test_conversation_gate_serializes_concurrent_turns():
    from core.turn_sink import TurnSource, record_assistant_turn

    pipeline = _FakePipeline(delay=0.05)
    await asyncio.gather(
        record_assistant_turn(
            assistant_text="一",
            uid="same",
            source=TurnSource.TRIGGER,
            trigger_name="random_message",
            fanout=[],
            pipeline=pipeline,
        ),
        record_assistant_turn(
            assistant_text="二",
            uid="same",
            source=TurnSource.TRIGGER,
            trigger_name="weather_alert",
            fanout=[],
            pipeline=pipeline,
        ),
    )

    assert pipeline.max_active == 1


async def test_bypass_gate_skips_conversation_gate():
    from core.turn_sink import TurnSource, record_assistant_turn

    pipeline = _FakePipeline(delay=0.05)
    await asyncio.gather(
        record_assistant_turn(
            assistant_text="一",
            uid="same-bypass",
            source=TurnSource.WATCH,
            trigger_name="hr_critical",
            fanout=[],
            bypass_gate=True,
            pipeline=pipeline,
        ),
        record_assistant_turn(
            assistant_text="二",
            uid="same-bypass",
            source=TurnSource.WATCH,
            trigger_name="hr_critical",
            fanout=[],
            bypass_gate=True,
            pipeline=pipeline,
        ),
    )

    assert pipeline.max_active == 2


# ── exclude_origin_channel ────────────────────────────────────────────────────

async def test_fanout_excludes_origin_channel():
    """fanout="all" + exclude_origin_channel="desktop" 时，
    desktop channel 不被调用，mobile channel 被调用。"""
    from channels import registry
    from core.turn_sink import TurnSource, record_assistant_turn

    await _reset_channels()
    desktop = _Channel("desktop")
    mobile = _Channel("mobile")
    registry.register(desktop)
    registry.register(mobile)

    result = await record_assistant_turn(
        assistant_text="回复内容",
        uid="owner",
        source=TurnSource.USER_CHAT,
        user_text="用户消息",
        fanout="all",
        bypass_gate=True,
        exclude_origin_channel="desktop",
        pipeline=_FakePipeline(),
    )

    assert "mobile" in result.fanout_targets
    assert "desktop" not in result.fanout_targets
    assert mobile.sent == [("回复内容", "owner", None)]
    assert desktop.sent == []


async def test_fanout_all_without_exclude_sends_to_all():
    """exclude_origin_channel=None 时，所有活跃 channel 均被调用（原有行为不变）。"""
    from channels import registry
    from core.turn_sink import TurnSource, record_assistant_turn

    await _reset_channels()
    desktop = _Channel("desktop")
    mobile = _Channel("mobile")
    registry.register(desktop)
    registry.register(mobile)

    result = await record_assistant_turn(
        assistant_text="回复内容",
        uid="owner",
        source=TurnSource.USER_CHAT,
        user_text="用户消息",
        fanout="all",
        bypass_gate=True,
        exclude_origin_channel=None,
        pipeline=_FakePipeline(),
    )

    assert set(result.fanout_targets) == {"desktop", "mobile"}
    assert desktop.sent == [("回复内容", "owner", None)]
    assert mobile.sent == [("回复内容", "owner", None)]


async def test_fanout_exclude_does_not_affect_named_fanout():
    """fanout 指定具体名称时，exclude_origin_channel 不产生影响。"""
    from channels import registry
    from core.turn_sink import TurnSource, record_assistant_turn

    await _reset_channels()
    desktop = _Channel("desktop")
    registry.register(desktop)

    result = await record_assistant_turn(
        assistant_text="回复内容",
        uid="owner",
        source=TurnSource.USER_CHAT,
        user_text="用户消息",
        fanout="desktop",
        bypass_gate=True,
        exclude_origin_channel="desktop",
        pipeline=_FakePipeline(),
    )

    # fanout 指定的是具体 channel 名称（非 "all"），exclude 不过滤
    assert "desktop" in result.fanout_targets
    assert desktop.sent == [("回复内容", "owner", None)]


# ── Narrative segments fanout tests ──────────────────────────────────────────
#
# All three tests use the *real* DesktopChannel so that push_message is called
# through the actual channel.send() path.  push_message and push_segments are
# monkeypatched at the desktop_ws module level to capture calls.

async def _setup_real_desktop(monkeypatch):
    """Register real DesktopChannel and fake out both WS push functions.
    Returns (push_msg_calls, push_seg_calls)."""
    from channels import registry
    from channels.desktop import DesktopChannel

    await _reset_channels()
    registry.register(DesktopChannel())

    push_msg_calls: list[dict] = []
    push_seg_calls: list[dict] = []

    async def fake_push_message(content, msg_id=None):
        push_msg_calls.append({"content": content, "msg_id": msg_id})
        return True

    async def fake_push_segments(content, segments, msg_id=None):
        push_seg_calls.append({"content": content, "segments": segments, "msg_id": msg_id})
        return True

    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: True)
    monkeypatch.setattr("channels.desktop_ws.push_message", fake_push_message)
    monkeypatch.setattr("channels.desktop_ws.push_segments", fake_push_segments)

    return push_msg_calls, push_seg_calls


async def test_message_segments_fanout_with_say(monkeypatch):
    """message_segments envelope 正确发出，包含 say segment，msg_id 与 channel_message 共享。"""
    from core.turn_sink import record_assistant_turn

    push_msg_calls, push_seg_calls = await _setup_real_desktop(monkeypatch)

    await record_assistant_turn(
        assistant_text="她说：<say>你好</say>",
        uid="uid_seg",
        source="user_chat",
        user_text="hello",
        fanout="all",
        pipeline=_FakePipeline(),
    )

    # push_segments called exactly once
    assert len(push_seg_calls) == 1
    seg = push_seg_calls[0]

    # channel_message called exactly once
    assert len(push_msg_calls) == 1

    # msg_id is shared between channel_message and message_segments
    assert seg["msg_id"] is not None
    assert push_msg_calls[0]["msg_id"] == seg["msg_id"]
    assert seg["msg_id"] == "turn-hello"

    # content has no tag markup
    assert "<say>" not in seg["content"]
    assert "</say>" not in seg["content"]
    assert "你好" in seg["content"]

    # segments contain a say entry with correct text
    say_segs = [s for s in seg["segments"] if s["type"] == "say"]
    assert len(say_segs) == 1
    assert say_segs[0]["text"] == "你好"


async def test_message_segments_exception_does_not_block_main_flow(monkeypatch):
    """push_segments 抛异常时，主流程不中断，channel_message 仍已发出，TurnResult 有效。"""
    from channels import registry
    from channels.desktop import DesktopChannel
    from core.turn_sink import record_assistant_turn

    await _reset_channels()
    registry.register(DesktopChannel())

    push_msg_calls: list[str] = []

    async def fake_push_message(content, msg_id=None):
        push_msg_calls.append(content)
        return True

    async def failing_push_segments(*args, **kwargs):
        raise RuntimeError("segments fanout boom")

    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: True)
    monkeypatch.setattr("channels.desktop_ws.push_message", fake_push_message)
    monkeypatch.setattr("channels.desktop_ws.push_segments", failing_push_segments)

    # Must not raise
    result = await record_assistant_turn(
        assistant_text="普通回复",
        uid="uid_exc",
        source="user_chat",
        user_text="test",
        fanout="all",
        pipeline=_FakePipeline(),
    )

    # Original channel_message was delivered
    assert push_msg_calls == ["普通回复"]
    # TurnResult reflects successful write and fanout
    assert result.written_to_memory is True
    assert "desktop" in result.fanout_targets
    assert result.fanout_failures == {}


async def test_message_segments_plain_text(monkeypatch):
    """无标签普通文本仍发送 message_segments，segments 为单个 narration，content 等于原文。"""
    from core.turn_sink import record_assistant_turn

    _, push_seg_calls = await _setup_real_desktop(monkeypatch)

    raw = "普通的一句话，没有任何标签。"
    await record_assistant_turn(
        assistant_text=raw,
        uid="uid_plain",
        source="user_chat",
        user_text="hi",
        fanout="all",
        pipeline=_FakePipeline(),
    )

    assert len(push_seg_calls) == 1
    seg = push_seg_calls[0]

    # content equals original text (no tags to strip)
    assert seg["content"] == raw

    # single segment — markdown path classifies plain text as "say"
    assert len(seg["segments"]) == 1
    assert seg["segments"][0]["type"] == "say"
    assert seg["segments"][0]["text"] == raw
