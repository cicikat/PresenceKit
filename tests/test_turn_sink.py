import asyncio

import pytest


class _MockCharacter:
    name = "叶瑄"


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

    async def send(self, content, user_id, behavior=None):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((content, user_id, behavior))


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

    pipeline = Pipeline(_MockCharacter(), lore_engine=None)
    result = await record_assistant_turn(
        assistant_text="嗯。",
        uid="uid3",
        source=TurnSource.TRIGGER,
        trigger_name="night_reminder",
        fanout=[],
        pipeline=pipeline,
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
