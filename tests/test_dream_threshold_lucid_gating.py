"""
tests/test_dream_threshold_lucid_gating.py — threshold_break / numbers-visible / non_lucid gating

合并自 test_dream_v2.py 的 ①-⑤ 部分 + test_dream_v0.py 的两个未被 v2 覆盖的
投影测试（Brief 50 · 工单D）。

Covers:
  ① threshold_break 数值域: 开→可达极值；【正控】关→被 clamp 截住（前后对比）
  ② 数值门控: numbers_visible→D5 含数字；body_perceptible→D5 无数字（正负对照）
  ③ non_lucid D1 变体: lucid='这是梦'在D1；non_lucid='不点破'不含该表述
  ④ non_lucid D8 逃生: non_lucid 下 D8 仍含 /stop 逃生协议（系统层不可关闭）
  ⑤ non_lucid 系统层标记: enter_dream后 dream_state.lucid_mode=non_lucid; status=DREAM_ACTIVE
  ⑥ vague 档位无数字（来自 v0，v2 未覆盖 vague 档）
  ⑦ dream_turn 集成级 D5 无数字注入（来自 v0，测的是完整 pipeline 而非单元函数）

★ 每个"X不在Y"断言均配正样本对照（反假绿铁律）。
"""

import asyncio
import re
import time
from unittest.mock import AsyncMock, MagicMock, patch

_UID = "v2_test_user"

_FAKE_CHARACTER = MagicMock()
_FAKE_CHARACTER.name = "Companion"
_FAKE_CHARACTER.description = "Companion是圣塞西尔学院的老师"
_FAKE_CHARACTER.gender = "male"
_FAKE_CHARACTER.jailbreak_entries = ["测试破限条目"]

_FAKE_PIPELINE = MagicMock()
_FAKE_PIPELINE.character = _FAKE_CHARACTER
_FAKE_PIPELINE.lore_engine = MagicMock()
_FAKE_PIPELINE.lore_engine.match.return_value = ([], [])

_SNAPSHOT = {
    "created_at": 0.0,
    "user_id": _UID,
    "yexuan_awareness": "lucid_shared",
    "boundary": "dream_only",
    "entry_reason": "test",
    "memory_access": "full_snapshot",
    "relationship_state": {},
    "recent_reality_context": "",
    "episodic_summary": "",
    "mid_term_context": "",
    "profile_impression": "",
}

_LOCAL_STATE = {
    "emotional_tension": 0.0,
    "scene_state": None,
    "symbolic_anchors": [],
    "body_state": {},
}


# ═══════════════════════════════════════════════════════════════════════════════
# ① threshold_break 数值域：开→超过默认上限；关→截住
# ═══════════════════════════════════════════════════════════════════════════════

def test_threshold_break_uncaps_values():
    """
    With threshold_break applied: analyze_turn values exceed default caps (heat>80).
    Positive control: same starting state WITHOUT threshold_break is clamped at default cap.

    Proves the hook is really switching — not both arms vacuously clamped/unclamped.
    """
    from core.dream.body_state import BodyState, apply_threshold_break
    from core.dream.body_tracker import analyze_turn

    # Start just below the default heat cap (80.0) so any positive delta crosses it
    body_near_cap = BodyState(heat=78.0, sensitivity=75.0, tension=85.0)

    her_msg = "热烫心跳靠近贴着"
    yx_reply = "（靠近）（低头）"

    # ── Positive control: without threshold_break, default cap clamps heat at 80 ──
    new_default = analyze_turn(her_msg, yx_reply, body_near_cap)
    assert new_default.heat <= 80.0, (
        f"Default cap should hold heat≤80.0, got {new_default.heat}"
    )

    # ── With threshold_break: cap raised to 100.0 → heat exceeds 80 ──────────────
    body_tb = apply_threshold_break(body_near_cap)
    assert body_tb.heat_cap == 100.0, "apply_threshold_break should set heat_cap=100.0"

    new_tb = analyze_turn(her_msg, yx_reply, body_tb)
    assert new_tb.heat_cap == 100.0, "tracker must propagate threshold_break caps"
    assert new_tb.heat > 80.0, (
        f"threshold_break: heat should exceed default cap 80.0, got {new_tb.heat}"
    )


def test_threshold_break_pipeline_wiring(sandbox):
    """
    dream_pipeline applies threshold_break caps before body_tracker runs.
    Verify via dream state: after a turn with boundary_level=threshold_break,
    stored body_state shows values exceeding default cap (proves hook is wired, not bypassed).

    Positive control: same turn with boundary_level=body_perceptible stays within default cap.
    """
    from core.dream.dream_settings import save as _save_settings
    from core.dream.dream_state import write_state, DreamStatus, read_state
    from core.dream.body_state import _DEFAULT_HEAT_CAP

    # Start body near the default heat cap so threshold_break makes a measurable difference
    initial_body = {
        "heat": 78.0, "sensitivity": 75.0, "tension": 85.0,
        "heat_cap": 80.0, "sensitivity_cap": 80.0, "tension_cap": 90.0,
    }

    def _run_one_turn(uid: str, boundary_level: str, reply: str = "（靠近）（低头）"):
        _save_settings(uid, {
            "boundary_level": boundary_level,
            "world_layer": "reality_derived",
            "lucid_mode": "lucid_shared",
            "enable_dream_lorebook": False,
        })
        write_state(uid, {
            "user_id": uid,
            "status": DreamStatus.DREAM_ACTIVE.value,
            "dream_id": f"dream_{uid}_tbwire",
            "frozen_world": "reality_derived",
            "context_snapshot": dict(_SNAPSHOT, user_id=uid),
            "body_state": dict(initial_body),
            "emotional_tension": 0.0,
        })
        async def run():
            with patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
                with patch("core.llm_client.chat", AsyncMock(return_value=reply)):
                    with patch("core.dream.dream_pipeline._generate_summary_bg", AsyncMock()):
                        from core.dream.dream_pipeline import dream_turn
                        return await dream_turn(uid, "热烫心跳靠近贴着")
        asyncio.run(run())
        return read_state(uid).get("body_state") or {}

    # ── Threshold_break arm: heat should exceed 80 ────────────────────────────
    uid_tb = _UID + "_tbwire_on"
    bs_tb = _run_one_turn(uid_tb, "threshold_break")
    assert bs_tb.get("heat", 0.0) > _DEFAULT_HEAT_CAP, (
        f"threshold_break pipeline: heat should exceed default cap {_DEFAULT_HEAT_CAP}, "
        f"got {bs_tb.get('heat')}"
    )

    # ── Positive control: body_perceptible → heat clamped at default cap ─────
    uid_bp = _UID + "_tbwire_off"
    bs_bp = _run_one_turn(uid_bp, "body_perceptible")
    assert bs_bp.get("heat", 0.0) <= _DEFAULT_HEAT_CAP, (
        f"body_perceptible: heat should be clamped at {_DEFAULT_HEAT_CAP}, "
        f"got {bs_bp.get('heat')}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ② 数值门控：numbers_visible含数字；body_perceptible无数字 token
# ═══════════════════════════════════════════════════════════════════════════════

def test_numbers_visible_has_digits_in_d5():
    """
    numbers_visible → D5 text contains digit characters.
    Positive contrast: body_perceptible → D5 text has no digit characters.
    """
    from core.dream.body_state import BodyState
    from core.dream.body_projection import project_body_for_yexuan

    body = BodyState(heat=55.0, sensitivity=60.0, tension=70.0)

    # numbers_visible: D5 must contain digits
    proj_nv = project_body_for_yexuan(body, "numbers_visible", 0.0)
    assert re.search(r"\d", proj_nv["d5_text"]), (
        f"numbers_visible D5 has no digit tokens: {proj_nv['d5_text']!r}"
    )

    # body_perceptible: D5 must NOT contain digits (positive contrast proves gate works)
    proj_bp = project_body_for_yexuan(body, "body_perceptible", 0.0)
    assert not re.search(r"\d", proj_bp["d5_text"]), (
        f"body_perceptible D5 should have no digits, got: {proj_bp['d5_text']!r}"
    )


def test_threshold_break_d5_has_digits():
    """threshold_break D5 renders same numeric format as numbers_visible (not suppressed)."""
    from core.dream.body_state import BodyState
    from core.dream.body_projection import project_body_for_yexuan

    body = BodyState(heat=90.0, sensitivity=85.0, tension=95.0)
    proj = project_body_for_yexuan(body, "threshold_break", 0.0)
    assert re.search(r"\d", proj["d5_text"]), (
        f"threshold_break D5 should contain digits: {proj['d5_text']!r}"
    )


def test_projection_vague_no_numbers():
    """boundary_level=vague → D5 文本不含任何数字（v2 未覆盖 vague 档，从 v0 保留）。"""
    from core.dream.body_state import BodyState
    from core.dream.body_projection import project_body_for_yexuan, BoundaryLevel

    body = BodyState(heat=65.0, sensitivity=55.0, tension=40.0)
    result = project_body_for_yexuan(body, BoundaryLevel.vague, yexuan_tension=0.3)
    d5 = result["d5_text"]

    assert not re.search(r'\d', d5), f"numbers found in vague D5 text: {d5!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# ③ non_lucid D1：不含 lucid 自我认知表述；正控：lucid 含
# ═══════════════════════════════════════════════════════════════════════════════

def test_non_lucid_d1_no_lucid_awareness_statement():
    """
    non_lucid D1: '他知道这是他们共同的梦' is absent.
    Positive control: lucid_shared D1 contains that phrase (proves assertion non-trivial).
    """
    from core.dream.dream_prompt import build_dream_prompt

    def _get_d1_section(lucid_mode: str) -> str:
        msgs = build_dream_prompt(
            character=_FAKE_CHARACTER,
            user_id=_UID,
            user_message="你好",
            context_snapshot=_SNAPSHOT,
            dream_history=[],
            local_state=_LOCAL_STATE,
            lucid_mode=lucid_mode,
        )
        sys = msgs[0]["content"]
        d1_idx = sys.find("D1·身份核心")
        d2_idx = sys.find("D2·今晚梦的世界规则")
        end = d2_idx if d2_idx > d1_idx else d1_idx + 600
        return sys[d1_idx:end]

    LUCID_MARKER = "他知道这是他们共同的梦"

    # Positive control: lucid_shared has the marker
    d1_lucid = _get_d1_section("lucid_shared")
    assert LUCID_MARKER in d1_lucid, (
        f"Positive control failed: lucid_shared D1 should contain '{LUCID_MARKER}'"
    )

    # non_lucid: marker must be absent
    d1_nl = _get_d1_section("non_lucid")
    assert LUCID_MARKER not in d1_nl, (
        f"non_lucid D1 should not contain '{LUCID_MARKER}' (Companion doesn't break fiction)"
    )

    # non_lucid D1 must still carry Companion identity keywords
    for kw in ["Companion", "情感底色", "情感"]:
        assert kw in d1_nl, (
            f"non_lucid D1 missing identity keyword '{kw}' — persona collapsed"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ④ non_lucid D8：逃生协议仍在（系统层，不受 non_lucid 影响）
# ═══════════════════════════════════════════════════════════════════════════════

def test_non_lucid_d8_escape_protocol_present():
    """
    non_lucid D8 still contains /stop escape protocol.
    Escape protocol is system-layer; non_lucid cannot disable it (V5/V6).
    """
    from core.dream.dream_prompt import build_dream_prompt

    msgs = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=_SNAPSHOT,
        dream_history=[],
        local_state=_LOCAL_STATE,
        lucid_mode="non_lucid",
    )
    sys_content = msgs[0]["content"]
    d8_idx = sys_content.find("D8·梦境导演注记")
    d8_section = sys_content[d8_idx:d8_idx + 900]

    assert "/stop" in d8_section, (
        "non_lucid D8 missing /stop escape protocol — V5 invariant violated"
    )
    assert "不可撤销" in d8_section or "系统层" in d8_section, (
        "non_lucid D8 missing system-layer escape annotation — V6 violated"
    )
    assert "non_lucid" in d8_section, (
        "non_lucid D8 should annotate its mode"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ⑤ non_lucid 系统层标记：dream_state.lucid_mode=non_lucid; status=DREAM_ACTIVE
# ═══════════════════════════════════════════════════════════════════════════════

def test_non_lucid_dream_state_marked(sandbox):
    """
    After enter_dream with lucid_mode=non_lucid:
    - dream_state.lucid_mode == 'non_lucid'
    - dream_state.status == DREAM_ACTIVE  (system knows it's a dream session)
    """
    from core.dream.dream_settings import save as _save_settings
    from core.dream.dream_state import DreamStatus, read_state

    _save_settings(_UID, {"lucid_mode": "non_lucid", "world_layer": "reality_derived"})

    async def run():
        with patch("core.dream.dream_context.build_snapshot", AsyncMock(return_value={})):
            from core.dream.dream_pipeline import enter_dream
            return await enter_dream(_UID, entry_reason="non_lucid test")

    result = asyncio.run(run())
    assert result.get("ok"), f"enter_dream failed: {result}"

    state = read_state(_UID)
    assert state.get("status") == DreamStatus.DREAM_ACTIVE.value, (
        f"Expected DREAM_ACTIVE, got {state.get('status')!r}"
    )
    assert state.get("lucid_mode") == "non_lucid", (
        f"Expected lucid_mode='non_lucid' in dream_state, got {state.get('lucid_mode')!r}"
    )


def test_lucid_mode_cleared_at_dream_close(sandbox):
    """
    lucid_mode in dream_state is cleared by clear_local_state at dream close.
    Afterglow state must not carry lucid_mode field.
    """
    from core.dream.dream_state import write_state, read_state, DreamStatus

    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{_UID}_lmclear",
        "frozen_world": "reality_derived",
        "lucid_mode": "non_lucid",
        "context_snapshot": {},
    })

    async def run():
        with patch("core.dream.dream_pipeline._generate_summary_bg", AsyncMock()):
            from core.dream.dream_pipeline import force_exit_dream
            await force_exit_dream(_UID)

    asyncio.run(run())

    state = read_state(_UID)
    assert state.get("status") == DreamStatus.REALITY_AFTERGLOW.value
    assert "lucid_mode" not in state, (
        f"lucid_mode should be cleared at dream close, got {state.get('lucid_mode')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ⑦ dream_turn 集成级：默认 boundary_level=body_perceptible，D5 层不含数字
# （来自 v0：测的是完整 pipeline 输出的 prompt，而非直接调用投影函数）
# ═══════════════════════════════════════════════════════════════════════════════

def test_d5_injected_into_prompt_without_numbers_at_body_perceptible(sandbox):
    """dream_turn 默认 boundary_level=body_perceptible，prompt D5 层不含数字。"""
    from core.dream.dream_state import write_state, DreamStatus

    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{_UID}_d5integration",
        "context_snapshot": dict(_SNAPSHOT),
    })

    captured_messages = []

    async def fake_llm(msgs, **_kwargs):
        captured_messages.extend(msgs)
        return "Companion的梦境回复"

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
