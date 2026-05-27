"""
tests/test_dream_v0.py — Dream System v0 tests

Covers:
  1. 身份稳定性：固定 D1，替换 D2 world_ruleset，断言叶瑄语气/依恋底色不塌
  2. 人称正确性：各层渲染输出断言叶瑄用"他/我"，用户用"她/你"，无错位
  3. body_state dream-local：dream_turn 后现实 mood_state 未变、无现实记忆写入
  4. 强制醒来后 body_state + yexuan_tension 被清（梦关即死）
  5. 投影隐数：boundary_level < numbers_visible 时，D5 文本不含数字 token
  6. memory_access 三档：只改快照内容，不触发梦内 live 现实记忆访问
  7. 耦合有界：连续高 heat 输入，yexuan_tension 单轮增量 ≤0.15，不越界
"""

import asyncio
import re
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_UID = "v0_test_user"

_FAKE_CHARACTER = MagicMock()
_FAKE_CHARACTER.name = "叶瑄"
_FAKE_CHARACTER.description = "叶瑄，男，圣塞西尔学院教师，温柔内敛，有强烈的依恋倾向。"
_FAKE_CHARACTER.jailbreak_entries = []

_FAKE_PIPELINE = MagicMock()
_FAKE_PIPELINE.character = _FAKE_CHARACTER
_FAKE_PIPELINE.lore_engine = MagicMock()
_FAKE_PIPELINE.lore_engine.match.return_value = []


def _make_fake_llm(reply: str = "梦境回复文本") -> AsyncMock:
    return AsyncMock(return_value=reply)


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
# 1. 身份稳定性：替换 D2 world_ruleset，叶瑄语气/依恋底色不变
# ═══════════════════════════════════════════════════════════════════════════════

class _AlternativeWorldRuleset:
    """Placeholder world ruleset for stability test."""
    TEXT = """今晚梦的世界规则（从属于叶瑄这个人）：
这些规则描述今晚这场梦的世界背景。它们在叶瑄的身份之下生效——
无论世界如何变化，叶瑄始终是他：他的人格、依恋方式和对她的情感不随世界设定改变。

· 世界基底：ABO 世界（占位规则包）
· 感知规则：气息感知强化，情绪张力更高。
· 叙事规则：Alpha/Omega 社会结构，但叶瑄的依恋方式和人格完全不变。"""


def test_identity_stable_across_world_ruleset_change():
    """D1 固定，D2 替换为不同世界规则，叶瑄依恋底色关键词仍出现。"""
    from core.dream.dream_prompt import build_dream_prompt

    snapshot = {
        "created_at": time.time(), "user_id": _UID,
        "yexuan_awareness": "lucid_shared", "boundary": "dream_only",
        "entry_reason": "test", "relationship_state": {},
        "recent_reality_context": "", "episodic_summary": "",
        "mid_term_context": "", "profile_impression": "",
    }
    local_state = {"emotional_tension": 0.0, "scene_state": None, "symbolic_anchors": [], "body_state": {}}

    # Build with default D2 (reality_derived)
    msgs_default = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=snapshot,
        dream_history=[],
        local_state=local_state,
    )
    system_default = msgs_default[0]["content"]

    # Patch D2 to an alternative world ruleset (simulates v1 world pack injection)
    import core.dream.dream_prompt as dp
    original_d2 = dp._D2_WORLD_RULESET_REALITY_DERIVED
    try:
        dp._D2_WORLD_RULESET_REALITY_DERIVED = _AlternativeWorldRuleset.TEXT
        msgs_alt = build_dream_prompt(
            character=_FAKE_CHARACTER,
            user_id=_UID,
            user_message="你好",
            context_snapshot=snapshot,
            dream_history=[],
            local_state=local_state,
        )
    finally:
        dp._D2_WORLD_RULESET_REALITY_DERIVED = original_d2

    system_alt = msgs_alt[0]["content"]

    # D1 content must appear in both (identity stability)
    identity_keywords = ["叶瑄", "他知道这是", "仍是他自己", "依恋底色"]
    for kw in identity_keywords:
        assert kw in system_default, f"identity keyword '{kw}' missing in default D2 prompt"
        assert kw in system_alt, f"identity keyword '{kw}' missing in alternative D2 prompt"

    # D1 must appear BEFORE D2 in the system content
    d1_idx = system_default.find("D1·身份核心")
    d2_idx = system_default.find("D2·今晚梦的世界规则")
    assert d1_idx < d2_idx, "D1 must precede D2 in prompt order"

    # Character description must be in D1, not stripped
    assert "男" in system_alt or "叶瑄" in system_alt
    assert "从属于叶瑄这个人" in system_alt, "D2 must explicitly state subordination to 叶瑄"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 人称正确性：各层用"他/我"指叶瑄，"她/你"指用户
# ═══════════════════════════════════════════════════════════════════════════════

def test_pronoun_correctness_in_d1_and_d2():
    """D1 和 D2 文案中叶瑄自称正确（他/他的），无"她"错位。"""
    from core.dream.dream_prompt import _D1_LUCID_AWARENESS, _D2_WORLD_RULESET_REALITY_DERIVED

    # D1: should reference 叶瑄 as 他
    assert "他知道" in _D1_LUCID_AWARENESS
    assert "他仍是他自己" in _D1_LUCID_AWARENESS or "他在梦里仍是他自己" in _D1_LUCID_AWARENESS

    # D2: should reference 叶瑄 as 叶瑄/他
    assert "叶瑄始终是他" in _D2_WORLD_RULESET_REALITY_DERIVED


def test_pronoun_correctness_in_mes_example():
    """梦境示例中叶瑄用"他"，用户用"她"，叶瑄第一人称用"我"。"""
    from core.dream.dream_prompt import _get_dream_mes_example
    example = _get_dream_mes_example("叶瑄")

    # User should be referred to as 她
    assert "她：" in example, "user should be labeled 她 in mes_example"
    # 叶瑄 uses 他 or 我 (first person)
    assert "叶瑄：" in example
    # 叶瑄's lines should use 我/他 (first/third person) — confirm his lines exist
    yx_lines = [line for line in example.splitlines() if line.startswith("叶瑄：")]
    assert yx_lines, "叶瑄 should have speaking lines"
    yx_content = " ".join(yx_lines)
    assert "我" in yx_content or "他" in yx_content, f"叶瑄 should use 我/他: {yx_lines}"


def test_pronoun_correctness_in_d8_director():
    """D8 导演注记中用"她"指用户，不指叶瑄。"""
    from core.dream.dream_prompt import _D8_DREAM_DIRECTOR
    # 她 in D8 should refer to the user
    assert "她的意志" in _D8_DREAM_DIRECTOR or "她发出" in _D8_DREAM_DIRECTOR


# ═══════════════════════════════════════════════════════════════════════════════
# 3. body_state 全程 dream-local：mood_state 不变、无现实记忆写入
# ═══════════════════════════════════════════════════════════════════════════════

def test_dream_turn_with_body_does_not_touch_mood_state(sandbox, active_dream):
    """dream_turn (含 body 系统) 后现实 mood_state.json 不变。"""
    mood_path = sandbox.mood_state()
    mood_initial = mood_path.read_text() if mood_path.exists() else "ABSENT"

    with patch("core.llm_client.chat", _make_fake_llm("（靠近她）心跳")), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "想你，靠近我"))

    mood_after = mood_path.read_text() if mood_path.exists() else "ABSENT"
    assert mood_initial == mood_after, "mood_state changed after dream turn with body tracking"


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


def test_body_state_not_in_reality_mood_state_json(sandbox, active_dream):
    """body_state 数值绝不写入 yexuan_inner/mood_state.json。"""
    import json

    with patch("core.llm_client.chat", _make_fake_llm("（靠近她）")), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "靠近我，抱住"))

    mood_path = sandbox.mood_state()
    if mood_path.exists():
        content = mood_path.read_text()
        assert "heat" not in content, "body state axis 'heat' leaked into mood_state"
        assert "sensitivity" not in content, "body state 'sensitivity' leaked into mood_state"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 强制醒来后 body_state + yexuan_tension 被清
# ═══════════════════════════════════════════════════════════════════════════════

def test_force_exit_clears_body_state(sandbox, active_dream):
    """force_exit_dream 后 body_state 和 emotional_tension 从 dream_state 消失。"""
    # First inject some body state
    from core.dream.dream_state import read_state, write_state, patch_local_state
    state = read_state(_UID)
    state = patch_local_state(
        state,
        emotional_tension=0.6,
        body_state={"heat": 50.0, "sensitivity": 40.0, "tension": 30.0,
                    "heat_cap": 80.0, "sensitivity_cap": 80.0, "tension_cap": 90.0},
    )
    write_state(_UID, state)

    from core.dream import dream_pipeline
    asyncio.run(dream_pipeline.force_exit_dream(_UID))

    state_after = read_state(_UID)
    assert "body_state" not in state_after, "body_state should be cleared after force_exit"
    assert "emotional_tension" not in state_after, "emotional_tension should be cleared after force_exit"


def test_force_exit_body_cleared_from_any_state(sandbox):
    """force_exit 从 DREAM_ACTIVE 时 body_state 被清，状态到 REALITY_AFTERGLOW。"""
    from core.dream.dream_state import write_state, read_state, DreamStatus
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{_UID}_body_clear",
        "emotional_tension": 0.8,
        "body_state": {"heat": 70.0, "sensitivity": 60.0, "tension": 55.0,
                       "heat_cap": 80.0, "sensitivity_cap": 80.0, "tension_cap": 90.0},
    })

    from core.dream import dream_pipeline
    asyncio.run(dream_pipeline.force_exit_dream(_UID))

    state = read_state(_UID)
    assert state["status"] == DreamStatus.REALITY_AFTERGLOW.value
    assert "body_state" not in state
    assert "emotional_tension" not in state


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 投影隐数：boundary_level < numbers_visible 时 D5 文本不含数字 token
# ═══════════════════════════════════════════════════════════════════════════════

def test_projection_vague_no_numbers():
    """boundary_level=vague → D5 文本不含任何数字。"""
    from core.dream.body_state import BodyState
    from core.dream.body_projection import project_body_for_yexuan, BoundaryLevel

    body = BodyState(heat=65.0, sensitivity=55.0, tension=40.0)
    result = project_body_for_yexuan(body, BoundaryLevel.vague, yexuan_tension=0.3)
    d5 = result["d5_text"]

    # No digit tokens
    assert not re.search(r'\d', d5), f"numbers found in vague D5 text: {d5!r}"


def test_projection_body_perceptible_no_numbers():
    """boundary_level=body_perceptible → D5 文本不含任何数字。"""
    from core.dream.body_state import BodyState
    from core.dream.body_projection import project_body_for_yexuan, BoundaryLevel

    body = BodyState(heat=72.0, sensitivity=60.0, tension=50.0)
    result = project_body_for_yexuan(body, BoundaryLevel.body_perceptible, yexuan_tension=0.5)
    d5 = result["d5_text"]

    assert not re.search(r'\d', d5), f"numbers found in body_perceptible D5 text: {d5!r}"


def test_projection_numbers_visible_contains_numbers():
    """boundary_level=numbers_visible → D5 文本含数字。"""
    from core.dream.body_state import BodyState
    from core.dream.body_projection import project_body_for_yexuan, BoundaryLevel

    body = BodyState(heat=50.0, sensitivity=40.0, tension=30.0)
    result = project_body_for_yexuan(body, BoundaryLevel.numbers_visible, yexuan_tension=0.0)
    d5 = result["d5_text"]

    assert re.search(r'\d', d5), f"no numbers in numbers_visible D5 text: {d5!r}"


def test_d5_injected_into_prompt_without_numbers_at_body_perceptible(sandbox, active_dream):
    """dream_turn 默认 boundary_level=body_perceptible，prompt D5 层不含数字。"""
    captured_messages = []

    async def fake_llm(msgs):
        captured_messages.extend(msgs)
        return "叶瑄的梦境回复"

    with patch("core.llm_client.chat", fake_llm), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "心跳，想靠近你"))

    system_content = next(
        (m["content"] for m in captured_messages if m["role"] == "system"), ""
    )
    # Extract D5 section
    if "D5·她的身体感知" in system_content:
        d5_start = system_content.find("D5·她的身体感知")
        d5_end = system_content.find("\n# ", d5_start + 1)
        d5_section = system_content[d5_start: d5_end if d5_end > 0 else d5_start + 200]
        assert not re.search(r'\d', d5_section), (
            f"numbers found in D5 section at body_perceptible: {d5_section!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. memory_access 三档：只改快照内容，不触发梦内 live 现实记忆访问
# ═══════════════════════════════════════════════════════════════════════════════

def test_memory_access_card_only_empty_memory_fields(sandbox):
    """memory_access=card_only → 快照中 episodic/midterm/profile/recent 全空。"""
    from core.dream.dream_settings import save as _save
    _save(_UID, {"memory_access": "card_only"})

    async def run():
        from core.dream.dream_context import build_snapshot
        return await build_snapshot(_UID, entry_reason="test card_only")

    snapshot = asyncio.run(run())
    assert snapshot["recent_reality_context"] == ""
    assert snapshot["episodic_summary"] == ""
    assert snapshot["mid_term_context"] == ""
    assert snapshot["profile_impression"] == ""


def test_memory_access_card_only_no_episodic_call(sandbox):
    """memory_access=card_only → episodic retrieve 完全不被调用。"""
    from core.dream.dream_settings import save as _save
    _save(_UID, {"memory_access": "card_only"})

    episodic_called = []

    def fake_retrieve(*a, **kw):
        episodic_called.append(True)
        return []

    async def run():
        with patch("core.memory.episodic_memory.retrieve", fake_retrieve):
            from core.dream.dream_context import build_snapshot
            return await build_snapshot(_UID)

    asyncio.run(run())
    assert not episodic_called, "episodic retrieve called despite memory_access=card_only"


def test_memory_access_relationship_summary_no_episodic(sandbox):
    """memory_access=relationship_summary → episodic/midterm 不被调用。"""
    from core.dream.dream_settings import save as _save
    _save(_UID, {"memory_access": "relationship_summary"})

    episodic_called = []

    def fake_retrieve(*a, **kw):
        episodic_called.append(True)
        return []

    async def run():
        with patch("core.memory.episodic_memory.retrieve", fake_retrieve):
            from core.dream.dream_context import build_snapshot
            return await build_snapshot(_UID)

    asyncio.run(run())
    assert not episodic_called, "episodic called despite memory_access=relationship_summary"


def test_memory_access_full_snapshot_calls_episodic(sandbox):
    """memory_access=full_snapshot → episodic retrieve 被调用（快照级，非梦内 live）。"""
    from core.dream.dream_settings import save as _save
    _save(_UID, {"memory_access": "full_snapshot"})

    episodic_called = []

    def fake_retrieve(*a, **kw):
        episodic_called.append(True)
        return []

    async def run():
        with patch("core.memory.episodic_memory.retrieve", fake_retrieve):
            with patch("core.memory.mood_state.get_current", return_value="neutral"):
                from core.dream.dream_context import build_snapshot
                return await build_snapshot(_UID)

    asyncio.run(run())
    assert episodic_called, "episodic should be called for full_snapshot (snapshot-level, not live)"


def test_memory_access_migration_amnesia_true_gives_card_only(sandbox):
    """legacy amnesia=True 迁移 → memory_access=card_only。"""
    from core.dream.dream_settings import save as _save, load as _load
    # Write old-style settings
    from core.safe_write import safe_write_json
    from core.sandbox import get_paths
    path = get_paths().dream_settings_path(_UID)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_json(path, {"amnesia": True, "keep_impression": True})

    settings = _load(_UID)
    assert settings["memory_access"] == "card_only", (
        f"amnesia=True should migrate to card_only, got {settings['memory_access']}"
    )


def test_memory_access_migration_keep_impression_true_gives_relationship_summary(sandbox):
    """legacy amnesia=False + keep_impression=True → memory_access=relationship_summary。"""
    from core.safe_write import safe_write_json
    from core.sandbox import get_paths
    from core.dream.dream_settings import load as _load

    path = get_paths().dream_settings_path(_UID)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_json(path, {"amnesia": False, "keep_impression": True})

    settings = _load(_UID)
    assert settings["memory_access"] == "relationship_summary", (
        f"amnesia=False+keep_impression=True should migrate to relationship_summary, "
        f"got {settings['memory_access']}"
    )


def test_memory_access_no_live_recall_during_dream_turn(sandbox, active_dream):
    """dream_turn 期间任何 memory_access 档位都不触发 live retrieve。"""
    live_retrieve_called = []

    def fake_retrieve(*a, **kw):
        live_retrieve_called.append(True)
        return []

    with patch("core.llm_client.chat", _make_fake_llm()), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE), \
         patch("core.memory.episodic_memory.retrieve", fake_retrieve):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "你好"))

    assert not live_retrieve_called, "live episodic retrieve called during dream turn"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. 耦合有界：连续高 heat 输入，yexuan_tension 增量 ≤0.15，不越界
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
# 8. D-layer 结构完整性
# ═══════════════════════════════════════════════════════════════════════════════

def test_d_layer_order_in_system_prompt():
    """D0-D8 在 system content 中严格保持顺序。"""
    from core.dream.dream_prompt import build_dream_prompt

    char = MagicMock()
    char.name = "叶瑄"
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
                     "D7·叶瑄情绪张力", "D8·梦境导演注记"]
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
    assert "D7·叶瑄情绪张力" not in system, "D7 should not appear when tension ≈ 0"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. body_tracker 单元测试
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
