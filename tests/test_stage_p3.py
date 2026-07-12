from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _settings(**overrides):
    from core.stage.models import StageSettings

    values = {
        "min_responders": 1,
        "max_responders": 1,
        "max_ai_chain_depth": 0,
        "transcript_limit": 20,
        "group_memory_strength": 0.7,
    }
    values.update(overrides)
    return StageSettings(**values)


def test_stage_context_renders_viewer_and_other_speakers(sandbox):
    from core.character_name_provider import get_char_name
    from core.stage.context import render_presence, render_transcript
    from core.stage.models import Stage, TranscriptEntry

    stage = Stage("g", "owner", ("yexuan", "yexuanJ-5412"), settings=_settings())
    transcript = [
        TranscriptEntry("owner", "hello", 1, "t", "user"),
        TranscriptEntry("yexuan", "one", 2, "t", "user"),
        TranscriptEntry("yexuanJ-5412", "two", 3, "t", "yexuan"),
    ]

    rendered = render_transcript(stage, transcript, viewer_id="yexuan")
    presence = render_presence(stage, viewer_id="yexuan")

    assert "owner：hello" in rendered
    assert "你：one" in rendered
    assert f"{get_char_name('yexuanJ-5412')}：two" in rendered
    assert "群聊在场感" in presence


@pytest.mark.asyncio
async def test_stage_character_view_uses_explicit_scope_and_prompt_context(sandbox):
    from core.stage.models import Stage, TranscriptEntry
    from core.stage.views import StageCharacterView

    captured = {}

    class FakePipeline:
        async def fetch_context(self, uid, content, *, frozen_scope):
            captured["scope"] = frozen_scope
            return {"history": []}

        def build_prompt(self, uid, content, context, **kwargs):
            captured["prompt_content"] = content
            captured["context"] = context
            captured["kwargs"] = kwargs
            return ([{"role": "user", "content": content}], {})

        async def run_llm(self, messages, *, char_id=None):
            captured["run_llm_char_id"] = char_id
            return "reply"

    view = object.__new__(StageCharacterView)
    view.char_id = "yexuanJ-5412"
    view.pipeline = FakePipeline()
    stage = Stage("g", "owner", ("yexuan", "yexuanJ-5412"), settings=_settings())
    transcript = [TranscriptEntry("owner", "hello", 1, "t", "user")]

    reply = await view.generate(stage, transcript, "t", "user")

    assert reply == "reply"
    assert captured["scope"].character_id == "yexuanJ-5412"
    assert captured["context"]["stage_presence"]
    assert "owner：hello" in captured["context"]["stage_transcript"]
    assert captured["kwargs"]["char_id"] == "yexuanJ-5412"
    assert captured["kwargs"]["consume_pending_perception"] is False
    assert captured["prompt_content"] != "hello"
    assert captured["run_llm_char_id"] == "yexuanJ-5412"


@pytest.mark.asyncio
async def test_projection_enqueues_per_character_and_is_idempotent(sandbox, monkeypatch):
    from core.stage.models import TranscriptEntry
    from core.stage.projection import enqueue_reality_projection
    from core.stage.store import append_transcript, create_stage, load_stage

    stage = create_stage(
        "group-projection",
        "owner",
        ["yexuan", "yexuanJ-5412"],
        settings=_settings(),
    )
    append_transcript(stage, TranscriptEntry("owner", "hello", 1, "t", "user"))
    append_transcript(stage, TranscriptEntry("yexuan", "reply", 2, "t", "user"))
    jobs = []
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda kind, payload: jobs.append((kind, payload)))

    count = await enqueue_reality_projection("group-projection")
    second = await enqueue_reality_projection("group-projection")

    assert count == 2
    assert second == 0
    assert {payload["char_id"] for _, payload in jobs} == {"yexuan", "yexuanJ-5412"}
    assert all(payload["source"] == "group:group-projection" for _, payload in jobs)
    strengths = {payload["char_id"]: payload["memory_strength"] for _, payload in jobs}
    assert strengths == {"yexuan": 0.55, "yexuanJ-5412": 0.4}
    assert all(payload["scope"]["character_id"] == payload["char_id"] for _, payload in jobs)
    assert load_stage("group-projection").projection_cursor == 2


@pytest.mark.asyncio
async def test_projection_cursor_survives_transcript_pruning(sandbox, monkeypatch):
    from core.stage.models import TranscriptEntry
    from core.stage.projection import enqueue_reality_projection
    from core.stage.store import append_transcript, create_stage, load_stage

    stage = create_stage(
        "group-prune",
        "owner",
        ["yexuan"],
        settings=_settings(transcript_limit=2),
    )
    jobs = []
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda kind, payload: jobs.append(payload))
    append_transcript(stage, TranscriptEntry("owner", "one", 1, "t1", "user"))
    append_transcript(stage, TranscriptEntry("yexuan", "two", 2, "t1", "user"))
    await enqueue_reality_projection("group-prune")

    latest = load_stage("group-prune")
    append_transcript(latest, TranscriptEntry("owner", "three", 3, "t2", "user"))

    assert load_stage("group-prune").projection_cursor == 1
    assert await enqueue_reality_projection("group-prune") == 1
    assert len(jobs) == 2


def test_mid_term_group_projection_metadata_persists(sandbox):
    from core.memory import mid_term

    mid_term.append(
        "owner",
        "group summary",
        mid_id="m1",
        source_turn_id="g:0:2",
        char_id="yexuan",
        source="group:g",
        memory_strength=0.7,
    )

    entry = mid_term.load("owner", char_id="yexuan")[0]
    assert entry["source"] == "group:g"
    assert entry["memory_strength"] == 0.7


def test_prompt_builder_injects_stage_layers(sandbox):
    from core import prompt_builder

    char = MagicMock()
    char.name = "Companion"
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""

    with (
        patch("core.prompt_builder._load_jailbreak", return_value=""),
        patch("core.prompt_builder._load_style_hint", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.config_loader.get_config", return_value={"chat": {"style": "chat"}}),
        patch("core.mood_text.get_mood_text", return_value=""),
        patch("core.activity_manager.get_prompt_fragment", return_value=""),
    ):
        messages, debug = prompt_builder.build(
            character=char,
            user_id="owner",
            user_message="hello",
            history=[],
            relation={},
            profile={},
            group_context=[],
            stage_presence="presence",
            stage_transcript="owner：hello",
        )

    layers = {message.get("_layer"): message["content"] for message in messages}
    assert layers["2.2_stage_presence"] == "presence"
    assert "owner：hello" in layers["4.2_stage_transcript"]
    assert "2.2_stage_presence" in debug["layers_activated"]


@pytest.mark.asyncio
async def test_reality_runtime_delivers_speaker_and_enqueues_projection(sandbox, monkeypatch):
    from core.stage.models import Stage
    from core.stage.runner import StageTurnResult
    from core.stage.runtime import run_reality_stage_turn

    stage = Stage("runtime-g", "actual-owner", ("yexuan",), settings=_settings())
    ws_sent = []
    non_desktop_sent = []
    projected = []

    async def fake_run(group_id, owner_content, *, generate_reply, deliver_reply, turn_id):
        await deliver_reply("yexuan", "reply", "t")
        return StageTurnResult(group_id, "t", (), 0)

    class FakeChannel:
        name = "mobile"
        is_active = True

        async def send(self, content, uid, behavior=None, *, char_id=None, **kw):
            non_desktop_sent.append((content, uid, char_id))

    async def fake_send_json(payload):
        ws_sent.append(payload)
        return True

    monkeypatch.setattr("core.stage.runtime.load_stage", lambda group_id: stage)
    monkeypatch.setattr("core.stage.runtime.run_owner_turn", fake_run)
    monkeypatch.setattr(
        "core.stage.runtime.enqueue_reality_projection",
        lambda group_id: _record_async(projected, group_id),
    )
    # Simulate WS connected by patching _send_json and _current_ws.
    monkeypatch.setattr("channels.desktop_ws._send_json", fake_send_json)
    monkeypatch.setattr("channels.desktop_ws._current_ws", object())
    # Return a non-desktop channel so the second fanout leg is exercised.
    monkeypatch.setattr("channels.registry.get_active", lambda: [FakeChannel()])

    result = await run_reality_stage_turn("runtime-g", "hello")

    assert result.turn_id == "t"
    # WS should have received: group_round_start, the reply channel_message, group_round_end.
    ws_types = [f["type"] for f in ws_sent]
    assert "channel_message" in ws_types
    msg = next(f for f in ws_sent if f["type"] == "channel_message")
    assert msg["content"] == "reply"
    assert msg.get("char_id") == "yexuan"
    # Non-desktop channel also received the delivery.
    assert non_desktop_sent == [("reply", "actual-owner", "yexuan")]
    assert projected == ["runtime-g"]


def test_scheduler_cooldown_can_be_scoped_per_character(sandbox, monkeypatch):
    from core.scheduler import loop

    monkeypatch.setattr(loop.time, "time", lambda: 100000.0)
    loop._last_trigger.clear()
    loop._mark("morning_greeting", char_id="yexuan")

    assert loop._is_ready("morning_greeting", char_id="yexuan") is False
    assert loop._is_ready("morning_greeting", char_id="yexuanJ-5412") is True
    assert "yexuan:morning_greeting" in loop._last_trigger


async def _record_async(target: list, value):
    target.append(value)
    return 1
