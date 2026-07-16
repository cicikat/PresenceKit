"""Scenario arc director and stage-advance integration contracts."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _layer(bucket: str) -> str:
    from core.dream.dream_prompt import _format_scenario_layer

    return _format_scenario_layer({
        "script_id": "test_short", "current_stage_id": "rooftop_meet",
        "_arc_mode": "arc", "_tension_bucket": bucket,
    })


def test_arc_below_target_injects_pressure():
    assert "张力导演：收紧节奏，推进冲突或靠近。" in _layer("low")


def test_arc_above_target_injects_release():
    assert "张力导演：放缓，给彼此喘息，退半步。" in _layer("high")


def test_arc_at_target_injects_no_director():
    layer = _layer("rising")
    assert "张力导演" not in layer


async def _run_two_satisfied_turns(sandbox, *, script_id: str, projected_tension: float) -> dict:
    from core.dream.dream_pipeline import enter_dream, dream_turn
    from core.dream.dream_settings import save
    from core.dream.dream_state import read_state

    uid = f"arc_{script_id}_{int(projected_tension * 100)}"
    save(uid, {"enable_dream_lorebook": False, "scenario_arc_mode": "arc"})
    character = MagicMock(name="character")
    character.name = "Companion"
    character.description = "A companion"
    character.gender = "male"
    character.jailbreak_entries = []
    pipeline = MagicMock(character=character)
    snapshot = {"created_at": 1, "user_id": uid, "entry_reason": "test", "relationship_state": {},
                "recent_reality_context": "", "episodic_summary": "", "mid_term_context": "", "profile_impression": ""}
    with (
        patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value=snapshot)),
        patch("core.pipeline_registry.get", return_value=pipeline),
        patch("core.dream.dream_hud.delete_hud_state"),
    ):
        result = await enter_dream(uid, char_id="yexuan", dream_mode="scenario", script_id=script_id)
    assert result["ok"] is True

    response = ('reply\n<scenario_control>{"progress_signal":"satisfied",'
                '"matched_exit_signs":[],"blocked_events":[]}</scenario_control>')
    projection = {"d5_text": "", "yexuan_tension": projected_tension}
    with (
        patch("core.dream.dream_log.read_current", return_value=[]),
        patch("core.dream.dream_log.append_turn"),
        patch("core.pipeline_registry.get", return_value=pipeline),
        patch("core.dream.dream_prompt.build_dream_prompt", return_value=[{"role": "system", "content": "x"}]),
        patch("core.llm_client.chat", new=AsyncMock(return_value=response)),
        patch("core.dream.body_tracker.analyze_turn", return_value=MagicMock(to_dict=lambda: {})),
        patch("core.dream.body_projection.project_body_for_yexuan", return_value=projection),
        patch("core.narrative_parser.parse_narrative_segments", return_value={"segments": [], "content": "reply"}),
    ):
        await dream_turn(uid, "one")
        await dream_turn(uid, "two")
    return read_state(uid)["scenario_core"]


@pytest.mark.asyncio
async def test_arc_below_target_blocks_satisfied_advance(sandbox):
    state = await _run_two_satisfied_turns(sandbox, script_id="test_short", projected_tension=0.0)
    assert state["current_stage_id"] == "rooftop_meet"


@pytest.mark.asyncio
async def test_arc_at_target_allows_satisfied_advance(sandbox):
    state = await _run_two_satisfied_turns(sandbox, script_id="test_short", projected_tension=0.3)
    assert state["current_stage_id"] == "unsaid_words"


@pytest.mark.asyncio
async def test_missing_arc_field_behaves_like_linear(sandbox):
    state = await _run_two_satisfied_turns(sandbox, script_id="prison_demo", projected_tension=0.0)
    assert state["current_stage_id"] == "negotiation"
