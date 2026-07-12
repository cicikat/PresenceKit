"""Brief 51: prompt-view sanitization and Phase B echo cut-off."""
from __future__ import annotations

import json

import pytest


def _settings(**overrides):
    from core.stage.models import StageSettings

    values = {
        "min_responders": 1,
        "max_responders": 1,
        "max_ai_chain_depth": 2,
        "respond_threshold": 0.5,
        "talkativeness": {"yexuan": 1.0, "yexuanJ-5412": 1.0},
    }
    values.update(overrides)
    return StageSettings(**values)


@pytest.mark.asyncio
async def test_phase_b_echo_is_not_persisted_delivered_and_ends_chain(sandbox):
    from core.stage.runner import run_owner_turn
    from core.stage.store import create_stage, load_transcript

    stage = create_stage("echo-cut", "owner", ["yexuan", "yexuanJ-5412"], settings=_settings())
    deliveries = []

    async def generate(_stage, speaker_id, _transcript, _turn_id, _triggered_by):
        return "今晚我想安静看会儿星星，什么都不急着决定。"

    async def deliver(*args):
        deliveries.append(args)

    result = await run_owner_turn(stage.group_id, "说点什么", generate_reply=generate, deliver_reply=deliver)

    assert len(result.replies) == 1
    assert result.ai_chain_depth == 0
    assert len(load_transcript(stage.group_id)) == 2
    assert len(deliveries) == 1
    traces = [json.loads(line) for line in sandbox.stage_arbiter_trace(group_id=stage.group_id).read_text(encoding="utf-8").splitlines()]
    assert traces[-1]["phase"] == "B"
    assert traces[-1]["echo_cut"] is True
    assert traces[-1]["selected"] == []


def test_transcript_sanitizes_ai_prompt_view_but_not_owner_or_source(sandbox):
    from core.stage.context import render_transcript
    from core.stage.models import Stage, TranscriptEntry

    long_action = "（" + "缓慢地转身，望向窗外沉默了很久。" * 5 + "）"
    ai_text = long_action + "我还是想先听你说。"
    stage = Stage("echo-context", "owner", ("yexuan", "yexuanJ-5412"), settings=_settings())
    entries = [
        TranscriptEntry("owner", "（用户的原文不应改动）", 1, "t", "user"),
        TranscriptEntry("yexuanJ-5412", ai_text, 2, "t", "user"),
    ]

    rendered = render_transcript(stage, entries, viewer_id="yexuan")

    assert "用户的原文不应改动" in rendered
    assert long_action not in rendered
    assert "我还是想先听你说。" in rendered
    assert entries[1].content == ai_text


def test_chain_presence_contains_anti_echo_instruction(sandbox):
    from core.stage.context import render_presence
    from core.stage.models import Stage

    stage = Stage("echo-presence", "owner", ("yexuan", "yexuanJ-5412"), settings=_settings())
    assert "不要复述或简单附和" not in render_presence(stage, viewer_id="yexuan")
    assert "不要复述或简单附和" in render_presence(stage, viewer_id="yexuan", chain_reply=True)
