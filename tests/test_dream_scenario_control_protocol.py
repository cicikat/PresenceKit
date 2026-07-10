"""
tests/test_dream_scenario_control_protocol.py — <scenario_control> 输出协议契约

从 test_dream_scenario_v0.py 拆出（Brief 50 · 工单D.2），是拆分出的 5 个文件
之一：LLM 输出中 <scenario_control> 控制块的存在性、解析、剥离、写入 state。

Covers（v0.6 Progress Signal Skeleton）:
  - Scenario prompt 含 <scenario_control> 输出协议；sandbox 不含
  - 合法控制块被解析、从可见回复中剥离
  - progress_signal / matched_exit_signs / blocked_events 正确写入
    scenario_core.last_progress_signal 等字段
  - 非法 progress_signal → 不更新，不崩溃；缺失控制块 → 不崩溃
  - LLM 无法通过控制块里的 next_stage 字段指定下一阶段（parser 丢弃未知键）
  - dream_turn 落盘的可见回复和 dream log 均不含控制块原文
"""

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_scenario_core() -> dict[str, Any]:
    return {
        "script_id": "prison_demo",
        "current_stage_id": "arrival",
        "stage_turns": 0,
        "ending_state": None,
    }


# ── Test 1: Scenario prompt contains <scenario_control> output protocol ───────

def test_scenario_prompt_contains_control_protocol():
    """DS layer in scenario prompt includes the <scenario_control> output protocol."""
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    scenario_core = _make_scenario_core()
    messages = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=_EMPTY_SNAPSHOT,
        dream_history=[],
        local_state={},
        dream_mode="scenario",
        scenario_core=scenario_core,
    )
    system = dump_dream_prompt(messages)

    assert "scenario_control" in system
    assert "progress_signal" in system
    assert "not_close" in system
    assert "approaching" in system
    assert "satisfied" in system
    # Current stage exit_signs listed as reference
    assert "双方有了第一次真实的对话" in system
    assert "她说出了自己的名字" in system


# ── Test 2: Sandbox prompt does NOT contain scenario_control ──────────────────

def test_sandbox_prompt_excludes_control_protocol():
    """sandbox dream mode never includes the <scenario_control> block."""
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    messages = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=_EMPTY_SNAPSHOT,
        dream_history=[],
        local_state={},
        dream_mode="sandbox",
        scenario_core=None,
    )
    system = dump_dream_prompt(messages)
    assert "scenario_control" not in system


# ── Test 3: Valid control block is parsed and stripped from visible reply ──────

def test_extract_scenario_control_valid():
    """Valid control block is parsed; visible reply has block removed."""
    from core.dream.dream_pipeline import _extract_scenario_control

    raw = (
        "Companion看了她一眼，没说话。\n"
        "<scenario_control>\n"
        '{"progress_signal": "approaching", "matched_exit_signs": ["双方有了第一次真实的对话"], "blocked_events": []}\n'
        "</scenario_control>"
    )
    visible, ctrl = _extract_scenario_control(raw)

    assert "scenario_control" not in visible
    assert "Companion看了她一眼" in visible
    assert ctrl is not None
    assert ctrl["progress_signal"] == "approaching"
    assert ctrl["matched_exit_signs"] == ["双方有了第一次真实的对话"]
    assert ctrl["blocked_events"] == []


# ── Test 4: last_progress_signal correctly saved to state via dream_turn ──────

def test_dream_turn_saves_progress_signal(sandbox):
    """dream_turn writes last_progress_signal to scenario_core when control block is valid."""
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

    llm_response = (
        "Companion沉默地看着她。\n"
        "<scenario_control>\n"
        '{"progress_signal": "satisfied", "matched_exit_signs": ["她说出了自己的名字"], "blocked_events": ["Companion主动表露情感"]}\n'
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
              return_value={"segments": [], "content": "Companion沉默地看着她。"}),
    ):
        result = asyncio.run(dream_turn(_UID, "我叫林梦。"))

    assert result.get("error") is None
    sc = read_state(_UID).get("scenario_core", {})
    assert sc.get("last_progress_signal") == "satisfied"
    assert sc.get("stage_turns") == 1


# ── Test 5: matched_exit_signs correctly saved ────────────────────────────────

def test_with_progress_signal_saves_matched_exit_signs():
    """ScenarioCore.with_progress_signal stores matched_exit_signs correctly."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(script_id="prison_demo", current_stage_id="arrival")
    sc2 = sc.with_progress_signal(
        "satisfied",
        matched_exit_signs=["她说出了自己的名字"],
        blocked_events=[],
    )
    assert sc2.last_matched_exit_signs == ["她说出了自己的名字"]
    assert sc.last_matched_exit_signs == []  # original frozen, unchanged


# ── Test 6: blocked_events correctly saved ────────────────────────────────────

def test_with_progress_signal_saves_blocked_events():
    """ScenarioCore.with_progress_signal stores blocked_events correctly."""
    from core.dream.scenario_core import ScenarioCore

    sc = ScenarioCore(script_id="prison_demo", current_stage_id="arrival")
    sc2 = sc.with_progress_signal(
        "not_close",
        matched_exit_signs=[],
        blocked_events=["Companion主动表露情感"],
    )
    assert sc2.last_blocked_events == ["Companion主动表露情感"]
    assert sc.last_blocked_events == []  # original frozen, unchanged


# ── Test 7: Invalid progress_signal → no update, no crash ────────────────────

def test_extract_scenario_control_invalid_signal():
    """Illegal progress_signal returns None control; visible reply still stripped."""
    from core.dream.dream_pipeline import _extract_scenario_control

    raw = (
        "回复文本。"
        "<scenario_control>"
        '{"progress_signal": "stage_complete", "matched_exit_signs": [], "blocked_events": []}'
        "</scenario_control>"
    )
    visible, ctrl = _extract_scenario_control(raw)

    assert "scenario_control" not in visible
    assert ctrl is None  # invalid signal → no update


# ── Test 8: Missing control block → no crash ─────────────────────────────────

def test_extract_scenario_control_missing():
    """When no control block is present, reply is unchanged and control is None."""
    from core.dream.dream_pipeline import _extract_scenario_control

    raw = "普通的Companion回复，没有任何控制块。"
    visible, ctrl = _extract_scenario_control(raw)

    assert visible == raw
    assert ctrl is None


# ── Test 10: Control block not in visible reply or dream log ──────────────────

def test_scenario_control_stripped_from_reply_and_log(sandbox):
    """dream_turn strips control block from visible reply and dream log entry."""
    from core.dream.dream_pipeline import enter_dream, dream_turn
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

    llm_response = (
        "Companion抬起眼睛。\n"
        "<scenario_control>\n"
        '{"progress_signal": "not_close", "matched_exit_signs": [], "blocked_events": []}\n'
        "</scenario_control>"
    )
    fake_msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    logged_assistant_turns: list[str] = []

    def _capture_turn(uid, did, role, content, **kw):
        if role == "assistant":
            logged_assistant_turns.append(content)

    with (
        patch("core.dream.dream_log.read_current", return_value=[]),
        patch("core.dream.dream_log.append_turn", side_effect=_capture_turn),
        patch("core.pipeline_registry.get", return_value=fake_pipeline),
        patch("core.dream.dream_prompt.build_dream_prompt", return_value=fake_msgs),
        patch("core.llm_client.chat", new=AsyncMock(return_value=llm_response)),
        patch("core.dream.body_tracker.analyze_turn",
              return_value=MagicMock(to_dict=lambda: {})),
        patch("core.dream.body_projection.project_body_for_yexuan",
              return_value={"d5_text": "", "yexuan_tension": 0.0}),
        patch("core.narrative_parser.parse_narrative_segments",
              return_value={"segments": [], "content": "Companion抬起眼睛。"}),
    ):
        result = asyncio.run(dream_turn(_UID, "你好"))

    assert "scenario_control" not in result.get("reply", "")
    assert len(logged_assistant_turns) == 1
    assert "scenario_control" not in logged_assistant_turns[0]
    assert "Companion抬起眼睛。" in logged_assistant_turns[0]


# ── Test 11: New ScenarioCore fields isolated from hidden state / impression ──

def test_scenario_core_new_fields_exclude_hidden_and_impression():
    """New progress signal fields in ScenarioCore contain no hidden state or impression data."""
    from core.dream.scenario_core import ScenarioCore
    from core.dream.scenario_loader import load_script

    script = load_script("prison_demo")
    core = ScenarioCore.from_script(script)
    d = core.to_dict()

    # New fields exist with correct defaults
    assert "last_progress_signal" in d
    assert d["last_progress_signal"] is None
    assert "last_matched_exit_signs" in d
    assert d["last_matched_exit_signs"] == []
    assert "last_blocked_events" in d
    assert d["last_blocked_events"] == []

    # After with_progress_signal, no hidden state or impression leakage
    sc2 = core.with_progress_signal("satisfied", ["双方有了第一次真实的对话"], [])
    d2 = sc2.to_dict()
    forbidden_fields = {
        "sensitivity", "touch_appetite", "embodied_ease",
        "memory_cues", "user_hidden_state", "hidden_state_snapshot",
        "symbolic_anchors", "dream_depth", "dream_stability",
        "impression", "impression_delta", "afterglow",
        "long_term_integration", "distill_impression",
    }
    for f in forbidden_fields:
        assert f not in d2, f"ScenarioCore v0.6 must not contain {f!r}"


# ── Test: LLM cannot specify next_stage via control block ─────────────────

def test_control_block_ignores_next_stage_key():
    """_extract_scenario_control ignores any next_stage field in the control JSON."""
    from core.dream.dream_pipeline import _extract_scenario_control

    raw = (
        "Companion抬眼看她。\n"
        "<scenario_control>\n"
        '{"progress_signal": "satisfied", "matched_exit_signs": [], "blocked_events": [],'
        ' "next_stage": "negotiation"}\n'
        "</scenario_control>"
    )
    visible, ctrl = _extract_scenario_control(raw)

    assert ctrl is not None
    assert ctrl["progress_signal"] == "satisfied"
    assert "next_stage" not in ctrl  # parser must strip unknown keys
