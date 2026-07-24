"""
tests/test_dream_body_tension_mechanics.py — body_state / yexuan_tension / body_tracker
单元级契约 + D-layer 结构完整性

从 test_dream_v0.py 拆出（Brief 50 · 工单D）。这部分内容在 v1/v2/mvp1 中均无
对应测试，是 body/tension 机制唯一的直接单元覆盖。

Covers:
  - body_state dream-local：dream_turn 后 dream_state 中的 body_state 有更新
  - 耦合有界：连续高 heat 输入，yexuan_tension 单轮增量 ≤0.15，永不超过 1.0，
    无信号时向 0 衰减
  - D0-D8 在 system content 中严格保持顺序
  - D5/D7 在无内容时不出现在 prompt 中
  - body_tracker.analyze_turn 的 heat 上升/单轮增量上限/cap 边界/floor 边界
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

_UID = "v0_test_user"

_FAKE_CHARACTER = MagicMock()
_FAKE_CHARACTER.name = "Companion"
_FAKE_CHARACTER.description = "Companion，男，圣塞西尔学院教师，温柔内敛，有强烈的依恋倾向。"
_FAKE_CHARACTER.gender = "male"
_FAKE_CHARACTER.jailbreak_entries = []

_FAKE_PIPELINE = MagicMock()
_FAKE_PIPELINE.character = _FAKE_CHARACTER
_FAKE_PIPELINE.lore_engine = MagicMock()
_FAKE_PIPELINE.lore_engine.match.return_value = ([], [])


def _make_fake_llm(reply: str = "梦境回复文本") -> AsyncMock:
    return AsyncMock(return_value=reply)


import pytest


@pytest.fixture
def active_dream(sandbox):
    from core.dream.dream_state import write_state, DreamStatus
    state = {
        "user_id": _UID,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{_UID}_v0test",
        "context_snapshot": {
            "created_at": time.time(),
            "user_id": _UID,
            "yexuan_awareness": "lucid_shared",
            "boundary": "dream_only",
            "entry_reason": "v0 unit test",
            "memory_access": "relationship_summary",
            "relationship_state": {},
            "recent_reality_context": "",
            "episodic_summary": "",
            "mid_term_context": "",
            "profile_impression": "",
        },
    }
    write_state(_UID, state)
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# body_state 更新（dream-local）
# ═══════════════════════════════════════════════════════════════════════════════

def test_dream_turn_with_body_updates_body_state_in_dream_state(sandbox, active_dream):
    """dream_turn 后 dream_state 中的 body_state 应有更新（trigger 词命中）。"""
    with patch("core.llm_client.chat", _make_fake_llm("（靠近她）心跳加速")), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "心跳，颤抖"))

    from core.dream.dream_state import read_state
    state = read_state(_UID)
    body = state.get("body_state") or {}
    # With heat/sensitivity trigger words in both sides, body should have some non-zero value
    total = body.get("heat", 0) + body.get("sensitivity", 0) + body.get("tension", 0)
    assert total > 0, f"body_state should update after trigger words, got {body}"


# ═══════════════════════════════════════════════════════════════════════════════
# 耦合有界：yexuan_tension 单轮增量/上限/衰减
# ═══════════════════════════════════════════════════════════════════════════════

def test_yexuan_tension_single_turn_delta_bounded():
    """单轮 heat 刺激后 yexuan_tension 增量 ≤ 0.15。"""
    from core.dream.body_state import BodyState
    from core.dream.body_projection import project_body_for_yexuan, _YEXUAN_TENSION_MAX_DELTA

    body = BodyState(heat=80.0, sensitivity=80.0, tension=60.0)
    initial_tension = 0.3
    result = project_body_for_yexuan(body, "body_perceptible", initial_tension)
    new_tension = result["yexuan_tension"]
    delta = new_tension - initial_tension

    assert delta <= _YEXUAN_TENSION_MAX_DELTA + 1e-9, (
        f"yexuan_tension single-turn delta {delta:.6f} exceeds max {_YEXUAN_TENSION_MAX_DELTA}"
    )


def test_yexuan_tension_never_exceeds_1(sandbox, active_dream):
    """连续高 heat 回合后 yexuan_tension 永不超过 1.0。"""
    from core.dream.dream_state import read_state, write_state, patch_local_state

    # Pre-load near-max tension
    state = read_state(_UID)
    state = patch_local_state(state, emotional_tension=0.95)
    write_state(_UID, state)

    # Run multiple turns with high-heat content
    for _ in range(5):
        with patch("core.llm_client.chat", _make_fake_llm("（靠近她）心跳加速，呼吸")), \
             patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
            from core.dream import dream_pipeline
            asyncio.run(dream_pipeline.dream_turn(_UID, "热，颤抖，想你，靠近"))

        state = read_state(_UID)
        tension = float(state.get("emotional_tension") or 0.0)
        assert tension <= 1.0, f"yexuan_tension exceeded 1.0: {tension}"


def test_yexuan_tension_decays_without_body_signal():
    """无强烈 body 信号时 yexuan_tension 向 0 衰减。"""
    from core.dream.body_state import BodyState
    from core.dream.body_projection import project_body_for_yexuan, _YEXUAN_TENSION_DECAY

    body = BodyState(heat=5.0, sensitivity=5.0, tension=10.0)
    initial_tension = 0.4
    result = project_body_for_yexuan(body, "body_perceptible", initial_tension)
    new_tension = result["yexuan_tension"]

    assert new_tension < initial_tension, (
        f"tension should decay with weak signal, got {new_tension} >= {initial_tension}"
    )
    assert new_tension >= initial_tension - _YEXUAN_TENSION_DECAY - 0.001


# ═══════════════════════════════════════════════════════════════════════════════
# D-layer 结构完整性
# ═══════════════════════════════════════════════════════════════════════════════

def test_d_layer_order_in_system_prompt(real_dream_worlds):
    """D0-D8 在 system content 中严格保持顺序。"""
    from core.dream.dream_prompt import build_dream_prompt

    char = MagicMock()
    char.name = "Companion"
    char.description = "角色描述"
    char.jailbreak_entries = ["jailbreak test line"]

    snapshot = {
        "created_at": time.time(), "user_id": _UID,
        "yexuan_awareness": "lucid_shared", "boundary": "dream_only",
        "entry_reason": "test layers", "relationship_state": {},
        "recent_reality_context": "最近对话", "profile_impression": "印象",
        "episodic_summary": "", "mid_term_context": "",
    }
    local_state = {
        "emotional_tension": 0.0, "scene_state": "光与影",
        "symbolic_anchors": ["光"], "body_state": {},
    }

    msgs = build_dream_prompt(
        character=char,
        user_id=_UID,
        user_message="test",
        context_snapshot=snapshot,
        dream_history=[],
        local_state=local_state,
        jailbreak_text="jailbreak text",
        body_projection_text="【她·身体读数·定性】温度：微热",
        yexuan_tension=0.3,
    )

    system = msgs[0]["content"]
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "test"

    layer_markers = ["D0·破限", "D1·身份核心", "D2·今晚梦的世界规则",
                     "D3·梦境示例", "D4·入梦前背景", "D5·她的身体感知",
                     "D7·", "D8·梦境导演注记"]
    positions = {}
    for marker in layer_markers:
        idx = system.find(marker)
        assert idx >= 0, f"Layer marker '{marker}' not found in system prompt"
        positions[marker] = idx

    sorted_markers = sorted(positions, key=lambda k: positions[k])
    assert sorted_markers == layer_markers, (
        f"Layer order wrong: {sorted_markers}"
    )


def test_d5_empty_when_no_body_state():
    """body_projection_text 为空时，system prompt 中不含 D5 层。"""
    from core.dream.dream_prompt import build_dream_prompt

    snapshot = {
        "created_at": time.time(), "user_id": _UID,
        "yexuan_awareness": "lucid_shared", "boundary": "dream_only",
        "entry_reason": "test", "relationship_state": {},
        "recent_reality_context": "", "episodic_summary": "",
        "mid_term_context": "", "profile_impression": "",
    }
    local_state = {"emotional_tension": 0.0, "scene_state": None, "symbolic_anchors": [], "body_state": {}}

    msgs = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="hello",
        context_snapshot=snapshot,
        dream_history=[],
        local_state=local_state,
        body_projection_text="",  # empty
        yexuan_tension=0.0,
    )
    system = msgs[0]["content"]
    assert "D5·她的身体感知" not in system, "D5 should not appear when body_projection_text is empty"


def test_d7_empty_when_tension_near_zero():
    """yexuan_tension ≈ 0 时 D7 不出现在 system prompt。"""
    from core.dream.dream_prompt import build_dream_prompt

    snapshot = {
        "created_at": time.time(), "user_id": _UID,
        "yexuan_awareness": "lucid_shared", "boundary": "dream_only",
        "entry_reason": "test", "relationship_state": {},
        "recent_reality_context": "", "episodic_summary": "",
        "mid_term_context": "", "profile_impression": "",
    }
    local_state = {"emotional_tension": 0.0, "scene_state": None, "symbolic_anchors": [], "body_state": {}}

    msgs = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="hello",
        context_snapshot=snapshot,
        dream_history=[],
        local_state=local_state,
        body_projection_text="",
        yexuan_tension=0.0,
    )
    system = msgs[0]["content"]
    assert "D7·" not in system, "D7 should not appear when tension ≈ 0"


# ═══════════════════════════════════════════════════════════════════════════════
# body_tracker 单元测试
# ═══════════════════════════════════════════════════════════════════════════════

def test_body_tracker_increases_heat_on_trigger_words():
    """触发词命中 → heat 上升。"""
    from core.dream.body_state import BodyState
    from core.dream.body_tracker import analyze_turn

    before = BodyState(heat=10.0, sensitivity=10.0, tension=10.0)
    after = analyze_turn("想你，靠近我", "（靠近她）心跳", before)
    assert after.heat > before.heat, "heat should increase with trigger words"


def test_body_tracker_delta_capped_per_turn():
    """单轮最大增量被 _MAX_DELTA 限制。"""
    from core.dream.body_state import BodyState
    from core.dream.body_tracker import analyze_turn, _MAX_DELTA

    before = BodyState()
    # Max possible signal: all her + yx signal groups fire
    mega_msg = "想你靠近贴着抱住触碰不想离开热烫心跳颤抖发抖喘害怕紧张不安慌好嗯继续再一次还要"
    mega_reply = "（靠近她）（拉住她）（握住她）（抱住她）（把她）（轻轻）（慢慢）心跳沉默了靠得更近"
    after = analyze_turn(mega_msg, mega_reply, before)

    delta_h = after.heat - before.heat
    delta_s = after.sensitivity - before.sensitivity
    delta_t = after.tension - before.tension

    assert abs(delta_h) <= _MAX_DELTA, f"heat delta {delta_h} exceeds _MAX_DELTA {_MAX_DELTA}"
    assert abs(delta_s) <= _MAX_DELTA, f"sensitivity delta {delta_s} exceeds _MAX_DELTA {_MAX_DELTA}"
    assert abs(delta_t) <= _MAX_DELTA, f"tension delta {delta_t} exceeds _MAX_DELTA {_MAX_DELTA}"


def test_body_tracker_stays_within_caps():
    """body_tracker 结果始终 ≤ cap，不越界。"""
    from core.dream.body_state import BodyState
    from core.dream.body_tracker import analyze_turn

    near_max = BodyState(heat=78.0, sensitivity=79.0, tension=88.0)
    after = analyze_turn("想你心跳颤抖", "（靠近她）心跳", near_max)

    assert after.heat <= near_max.heat_cap
    assert after.sensitivity <= near_max.sensitivity_cap
    assert after.tension <= near_max.tension_cap


def test_body_tracker_does_not_go_below_zero():
    """抑制词不让 axes 低于 0。"""
    from core.dream.body_state import BodyState
    from core.dream.body_tracker import analyze_turn

    zero_body = BodyState(heat=0.0, sensitivity=0.0, tension=0.0)
    after = analyze_turn("困，安静，平静，轻柔，放开，走开，停下，不要，别碰",
                         "（拉开距离）（后退）（松开）", zero_body)
    assert after.heat >= 0.0
    assert after.sensitivity >= 0.0
    assert after.tension >= 0.0
