"""
tests/test_dream_scenario_stage_progression.py — stage_turns 计数 + satisfied
streak 推进逻辑

从 test_dream_scenario_v0.py 拆出（Brief 50 · 工单D.2），是拆分出的 6 个文件
之一：ScenarioCore 的 stage_turns/satisfied_streak 状态机 + dream_turn 集成级
验证。v0.7.1 的 off-by-one 语义审计另见
tests/test_dream_scenario_stage_turns_offbyone.py（为满足 ≤500 行限制单独成文件）。

Covers（v0 Phase B + v0.7 Stage Transition MVP）:
  - stage_turns 每回合递增；连续两次 dream_turn 递增两次
  - satisfied 一次不推进；连续两次触发 advance_to_stage
  - advance_to_stage 清零 stage_turns/last_progress_signal/satisfied_streak
  - approaching / not_close 打断 satisfied_streak
  - 缺失控制块不推进；最后一个 stage 连续 satisfied → ending_state=completed
  - sandbox 模式 dream_turn 不受 scenario 逻辑影响，不写 scenario_core
  - advance_to_stage 后的 dict 仍不含 hidden_state/impression/Mirror 字段
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from typing import Any
import time

_UID = "scenario_test_user"

_FAKE_CHARACTER = MagicMock()
_FAKE_CHARACTER.name = "Companion"
_FAKE_CHARACTER.description = "Companion是圣塞西尔学院的老师"
_FAKE_CHARACTER.gender = "male"
_FAKE_CHARACTER.jailbreak_entries = []

_EMPTY_SNAPSHOT: dict[str, Any] = {
    "created_at": time.time(),
    "user_id": _UID,
    "entry_reason": "test",
    "relationship_state": {},
    "recent_reality_context": "",
    "episodic_summary": "",
    "mid_term_context": "",
    "profile_impression": "",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Phase B — stage_turns increment
# ═══════════════════════════════════════════════════════════════════════════════

def test_scenario_core_increment_stage_turns():
    """ScenarioCore.increment_stage_turns returns new frozen instance with stage_turns+1."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(script_id="prison_demo", current_stage_id="arrival", stage_turns=0)
    sc1 = sc.increment_stage_turns()
    assert sc1.stage_turns == 1
    assert sc.stage_turns == 0  # original is frozen, unchanged

    sc2 = sc1.increment_stage_turns()
    assert sc2.stage_turns == 2


def test_dream_turn_increments_scenario_stage_turns(sandbox):
    """dream_turn increments scenario_core.stage_turns to 1 after successful reply."""
    from core.dream.dream_pipeline import enter_dream, dream_turn
    from core.dream.dream_state import read_state
    from core.dream.dream_settings import save as save_settings

    save_settings(_UID, {"enable_dream_lorebook": False})
    fake_snapshot = dict(_EMPTY_SNAPSHOT)
    fake_pipeline = MagicMock()
    fake_pipeline.character = _FAKE_CHARACTER

    with (
        patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value=fake_snapshot)),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_hud.delete_hud_state"),
    ):
        r = asyncio.run(enter_dream(
            _UID, char_id="yexuan", dream_mode="scenario", script_id="prison_demo"
        ))
    assert r.get("ok") is True

    fake_msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    with (
        patch("core.dream.dream_log.read_current", return_value=[]),
        patch("core.dream.dream_log.append_turn"),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_prompt.build_dream_prompt", return_value=fake_msgs),
        patch("core.llm_client.chat", new=AsyncMock(return_value="Companion回复了")),
        patch(
            "core.dream.body_tracker.analyze_turn",
            return_value=MagicMock(to_dict=lambda: {}),
        ),
        patch(
            "core.dream.body_projection.project_body_for_yexuan",
            return_value={"d5_text": "", "yexuan_tension": 0.0},
        ),
        patch(
            "core.narrative_parser.parse_narrative_segments",
            return_value={"segments": [], "content": "Companion回复了"},
        ),
    ):
        result = asyncio.run(dream_turn(_UID, "你好"))

    assert result.get("error") is None, f"dream_turn error: {result.get('error')}"
    state = read_state(_UID)
    assert state.get("scenario_core", {}).get("stage_turns") == 1


def test_dream_turn_stage_turns_increments_twice(sandbox):
    """Two consecutive dream_turns increment stage_turns to 2."""
    from core.dream.dream_pipeline import enter_dream, dream_turn
    from core.dream.dream_state import read_state
    from core.dream.dream_settings import save as save_settings

    save_settings(_UID, {"enable_dream_lorebook": False})
    fake_snapshot = dict(_EMPTY_SNAPSHOT)
    fake_pipeline = MagicMock()
    fake_pipeline.character = _FAKE_CHARACTER

    with (
        patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value=fake_snapshot)),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_hud.delete_hud_state"),
    ):
        asyncio.run(enter_dream(
            _UID, char_id="yexuan", dream_mode="scenario", script_id="prison_demo"
        ))

    fake_msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    with (
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_log.read_current", return_value=[]),
        patch("core.dream.dream_log.append_turn"),
        patch("core.dream.dream_prompt.build_dream_prompt", return_value=fake_msgs),
        patch("core.llm_client.chat", new=AsyncMock(return_value="回复1")),
        patch("core.dream.body_tracker.analyze_turn",
              return_value=MagicMock(to_dict=lambda: {})),
        patch("core.dream.body_projection.project_body_for_yexuan",
              return_value={"d5_text": "", "yexuan_tension": 0.0}),
        patch("core.narrative_parser.parse_narrative_segments",
              return_value={"segments": [], "content": "回复1"}),
    ):
        asyncio.run(dream_turn(_UID, "你好"))
        asyncio.run(dream_turn(_UID, "再说一次"))

    state = read_state(_UID)
    assert state.get("scenario_core", {}).get("stage_turns") == 2


# ═══════════════════════════════════════════════════════════════════════════════
# v0.7 — Stage Transition MVP
# ═══════════════════════════════════════════════════════════════════════════════

def test_satisfied_once_does_not_advance():
    """satisfied_streak == 1 after one satisfied signal; stage unchanged."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(script_id="prison_demo", current_stage_id="arrival")
    sc2 = sc.with_progress_signal("satisfied")
    assert sc2.satisfied_streak == 1
    assert sc2.current_stage_id == "arrival"


def test_satisfied_twice_advances_stage():
    """Two consecutive satisfied signals trigger advance_to_stage(negotiation)."""
    from core.dream.scenario_core import ScenarioCore
    from core.dream.scenario_loader import load_script, get_next_stage

    sc = ScenarioCore(script_id="prison_demo", current_stage_id="arrival")
    sc = sc.with_progress_signal("satisfied")  # streak = 1
    sc = sc.with_progress_signal("satisfied")  # streak = 2 → advance
    assert sc.satisfied_streak == 2

    script = load_script("prison_demo")
    next_stage = get_next_stage(script, "arrival")
    assert next_stage is not None
    sc_advanced = sc.advance_to_stage(next_stage["id"])
    assert sc_advanced.current_stage_id == "negotiation"


def test_advance_resets_stage_turns():
    """advance_to_stage sets stage_turns = 0."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(
        script_id="prison_demo", current_stage_id="arrival", stage_turns=5
    )
    sc2 = sc.advance_to_stage("negotiation")
    assert sc2.stage_turns == 0
    assert sc.stage_turns == 5  # original frozen, unchanged


def test_advance_clears_last_progress_signal():
    """advance_to_stage sets last_progress_signal = None."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(
        script_id="prison_demo", current_stage_id="arrival",
    )
    sc = sc.with_progress_signal("satisfied", ["她说出了自己的名字"], [])
    sc2 = sc.advance_to_stage("negotiation")
    assert sc2.last_progress_signal is None
    assert sc2.last_matched_exit_signs == []
    assert sc2.last_blocked_events == []


def test_advance_clears_satisfied_streak():
    """advance_to_stage sets satisfied_streak = 0."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(script_id="prison_demo", current_stage_id="arrival")
    sc = sc.with_progress_signal("satisfied")
    sc = sc.with_progress_signal("satisfied")
    assert sc.satisfied_streak == 2
    sc2 = sc.advance_to_stage("negotiation")
    assert sc2.satisfied_streak == 0


def test_approaching_interrupts_satisfied_streak():
    """approaching after satisfied resets streak to 0."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(script_id="prison_demo", current_stage_id="arrival")
    sc = sc.with_progress_signal("satisfied")
    assert sc.satisfied_streak == 1
    sc = sc.with_progress_signal("approaching")
    assert sc.satisfied_streak == 0
    assert sc.current_stage_id == "arrival"


def test_not_close_interrupts_satisfied_streak():
    """not_close after satisfied resets streak to 0."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(script_id="prison_demo", current_stage_id="arrival")
    sc = sc.with_progress_signal("satisfied")
    assert sc.satisfied_streak == 1
    sc = sc.with_progress_signal("not_close")
    assert sc.satisfied_streak == 0


def test_missing_control_does_not_advance():
    """reset_satisfied_streak prevents streak from reaching 2 via a missing turn."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(script_id="prison_demo", current_stage_id="arrival")
    sc = sc.with_progress_signal("satisfied")   # streak = 1
    sc = sc.reset_satisfied_streak()             # control block absent — reset
    assert sc.satisfied_streak == 0
    assert sc.current_stage_id == "arrival"

    # Even one more satisfied after reset: streak = 1, not 2 → no advance
    sc = sc.with_progress_signal("satisfied")
    assert sc.satisfied_streak == 1
    assert sc.current_stage_id == "arrival"


def test_last_stage_satisfied_twice_marks_completed():
    """advance_to_stage on final stage returns None; mark_completed sets ending_state."""
    from core.dream.scenario_core import ScenarioCore
    from core.dream.scenario_loader import load_script, get_next_stage

    script = load_script("prison_demo")
    # fracture is the last stage in prison_demo
    sc = ScenarioCore(script_id="prison_demo", current_stage_id="fracture")
    sc = sc.with_progress_signal("satisfied")
    sc = sc.with_progress_signal("satisfied")
    assert sc.satisfied_streak == 2

    next_stage = get_next_stage(script, "fracture")
    assert next_stage is None  # fracture is the last stage

    sc_done = sc.mark_completed()
    assert sc_done.ending_state == "completed"
    assert sc_done.current_stage_id == "fracture"  # stays at last stage


def test_sandbox_dream_turn_not_affected_by_scenario_logic(sandbox):
    """In sandbox mode, dream_turn does not create or modify scenario_core."""
    from core.dream.dream_pipeline import enter_dream, dream_turn
    from core.dream.dream_state import read_state
    from core.dream.dream_settings import save as save_settings

    save_settings(_UID, {"enable_dream_lorebook": False})
    fake_snapshot = dict(_EMPTY_SNAPSHOT)
    fake_pipeline = MagicMock()
    fake_pipeline.character = _FAKE_CHARACTER

    with (
        patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value=fake_snapshot)),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_hud.delete_hud_state"),
    ):
        r = asyncio.run(enter_dream(
            _UID, char_id="yexuan", dream_mode="sandbox"
        ))
    assert r.get("ok") is True

    llm_response = (
        "Companion安静地看着她。\n"
        "<scenario_control>\n"
        '{"progress_signal": "satisfied", "matched_exit_signs": [], "blocked_events": []}\n'
        "</scenario_control>"
    )
    fake_msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]

    with (
        patch("core.dream.dream_log.read_current", return_value=[]),
        patch("core.dream.dream_log.append_turn"),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_prompt.build_dream_prompt", return_value=fake_msgs),
        patch("core.llm_client.chat", new=AsyncMock(return_value=llm_response)),
        patch("core.dream.body_tracker.analyze_turn",
              return_value=MagicMock(to_dict=lambda: {})),
        patch("core.dream.body_projection.project_body_for_yexuan",
              return_value={"d5_text": "", "yexuan_tension": 0.0}),
        patch("core.narrative_parser.parse_narrative_segments",
              return_value={"segments": [], "content": "Companion安静地看着她。"}),
    ):
        result = asyncio.run(dream_turn(_UID, "你好"))

    assert result.get("error") is None
    state = read_state(_UID)
    # sandbox mode: no scenario_core written
    assert "scenario_core" not in state


def test_advance_to_stage_dict_excludes_isolation_fields():
    """ScenarioCore after advance_to_stage must not contain hidden_state, impression,
    or Mirror HUD fields."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(script_id="prison_demo", current_stage_id="arrival")
    sc = sc.with_progress_signal("satisfied")
    sc = sc.with_progress_signal("satisfied")
    sc2 = sc.advance_to_stage("negotiation")
    d = sc2.to_dict()

    forbidden = {
        "sensitivity", "touch_appetite", "embodied_ease",
        "memory_cues", "user_hidden_state", "hidden_state_snapshot",
        "symbolic_anchors", "dream_depth", "dream_stability",
        "impression", "impression_delta", "afterglow",
        "long_term_integration", "distill_impression",
    }
    for f in forbidden:
        assert f not in d, f"advance_to_stage must not contain {f!r}"


# v0.7.1 stage_turns off-by-one audit continues in
# tests/test_dream_scenario_stage_turns_offbyone.py (split out to satisfy ≤500-line limit).
