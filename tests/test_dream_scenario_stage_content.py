"""
tests/test_dream_scenario_stage_content.py — Scenario Mode 阶段内容注入契约

从 test_dream_scenario_v0.py 拆出（Brief 50 · 工单D.2），是拆分出的 5 个文件
之一：DS 层 prompt 里「当前 stage 出现、后续 stage 不泄漏、drift_pressure 门控、
Mirror HUD 字段不混入」的部分。

Covers:
  4. Prompt 中出现当前 stage（DS 层包含 dramatic_task / entry_pressure）
  5. Prompt 中不出现后续 stage（DS 层只注入 stage[0]，不含 stage[1] 内容）
  - drift_pressure 阈值门控 + 不跨 stage 泄漏
  - Mirror HUD 字段（dream_depth/dream_stability/symbolic_anchors）不混入 DS 层
  - stage 推进后 prompt 显示新 stage、旧/未来 stage 内容不残留
  - 推进后新 stage 的 drift_pressure 不会立即触发（stage_turns 清零核对）
"""

import time
from typing import Any
from unittest.mock import MagicMock

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


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4 — Prompt 中出现当前 stage
# ═══════════════════════════════════════════════════════════════════════════════

def test_prompt_contains_current_stage():
    """DS layer in prompt includes dramatic_task and entry_pressure for current stage."""
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    scenario_core = _make_scenario_core()
    local_state = {"emotional_tension": 0.0}

    messages = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=_EMPTY_SNAPSHOT,
        dream_history=[],
        local_state=local_state,
        dream_mode="scenario",
        scenario_core=scenario_core,
    )

    system = dump_dream_prompt(messages)
    assert "DS·剧本当前阶段" in system
    # arrival stage content
    assert "初次相遇" in system          # stage name
    assert "囚犯" in system              # from dramatic_task
    assert "铁门" in system              # from entry_pressure


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5 — Prompt 中不出现后续 stage
# ═══════════════════════════════════════════════════════════════════════════════

def test_prompt_does_not_contain_subsequent_stages():
    """DS layer must not include content from stage[1] (negotiation) when at stage[0]."""
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    scenario_core = _make_scenario_core()   # current_stage_id = arrival
    local_state = {"emotional_tension": 0.0}

    messages = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=_EMPTY_SNAPSHOT,
        dream_history=[],
        local_state=local_state,
        dream_mode="scenario",
        scenario_core=scenario_core,
    )

    system = dump_dream_prompt(messages)
    # Stage 1 (negotiation) content must not appear
    assert "秘密交换" not in system          # stage 1 name
    assert "今天他比平时晚了" not in system   # stage 1 entry_pressure
    # Stage 2 (fracture) content must not appear
    assert "裂缝" not in system


# ═══════════════════════════════════════════════════════════════════════════════
# Phase D — drift_pressure prompt injection
# ═══════════════════════════════════════════════════════════════════════════════

def test_drift_pressure_absent_below_threshold():
    """stage_turns < after_turns (6): drift pressure block must not appear in DS layer."""
    from core.dream.dream_prompt import _format_scenario_layer

    sc = {
        "script_id": "prison_demo",
        "current_stage_id": "arrival",
        "stage_turns": 3,
        "ending_state": None,
    }
    text = _format_scenario_layer(sc)
    assert "漂移压力" not in text
    assert "Drift Pressure" not in text


def test_drift_pressure_injected_at_threshold():
    """stage_turns >= after_turns (6): drift pressure instruction appears in DS layer."""
    from core.dream.dream_prompt import _format_scenario_layer

    sc = {
        "script_id": "prison_demo",
        "current_stage_id": "arrival",
        "stage_turns": 7,
        "ending_state": None,
    }
    text = _format_scenario_layer(sc)
    assert "漂移压力" in text
    assert "Drift Pressure" in text
    # Content from arrival's drift_pressure.instruction
    assert "巡视时间" in text


def test_drift_pressure_subsequent_stage_does_not_leak():
    """Being at arrival with high stage_turns must NOT inject negotiation's drift_pressure."""
    from core.dream.dream_prompt import _format_scenario_layer

    sc_arrival = {
        "script_id": "prison_demo",
        "current_stage_id": "arrival",
        "stage_turns": 10,
        "ending_state": None,
    }
    text = _format_scenario_layer(sc_arrival)

    # arrival's drift_pressure appears (stage_turns=10 >= 6)
    assert "漂移压力" in text
    # negotiation's drift_pressure instruction must NOT appear
    assert "巡视组" not in text


def test_v071_drift_pressure_not_shown_immediately_after_advance():
    """New stage at stage_turns == 0 must not show drift_pressure (below threshold=6)."""
    from core.dream.dream_prompt import _format_scenario_layer

    sc = {
        "script_id": "prison_demo",
        "current_stage_id": "negotiation",
        "stage_turns": 0,
        "ending_state": None,
        "satisfied_streak": 0,
    }
    text = _format_scenario_layer(sc)

    assert "漂移压力" not in text, (
        "stage_turns=0 must not trigger drift_pressure injection; "
        "if this fails, the transition turn inflated the new stage's turn count"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Phase E — isolation regression: Mirror HUD fields absent from scenario DS layer
# ═══════════════════════════════════════════════════════════════════════════════

def test_scenario_ds_layer_excludes_mirror_hud_fields():
    """DS scenario layer must not contain Mirror-mode HUD fields."""
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    scenario_core = _make_scenario_core()
    local_state = {"emotional_tension": 0.0}

    messages = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=_EMPTY_SNAPSHOT,
        dream_history=[],
        local_state=local_state,
        dream_mode="scenario",
        scenario_core=scenario_core,
    )
    system = dump_dream_prompt(messages)

    # Isolate the DS layer section
    ds_start = system.find("DS·剧本当前阶段")
    assert ds_start != -1, "DS layer header not found"
    ds_section = system[ds_start:]

    # Mirror HUD fields must not appear in DS layer
    mirror_fields = ("dream_depth", "dream_stability", "symbolic_anchors",
                     "dream_depth:", "dream_stability:")
    for field in mirror_fields:
        assert field not in ds_section, (
            f"Mirror HUD field {field!r} leaked into DS layer"
        )


def test_scenario_no_mirror_hud_layer_injected():
    """When dream_mode=scenario, Mirror HUD layer (D6 style numbers) is not injected."""
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    scenario_core = _make_scenario_core()
    local_state = {"emotional_tension": 0.0}

    messages = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=_EMPTY_SNAPSHOT,
        dream_history=[],
        local_state=local_state,
        dream_mode="scenario",
        scenario_core=scenario_core,
    )
    system = dump_dream_prompt(messages)

    # These are Mirror-only numeric HUD fields; they must not appear anywhere in the prompt
    assert "dream_depth" not in system
    assert "dream_stability" not in system


# ═══════════════════════════════════════════════════════════════════════════════
# Subsequent-stage exit_signs must not leak into prompt (v0.6 Test 9)
# ═══════════════════════════════════════════════════════════════════════════════

def test_subsequent_stage_exit_signs_not_in_prompt():
    """exit_signs from stage[1] (negotiation) must not appear in prompt when at stage[0]."""
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    scenario_core = _make_scenario_core()  # current_stage_id = arrival
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

    # arrival exit_signs ARE present (as control protocol reference)
    assert "双方有了第一次真实的对话" in system

    # negotiation (stage 1) exit_signs must NOT appear
    assert "她接受了他带来的东西" not in system
    assert "两人之间有了不能被人看见的默契" not in system
    # fracture (stage 2) exit_signs must NOT appear
    assert "他承认他知道自己在做什么" not in system


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt reflects the new stage after advance (v0.7 Test 11-12)
# ═══════════════════════════════════════════════════════════════════════════════

def test_prompt_after_advance_shows_new_stage():
    """_format_scenario_layer with negotiation stage_id shows negotiation content."""
    from core.dream.dream_prompt import _format_scenario_layer

    sc_negotiation = {
        "script_id": "prison_demo",
        "current_stage_id": "negotiation",
        "stage_turns": 0,
        "ending_state": None,
        "satisfied_streak": 0,
    }
    text = _format_scenario_layer(sc_negotiation)

    assert "秘密交换" in text           # negotiation stage name
    assert "今天他比平时晚了" in text    # negotiation entry_pressure
    assert "初次相遇" not in text        # arrival stage name must not appear
    assert "铁门" not in text            # arrival entry_pressure must not appear


def test_future_stages_do_not_leak_at_arrival():
    """At arrival stage, fracture and negotiation content must not appear."""
    from core.dream.dream_prompt import _format_scenario_layer

    sc = {
        "script_id": "prison_demo",
        "current_stage_id": "arrival",
        "stage_turns": 0,
        "ending_state": None,
        "satisfied_streak": 0,
    }
    text = _format_scenario_layer(sc)

    # arrival present
    assert "初次相遇" in text
    # negotiation must not appear
    assert "秘密交换" not in text
    assert "今天他比平时晚了" not in text
    # fracture must not appear
    assert "裂缝" not in text
    assert "替她撒了谎" not in text
