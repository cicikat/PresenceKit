"""
tests/test_dream_scenario_stage_turns_offbyone.py — v0.7.1 stage_turns off-by-one 审计

从 test_dream_scenario_v0.py 拆出（Brief 50 · 工单D.2），是
test_dream_scenario_stage_progression.py 的延续——原 test_dream_scenario_v0.py
拆分后该部分单独成文件以满足 ≤500 行限制。

Covers（v0.7.1）:
  - 非转场回合：stage_turns 正常从 0 递增到 1
  - 第一次 satisfied：不推进 stage，stage_turns 仍递增到 1，streak 到 1
  - 第二次 satisfied（转场回合）：属于旧 stage，新 stage 从 stage_turns=0 开始
  - 转场后新 stage 的第一个真实回合：stage_turns 递增到 1
  - mark_completed（最后一个 stage 达成）：不额外递增 stage_turns
"""

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

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

_SATISFIED_REPLY = (
    "Companion沉默地看着她。\n"
    "<scenario_control>\n"
    '{"progress_signal": "satisfied", "matched_exit_signs": ["她说出了自己的名字"], "blocked_events": []}\n'
    "</scenario_control>"
)
_NOT_CLOSE_REPLY = (
    "Companion没有回应。\n"
    "<scenario_control>\n"
    '{"progress_signal": "not_close", "matched_exit_signs": [], "blocked_events": []}\n'
    "</scenario_control>"
)
_FAKE_MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]


def _run_dream_turn(uid: str, fake_pipeline, llm_response: str) -> dict:
    """Run one dream_turn with a fixed LLM response. Returns dream_turn result."""
    from core.dream.dream_pipeline import dream_turn
    with (
        patch("core.dream.dream_log.read_current", return_value=[]),
        patch("core.dream.dream_log.append_turn"),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_prompt.build_dream_prompt", return_value=_FAKE_MSGS),
        patch("core.llm_client.chat", new=AsyncMock(return_value=llm_response)),
        patch("core.dream.body_tracker.analyze_turn",
              return_value=MagicMock(to_dict=lambda: {})),
        patch("core.dream.body_projection.project_body_for_yexuan",
              return_value={"d5_text": "", "yexuan_tension": 0.0}),
        patch("core.narrative_parser.parse_narrative_segments",
              return_value={"segments": [], "content": "Companion回复了"}),
    ):
        return asyncio.run(dream_turn(uid, "你好"))


def test_v071_normal_turn_increments_stage_turns(sandbox):
    """Non-transition turn: stage_turns increments normally from 0 to 1."""
    from core.dream.dream_pipeline import enter_dream
    from core.dream.dream_state import read_state
    from core.dream.dream_settings import save as save_settings

    save_settings(_UID, {"enable_dream_lorebook": False})
    fake_pipeline = MagicMock()
    fake_pipeline.character = _FAKE_CHARACTER

    with (
        patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value=dict(_EMPTY_SNAPSHOT))),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_hud.delete_hud_state"),
    ):
        asyncio.run(enter_dream(_UID, char_id="yexuan", dream_mode="scenario", script_id="prison_demo"))

    _run_dream_turn(_UID, fake_pipeline, _NOT_CLOSE_REPLY)

    sc = read_state(_UID).get("scenario_core", {})
    assert sc.get("current_stage_id") == "arrival"
    assert sc.get("stage_turns") == 1


def test_v071_first_satisfied_no_advance_increments_turns(sandbox):
    """First satisfied turn: no stage advance; stage_turns goes to 1, streak goes to 1."""
    from core.dream.dream_pipeline import enter_dream
    from core.dream.dream_state import read_state
    from core.dream.dream_settings import save as save_settings

    save_settings(_UID, {"enable_dream_lorebook": False})
    fake_pipeline = MagicMock()
    fake_pipeline.character = _FAKE_CHARACTER

    with (
        patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value=dict(_EMPTY_SNAPSHOT))),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_hud.delete_hud_state"),
    ):
        asyncio.run(enter_dream(_UID, char_id="yexuan", dream_mode="scenario", script_id="prison_demo"))

    _run_dream_turn(_UID, fake_pipeline, _SATISFIED_REPLY)

    sc = read_state(_UID).get("scenario_core", {})
    assert sc.get("current_stage_id") == "arrival"
    assert sc.get("satisfied_streak") == 1
    assert sc.get("stage_turns") == 1, (
        f"first satisfied: expected stage_turns=1, got {sc.get('stage_turns')}"
    )


def test_v071_second_satisfied_new_stage_starts_at_zero(sandbox):
    """The transition turn (2nd satisfied) must leave the NEW stage at stage_turns == 0.

    The transitioning turn belongs to the OLD stage.  The new stage has not been
    'entered' in any meaningful sense yet — it should start fresh at 0.
    """
    from core.dream.dream_pipeline import enter_dream
    from core.dream.dream_state import read_state
    from core.dream.dream_settings import save as save_settings

    save_settings(_UID, {"enable_dream_lorebook": False})
    fake_pipeline = MagicMock()
    fake_pipeline.character = _FAKE_CHARACTER

    with (
        patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value=dict(_EMPTY_SNAPSHOT))),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_hud.delete_hud_state"),
    ):
        asyncio.run(enter_dream(_UID, char_id="yexuan", dream_mode="scenario", script_id="prison_demo"))

    # Turn 1: satisfied — streak=1, stage_turns=1, still at arrival
    _run_dream_turn(_UID, fake_pipeline, _SATISFIED_REPLY)
    sc_after_t1 = read_state(_UID).get("scenario_core", {})
    assert sc_after_t1.get("current_stage_id") == "arrival"
    assert sc_after_t1.get("satisfied_streak") == 1

    # Turn 2: satisfied — streak=2 → advance to negotiation; new stage_turns must be 0
    _run_dream_turn(_UID, fake_pipeline, _SATISFIED_REPLY)
    sc = read_state(_UID).get("scenario_core", {})
    assert sc.get("current_stage_id") == "negotiation", (
        f"expected negotiation, got {sc.get('current_stage_id')}"
    )
    assert sc.get("stage_turns") == 0, (
        f"new stage after transition must start at stage_turns=0, got {sc.get('stage_turns')}"
    )
    assert sc.get("satisfied_streak") == 0


def test_v071_first_turn_in_new_stage_increments_to_one(sandbox):
    """After stage advance, the first real turn in the new stage sets stage_turns == 1."""
    from core.dream.dream_pipeline import enter_dream
    from core.dream.dream_state import read_state
    from core.dream.dream_settings import save as save_settings

    save_settings(_UID, {"enable_dream_lorebook": False})
    fake_pipeline = MagicMock()
    fake_pipeline.character = _FAKE_CHARACTER

    with (
        patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value=dict(_EMPTY_SNAPSHOT))),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_hud.delete_hud_state"),
    ):
        asyncio.run(enter_dream(_UID, char_id="yexuan", dream_mode="scenario", script_id="prison_demo"))

    # Turns 1+2: trigger advance to negotiation (stage_turns=0 on arrival)
    _run_dream_turn(_UID, fake_pipeline, _SATISFIED_REPLY)
    _run_dream_turn(_UID, fake_pipeline, _SATISFIED_REPLY)
    sc_at_new = read_state(_UID).get("scenario_core", {})
    assert sc_at_new.get("current_stage_id") == "negotiation"
    assert sc_at_new.get("stage_turns") == 0

    # Turn 3: first genuine turn in new stage — must increment to 1
    _run_dream_turn(_UID, fake_pipeline, _NOT_CLOSE_REPLY)
    sc = read_state(_UID).get("scenario_core", {})
    assert sc.get("current_stage_id") == "negotiation"
    assert sc.get("stage_turns") == 1, (
        f"first turn in new stage must set stage_turns=1, got {sc.get('stage_turns')}"
    )


def test_v071_mark_completed_does_not_increment_stage_turns(sandbox):
    """Completing the last stage must not call increment_stage_turns on the final stage.

    The completing turn (2nd satisfied on last stage) belongs to the final stage but
    must not cause a spurious extra increment — drift_pressure and any future reader
    must see the pre-completion stage_turns value.
    """
    from core.dream.dream_pipeline import enter_dream
    from core.dream.dream_state import read_state, write_state
    from core.dream.dream_settings import save as save_settings

    save_settings(_UID, {"enable_dream_lorebook": False})
    fake_pipeline = MagicMock()
    fake_pipeline.character = _FAKE_CHARACTER

    with (
        patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value=dict(_EMPTY_SNAPSHOT))),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_hud.delete_hud_state"),
    ):
        asyncio.run(enter_dream(_UID, char_id="yexuan", dream_mode="scenario", script_id="prison_demo"))

    # Manually jump to last stage (fracture) with satisfied_streak=1 and stage_turns=3
    state = read_state(_UID)
    state["scenario_core"].update({
        "current_stage_id": "fracture",
        "satisfied_streak": 1,
        "stage_turns": 3,
        "ending_state": None,
    })
    write_state(_UID, state)

    # Turn: 2nd satisfied on last stage → mark_completed, _did_advance=True → no increment
    _run_dream_turn(_UID, fake_pipeline, _SATISFIED_REPLY)
    sc = read_state(_UID).get("scenario_core", {})
    assert sc.get("ending_state") == "completed", (
        f"expected completed, got {sc.get('ending_state')}"
    )
    assert sc.get("current_stage_id") == "fracture"
    assert sc.get("stage_turns") == 3, (
        f"mark_completed must not increment stage_turns; expected 3, got {sc.get('stage_turns')}"
    )
