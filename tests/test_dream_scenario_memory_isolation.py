"""
tests/test_dream_scenario_memory_isolation.py — Scenario Mode 记忆/身体投影隔离守卫

从 test_dream_scenario_v0.py 拆出（Brief 50 · 工单D.2），是拆分出的 5 个文件
之一：Scenario Mode 不读用户隐性状态、不写 impression、不注入 D4.5/D5 身体投影
——这些是剧本模式与 sandbox/mirror 模式的核心隔离边界，每条守卫都配 sandbox
模式的正控（regression 对照），证明断言不是空判真。

Covers（v0.8 + v0.8.1 + v0.8.2）:
  - Scenario 退出（软退/硬退）不调用 wire_afterglow_from_summary；sandbox 会调用
  - Scenario prompt 不注入 D4.5（即使触发标签存在）；sandbox 会注入
  - Scenario 退出不调用 distill_impression；sandbox 会调用；summary 生成不受影响
  - Scenario prompt 不注入 D5 身体投影（即使 body_projection_text 非空）；sandbox 会注入
"""

from typing import Any
import asyncio
import time
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


_HS_SNAPSHOT = {
    "sensitivity": "medium",
    "touch_appetite": "low",
    "embodied_ease": "stable",
}

_TRIGGER_LOCAL_STATE = {
    "emotional_tension": 0.0,
    "scene_state": "body_intimate",
    "symbolic_anchors": ["physical_closeness"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# v0.8 — Hidden-state isolation guards (afterglow wiring)
# ═══════════════════════════════════════════════════════════════════════════════

def test_scenario_exit_skips_wire_afterglow():
    """_generate_summary_bg(dream_mode=scenario) never calls wire_afterglow_from_summary."""
    from core.dream.dream_pipeline import _generate_summary_bg

    wire_mock = MagicMock()

    with (
        patch("core.dream.dream_summary.generate_summary", new=AsyncMock()),
        patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary", wire_mock),
        patch("core.dream.distill_impression.distill_impression", new=AsyncMock()),
    ):
        asyncio.run(_generate_summary_bg(
            "u_scenario", "dream_u_001", "soft",
            char_id="yexuan", dream_mode="scenario",
        ))

    wire_mock.assert_not_called()


def test_scenario_hard_exit_also_skips_wire_afterglow():
    """Hard-exit scenario dream also skips wire_afterglow_from_summary."""
    from core.dream.dream_pipeline import _generate_summary_bg

    wire_mock = MagicMock()

    with (
        patch("core.dream.dream_summary.generate_summary", new=AsyncMock()),
        patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary", wire_mock),
        patch("core.dream.distill_impression.distill_impression", new=AsyncMock()),
    ):
        asyncio.run(_generate_summary_bg(
            "u_scenario", "dream_u_002", "hard_exit",
            char_id="yexuan", dream_mode="scenario",
        ))

    wire_mock.assert_not_called()


def test_sandbox_exit_calls_wire_afterglow():
    """_generate_summary_bg(dream_mode=sandbox) calls wire_afterglow_from_summary (regression)."""
    from core.dream.dream_pipeline import _generate_summary_bg

    wire_mock = MagicMock()

    with (
        patch("core.dream.dream_summary.generate_summary", new=AsyncMock()),
        patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary", wire_mock),
        patch("core.dream.distill_impression.distill_impression", new=AsyncMock()),
    ):
        asyncio.run(_generate_summary_bg(
            "u_sandbox", "dream_u_003", "soft",
            char_id="yexuan", dream_mode="sandbox",
        ))

    wire_mock.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# v0.8 — D4.5 body-cue-snapshot injection guard
# ═══════════════════════════════════════════════════════════════════════════════

def test_scenario_prompt_excludes_d45_with_body_intimate_tag():
    """Scenario prompt must not inject D4.5 even when body_intimate tag is present."""
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    scenario_core = _make_scenario_core()
    snapshot = dict(_EMPTY_SNAPSHOT)
    snapshot["user_hidden_state_snapshot"] = dict(_HS_SNAPSHOT)

    messages = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=snapshot,
        dream_history=[],
        local_state=dict(_TRIGGER_LOCAL_STATE),
        dream_mode="scenario",
        scenario_core=scenario_core,
    )
    system = dump_dream_prompt(messages)

    assert "D4.5" not in system
    assert "user_hidden_state_snapshot" not in system
    assert "sensitivity:" not in system
    assert "touch_appetite:" not in system
    assert "embodied_ease:" not in system


def test_scenario_prompt_excludes_d45_with_physical_closeness_tag():
    """Scenario prompt must not inject D4.5 when physical_closeness anchor is present."""
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    scenario_core = _make_scenario_core()
    snapshot = dict(_EMPTY_SNAPSHOT)
    snapshot["user_hidden_state_snapshot"] = dict(_HS_SNAPSHOT)
    local_with_anchor = {
        "emotional_tension": 0.0,
        "symbolic_anchors": ["physical_closeness"],
    }

    messages = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=snapshot,
        dream_history=[],
        local_state=local_with_anchor,
        dream_mode="scenario",
        scenario_core=scenario_core,
    )
    system = dump_dream_prompt(messages)

    assert "D4.5" not in system
    assert "user_hidden_state_snapshot" not in system


def test_sandbox_prompt_injects_d45_with_body_intimate_tag():
    """Sandbox dream mode injects D4.5 when body_intimate tag is present (regression)."""
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    snapshot = dict(_EMPTY_SNAPSHOT)
    snapshot["user_hidden_state_snapshot"] = dict(_HS_SNAPSHOT)

    messages = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=snapshot,
        dream_history=[],
        local_state=dict(_TRIGGER_LOCAL_STATE),
        dream_mode="sandbox",
        scenario_core=None,
    )
    system = dump_dream_prompt(messages)

    assert "D4.5" in system
    assert "user_hidden_state_snapshot" in system


# ═══════════════════════════════════════════════════════════════════════════════
# v0.8.1 — Impression isolation guards
# ═══════════════════════════════════════════════════════════════════════════════

def test_scenario_exit_skips_distill_impression():
    """_generate_summary_bg(dream_mode=scenario) never calls distill_impression."""
    from core.dream.dream_pipeline import _generate_summary_bg

    distill_mock = AsyncMock()

    with (
        patch("core.dream.dream_summary.generate_summary", new=AsyncMock()),
        patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary"),
        patch("core.dream.distill_impression.distill_impression", distill_mock),
    ):
        asyncio.run(_generate_summary_bg(
            "u_scenario_h", "dream_u_h001", "soft",
            char_id="yexuan", dream_mode="scenario",
        ))

    distill_mock.assert_not_called()


def test_scenario_hard_exit_also_skips_distill_impression():
    """Hard-exit scenario dream also skips distill_impression."""
    from core.dream.dream_pipeline import _generate_summary_bg

    distill_mock = AsyncMock()

    with (
        patch("core.dream.dream_summary.generate_summary", new=AsyncMock()),
        patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary"),
        patch("core.dream.distill_impression.distill_impression", distill_mock),
    ):
        asyncio.run(_generate_summary_bg(
            "u_scenario_i", "dream_u_i001", "hard_exit",
            char_id="yexuan", dream_mode="scenario",
        ))

    distill_mock.assert_not_called()


def test_sandbox_exit_calls_distill_impression():
    """_generate_summary_bg(dream_mode=sandbox) calls distill_impression (regression)."""
    from core.dream.dream_pipeline import _generate_summary_bg

    distill_mock = AsyncMock()

    with (
        patch("core.dream.dream_summary.generate_summary", new=AsyncMock()),
        patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary"),
        patch("core.dream.distill_impression.distill_impression", distill_mock),
    ):
        asyncio.run(_generate_summary_bg(
            "u_sandbox_j", "dream_u_j001", "soft",
            char_id="yexuan", dream_mode="sandbox",
        ))

    distill_mock.assert_called_once()


def test_scenario_summary_generation_not_blocked():
    """generate_summary still runs for scenario mode; only distill_impression is skipped."""
    from core.dream.dream_pipeline import _generate_summary_bg

    summary_mock = AsyncMock()
    distill_mock = AsyncMock()

    with (
        patch("core.dream.dream_summary.generate_summary", summary_mock),
        patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary"),
        patch("core.dream.distill_impression.distill_impression", distill_mock),
    ):
        asyncio.run(_generate_summary_bg(
            "u_scenario_k", "dream_u_k001", "soft",
            char_id="yexuan", dream_mode="scenario",
        ))

    summary_mock.assert_called_once()
    distill_mock.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# v0.8.2 — D5 body_projection isolation guard
# Body/intimate expression in Scenario is driven by script stage text, not the
# general Dream body_state system.
# ═══════════════════════════════════════════════════════════════════════════════

_BODY_PROJECTION_TEXT = "她的心跳加快，皮肤微微发热，意识到他站得很近。"


def test_scenario_prompt_excludes_d5_body_projection():
    """Scenario prompt must not inject D5 even when body_projection_text is non-empty."""
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
        body_projection_text=_BODY_PROJECTION_TEXT,
    )
    system = dump_dream_prompt(messages)

    assert "D5" not in system
    assert "D5·她的身体感知" not in system
    assert _BODY_PROJECTION_TEXT not in system


def test_sandbox_prompt_injects_d5_body_projection():
    """Sandbox dream mode injects D5 when body_projection_text is non-empty (regression)."""
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
        body_projection_text=_BODY_PROJECTION_TEXT,
    )
    system = dump_dream_prompt(messages)

    assert "D5·她的身体感知" in system
    assert _BODY_PROJECTION_TEXT in system


def test_scenario_with_nonempty_body_state_still_excludes_d5():
    """Scenario must exclude D5 even when body_projection_text is substantive.

    Guards against a false pass caused by an empty projection string rather than
    the scenario mode guard.
    """
    from core.dream.dream_prompt import build_dream_prompt, dump_dream_prompt

    scenario_core = _make_scenario_core()
    long_projection = (
        "她呼吸浅而急促，身体对他的靠近产生了明显的反应——热度从皮肤下涌上来，"
        "指尖有些发颤，不得不攥住了什么来稳住自己。"
    )
    assert long_projection  # sanity: projection is non-empty

    messages = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=_EMPTY_SNAPSHOT,
        dream_history=[],
        local_state={},
        dream_mode="scenario",
        scenario_core=scenario_core,
        body_projection_text=long_projection,
    )
    system = dump_dream_prompt(messages)

    assert "D5" not in system
    assert long_projection not in system
    # DS layer still present (scenario core is intact)
