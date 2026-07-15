"""Brief 72：生成后段落硬兜底、双输出路径与热开关。"""

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.auth import TokenInfo
from admin.routers.settings_misc import router as settings_misc_router


def test_long_single_paragraph_gets_one_blank_line():
    from core.output.segment_enforcer import enforce_paragraph_breaks

    text = "第一句在这里慢慢铺开一些内容。第二句继续补充足够多的细节。第三句收束这段回复。"
    result = enforce_paragraph_breaks(text, min_len=20)

    assert "\n\n" in result
    assert result.replace("\n", "") == text


def test_short_reply_is_unchanged():
    from core.output.segment_enforcer import enforce_paragraph_breaks

    text = "嗯，我在。"
    assert enforce_paragraph_breaks(text, min_len=40) == text


def test_existing_paragraph_break_is_unchanged():
    from core.output.segment_enforcer import enforce_paragraph_breaks

    text = "第一段已经分好了。\n\n第二段不应再加工。"
    assert enforce_paragraph_breaks(text, min_len=5) == text


def test_no_sentence_boundary_is_unchanged():
    from core.output.segment_enforcer import enforce_paragraph_breaks

    text = "这是一段很长但完全没有目标句末标点的回复内容"
    assert enforce_paragraph_breaks(text, min_len=10) == text


def test_invalid_min_len_fails_open():
    from core.output.segment_enforcer import enforce_paragraph_breaks

    text = "第一句足够长。第二句也足够长。"
    assert enforce_paragraph_breaks(text, min_len="invalid") == text  # type: ignore[arg-type]


def test_stream_enforcer_breaks_before_next_sentence_delta():
    from core.output.segment_enforcer import ParagraphStreamEnforcer

    enforcer = ParagraphStreamEnforcer(min_len=10)
    first = enforcer.feed("这是一句已经超过阈值的开场内容。")
    second = enforcer.feed("第二句继续自然出现。")

    assert first == "这是一句已经超过阈值的开场内容。"
    assert second.startswith("\n\n第二句")


def test_stream_enforcer_keeps_closing_quote_before_break():
    from core.output.segment_enforcer import ParagraphStreamEnforcer

    enforcer = ParagraphStreamEnforcer(min_len=10)
    first = enforcer.feed("他说：“这是一句已经超过阈值的话。”")
    second = enforcer.feed("随后才是下一句。")

    assert first.endswith("。”")
    assert second.startswith("\n\n随后")


def test_stream_enforcer_does_not_split_xml_tag_across_bubbles():
    from core.output.segment_enforcer import ParagraphStreamEnforcer

    enforcer = ParagraphStreamEnforcer(min_len=10)
    opening = enforcer.feed("<say>这是一句已经超过阈值的话。")
    closing = enforcer.feed("</say>")
    next_segment = enforcer.feed("<say>下一句继续。</say>")

    assert "\n" not in opening
    assert "\n" not in closing
    assert next_segment.startswith("\n\n<say>")


def test_stream_enforcer_closes_and_reopens_wrapping_tag_at_live_break():
    from core.output.segment_enforcer import ParagraphStreamEnforcer

    enforcer = ParagraphStreamEnforcer(min_len=10)
    first = enforcer.feed("<say>这是一句已经超过阈值的话。")
    second = enforcer.feed("下一句仍在同一个标签里。</say>")

    assert first == "<say>这是一句已经超过阈值的话。"
    assert second.startswith("</say>\n\n<say>下一句")
    assert second.endswith("</say>")


def test_stream_and_canonical_enforcement_use_same_rule():
    from core.output.segment_enforcer import (
        ParagraphStreamEnforcer,
        enforce_paragraph_breaks,
    )

    chunks = [
        "第一句在这里慢慢铺开足够多的内容。",
        "第二句继续补充细节。",
        "第三句负责收束。",
    ]
    enforcer = ParagraphStreamEnforcer(min_len=20)
    streamed = "".join(enforcer.feed(chunk) for chunk in chunks)

    assert streamed == enforce_paragraph_breaks("".join(chunks), min_len=20)
    assert "\n\n" in streamed


def test_effective_threshold_falls_back_to_s4(monkeypatch):
    import core.output.segment_enforcer as segment_enforcer

    monkeypatch.setattr(
        segment_enforcer,
        "get_config",
        lambda: {
            "anti_collapse": {"segment_min_len": 73},
            "output": {"segment_enforce": {"enabled": True}},
        },
    )
    assert segment_enforcer.get_segment_enforce_settings() == (True, 73)


@pytest.mark.parametrize("path", ["qq", "desktop"])
def test_enabled_setting_applies_to_both_output_paths(monkeypatch, path):
    import core.output.segment_enforcer as segment_enforcer

    monkeypatch.setattr(segment_enforcer, "get_segment_enforce_settings", lambda: (True, 20))
    text = "第一句在这里慢慢铺开一些内容。第二句继续补充足够多的细节。第三句收束这段回复。"

    if path == "qq":
        import core.response_processor as response_processor
        monkeypatch.setattr(response_processor, "get_segment_enforce_settings", lambda: (True, 20))
        result = "".join(response_processor.process(text, "Companion"))
    else:
        import core.reality_output_guard as reality_output_guard
        monkeypatch.setattr(reality_output_guard, "get_segment_enforce_settings", lambda: (True, 20))
        result = reality_output_guard.clean_reality_reply_text(text, "Companion")

    assert "\n\n" in result
    assert result.replace("\n", "") == text


def test_qq_memory_copy_preserves_original_paragraph_shape(monkeypatch):
    import core.response_processor as response_processor

    monkeypatch.setattr(response_processor, "get_segment_enforce_settings", lambda: (True, 20))
    text = "第一句在这里慢慢铺开一些内容。第二句继续补充足够多的细节。第三句收束这段回复。"

    visible = "".join(response_processor.process(text, "Companion"))
    memory = "".join(response_processor.process_memory_copy(text, "Companion"))

    assert "\n\n" in visible
    assert "\n\n" not in memory
    assert memory == text


def test_reality_memory_copy_preserves_original_paragraph_shape(monkeypatch):
    import core.reality_output_guard as reality_output_guard

    monkeypatch.setattr(reality_output_guard, "get_segment_enforce_settings", lambda: (True, 20))
    text = "第一句在这里慢慢铺开一些内容。第二句继续补充足够多的细节。第三句收束这段回复。"

    visible = reality_output_guard.clean_reality_reply_text(text, "Companion")
    memory = reality_output_guard.clean_reality_reply_text_for_memory(text, "Companion")

    assert "\n\n" in visible
    assert "\n\n" not in memory
    assert memory == text


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_owner_chat_stream_enforces_visible_copy_only(monkeypatch, enabled):
    """Live deltas and canonical converge; memory always keeps raw shape."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import admin.routers.chat as chat
    import channels.desktop_ws as desktop_ws
    import channels.registry as channel_registry
    import channels.ui_push as ui_push
    import core.config_loader as config_loader
    import core.memory.user_profile as user_profile
    import core.output.segment_enforcer as segment_enforcer
    import core.perform_mapper as perform_mapper
    import core.pipeline_registry as pipeline_registry
    import core.reality_output_guard as reality_output_guard
    import core.scheduler.loop as scheduler_loop
    import core.scheduler.state_machine as scheduler_state
    import core.tool_dispatcher as tool_dispatcher
    import core.turn_sink as turn_sink

    pieces = [
        "第一句在这里慢慢铺开足够多的内容。",
        "第二句继续补充细节。",
        "第三句负责收束。",
    ]
    raw_reply = "".join(pieces)
    stream_deltas: list[str] = []
    canonical_messages: list[str] = []
    segment_messages: list[str] = []
    memory_messages: list[str] = []

    class FakePipeline:
        character = SimpleNamespace(name="Companion")

        def _current_reality_scope(self, uid):
            return SimpleNamespace(character_id="yexuan")

        async def fetch_context(self, uid, message, **kwargs):
            return {}

        def build_prompt(self, uid, message, context, **kwargs):
            return [{"role": "user", "content": message}], {}

        async def run_llm_stream(self, messages):
            for piece in pieces:
                yield piece

    async def fake_record_assistant_turn(*, assistant_text, **kwargs):
        memory_messages.append(assistant_text)
        return turn_sink.TurnResult(
            turn_id="turn-stream",
            written_to_memory=True,
            fanout_targets=[],
        )

    async def fake_stream_delta(msg_id, delta):
        stream_deltas.append(delta)

    async def fake_push_message(content, **kwargs):
        canonical_messages.append(content)

    async def fake_push_segments(content, segments, **kwargs):
        segment_messages.append(content)

    async def noop_async(*args, **kwargs):
        return None

    @asynccontextmanager
    async def noop_lock(uid):
        yield

    monkeypatch.setattr(pipeline_registry, "get", lambda: FakePipeline())
    monkeypatch.setattr(config_loader, "get_config", lambda: {"scheduler": {"owner_id": "owner"}})
    monkeypatch.setattr(tool_dispatcher, "tool_loop_active", lambda uid: False)
    monkeypatch.setattr(scheduler_loop, "mark_user_active", lambda: None)
    monkeypatch.setattr(scheduler_state, "notify_owner_turn", lambda uid: None)
    monkeypatch.setattr(chat, "_probe_and_execute_tools", noop_async)
    monkeypatch.setattr("core.conversation_gate.conversation_lock", noop_lock)
    monkeypatch.setattr(channel_registry, "get", lambda name: None)
    monkeypatch.setattr(turn_sink, "record_assistant_turn", fake_record_assistant_turn)
    monkeypatch.setattr(user_profile, "get_affection_level", lambda uid: {"value": 0, "label": "n/a"})
    monkeypatch.setattr(segment_enforcer, "get_segment_enforce_settings", lambda: (enabled, 20))
    monkeypatch.setattr(reality_output_guard, "get_segment_enforce_settings", lambda: (enabled, 20))
    monkeypatch.setattr(ui_push, "any_connected", lambda: True)
    monkeypatch.setattr(ui_push, "push_stream_start", noop_async)
    monkeypatch.setattr(ui_push, "push_stream_delta", fake_stream_delta)
    monkeypatch.setattr(ui_push, "push_stream_end", noop_async)
    monkeypatch.setattr(desktop_ws, "_new_msg_id", lambda: "stream-message")
    monkeypatch.setattr(desktop_ws, "push_message", fake_push_message)
    monkeypatch.setattr(desktop_ws, "push_segments", fake_push_segments)
    monkeypatch.setattr(perform_mapper, "enrich_say_segments", noop_async)

    result = await chat.run_owner_chat_turn("你好", "desktop")

    streamed = "".join(stream_deltas)
    assert memory_messages == [raw_reply]
    assert canonical_messages == [result["reply"]]
    assert len(segment_messages) == 1
    assert [line for line in segment_messages[0].splitlines() if line] == [
        line for line in result["reply"].splitlines() if line
    ]
    if enabled:
        expected = segment_enforcer.enforce_paragraph_breaks(raw_reply, min_len=20)
        assert streamed == expected
        assert result["reply"] == expected
        assert "\n\n" in streamed
    else:
        assert streamed == raw_reply
        assert result["reply"] == raw_reply


@pytest.fixture
def persona_client():
    app = FastAPI()
    app.include_router(settings_misc_router)
    fake_persona = TokenInfo(label="test-desktop", scopes=frozenset({"persona"}))
    for route in settings_misc_router.routes:
        for dep in route.dependant.dependencies:
            if hasattr(dep.call, "_required_scopes"):
                app.dependency_overrides[dep.call] = lambda: fake_persona
    return TestClient(app)


def test_output_segment_enforce_api_hot_updates_config(
    tmp_path: Path,
    monkeypatch,
    persona_client,
):
    import admin.routers.settings_misc as settings_misc
    import core.config_loader as config_loader
    import core.output.segment_enforcer as segment_enforcer

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "anti_collapse": {"segment_min_len": 55},
                "output": {"segment_enforce": {"enabled": False}},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_misc, "CONFIG_FILE", config_path)
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)
    monkeypatch.setattr(
        segment_enforcer,
        "get_config",
        lambda: yaml.safe_load(config_path.read_text(encoding="utf-8")),
    )

    initial = persona_client.get("/output-segment-enforce")
    assert initial.status_code == 200
    assert initial.json() == {"enabled": False, "min_len": 55}

    updated = persona_client.put(
        "/output-segment-enforce",
        json={"enabled": True, "min_len": 64},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is True
    assert updated.json()["min_len"] == 64
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["output"]["segment_enforce"] == {"enabled": True, "min_len": 64}


def test_output_segment_enforce_api_rejects_invalid_threshold(persona_client):
    response = persona_client.put(
        "/output-segment-enforce",
        json={"enabled": True, "min_len": 0},
    )
    assert response.status_code == 422
