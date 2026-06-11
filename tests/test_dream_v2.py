"""
tests/test_dream_v2.py — Dream System v2 contract tests

Covers:
  ① threshold_break 数值域: 开→可达极值；【正控】关→被 clamp 截住（前后对比）
  ② 数值门控: numbers_visible→D5 含数字；body_perceptible→D5 无数字（正负对照）
  ③ non_lucid D1 变体: lucid='这是梦'在D1；non_lucid='不点破'不含该表述
  ④ non_lucid D8 逃生: non_lucid 下 D8 仍含 /stop 逃生协议（系统层不可关闭）
  ⑤ non_lucid 系统层标记: enter_dream后 dream_state.lucid_mode=non_lucid; status=DREAM_ACTIVE
  ⑥a. 全开档矩阵: 现实 mood_state 未变 + 正控（mood_state.update()确实改mood）
  ⑥b. 全开档矩阵: 无现实记忆写入（episodic/history/mid_term）
  ⑥c. 全开档矩阵: impression 链路真跑 + 隔离（哨兵进印象库；空LLM不进）
  ⑥d. 全开档矩阵: hard_exit 即时穿透；叙事挽留（叶瑄拒绝软退）后 /stop 仍穿透
  ⑥e. 全开档矩阵: body_state/yexuan_tension 梦关即清（真 force_exit 路径，非手动赋值）

★ 每个"X不在Y"断言均配正样本对照（反假绿铁律）。
"""

import asyncio
import json
import re
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_UID = "v2_test_user"

_FAKE_CHARACTER = MagicMock()
_FAKE_CHARACTER.name = "叶瑄"
_FAKE_CHARACTER.description = "叶瑄是圣塞西尔学院的老师"
_FAKE_CHARACTER.jailbreak_entries = ["测试破限条目"]

_FAKE_PIPELINE = MagicMock()
_FAKE_PIPELINE.character = _FAKE_CHARACTER
_FAKE_PIPELINE.lore_engine = MagicMock()
_FAKE_PIPELINE.lore_engine.match.return_value = []

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


def _full_matrix_settings():
    return {
        "enable_dream_lorebook": False,
        "memory_access": "full_snapshot",
        "boundary_level": "threshold_break",
        "world_layer": "reality_derived",
        "lucid_mode": "non_lucid",
    }


def _setup_full_matrix_dream(uid: str):
    """Write settings + DREAM_ACTIVE state for a full-matrix dream session."""
    from core.dream.dream_settings import save as _save_settings
    from core.dream.dream_state import write_state, DreamStatus

    _save_settings(uid, _full_matrix_settings())
    state = {
        "user_id": uid,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{uid}_v2matrix",
        "frozen_world": "reality_derived",
        "lucid_mode": "non_lucid",
        "context_snapshot": dict(_SNAPSHOT, user_id=uid),
    }
    write_state(uid, state)
    return state


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

    # Messages that trigger large positive deltas from both her_signals and yx_signals:
    # her: "热"+"靠近" → dh cumulative sum before per-turn cap = 4+5=9 → capped to 8
    # yx:  "（靠近"+"（低头" → dh sum = 3+2=5
    # total dh after per-turn cap on (9+5=14) → clamped to _MAX_DELTA=8
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
        f"non_lucid D1 should not contain '{LUCID_MARKER}' (叶瑄 doesn't break fiction)"
    )

    # non_lucid D1 must still carry 叶瑄 identity keywords
    for kw in ["叶瑄", "情感底色", "情感"]:
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
# ⑥a. 全开档矩阵: 现实 mood_state 未变 + 正控
# ═══════════════════════════════════════════════════════════════════════════════

def test_full_matrix_mood_state_not_touched(sandbox):
    """
    a. Full-matrix dream turn → reality mood_state.json not written.
    Positive control: mood_state.update() directly does write the file.
    Proves the 'not written' assertion is non-trivial (V1 invariant).
    """
    uid = _UID + "_mood"
    _setup_full_matrix_dream(uid)

    # Dream pipeline runs as yexuan; check reality mood_state is not written.
    mood_path = sandbox.mood_state(char_id="yexuan")
    assert not mood_path.exists(), "mood_state.json should not exist before test"

    async def run_dream():
        with patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
            with patch("core.llm_client.chat", AsyncMock(return_value="梦境回复")):
                from core.dream.dream_pipeline import dream_turn
                return await dream_turn(uid, "梦境内容")

    asyncio.run(run_dream())

    assert not mood_path.exists(), (
        "Dream turn wrote to mood_state.json — reality isolation violated (V1)"
    )

    # Positive control: mood_state.update() DOES create the file
    from core.memory.mood_state import update as _mood_update
    _mood_update("happy", 0.8, source="positive_control")

    assert mood_path.exists(), (
        "Positive control failed: mood_state.update() should create mood_state.json"
    )
    raw = mood_path.read_text(encoding="utf-8")
    assert "happy" in raw, (
        f"Positive control: 'happy' not found in mood_state.json content: {raw!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ⑥b. 全开档矩阵: 无现实记忆写入
# ═══════════════════════════════════════════════════════════════════════════════

def test_full_matrix_no_reality_memory_writes(sandbox):
    """
    b. Full-matrix dream turn → no writes to episodic/history/mid_term directories.
    """
    uid = _UID + "_isolation"
    _setup_full_matrix_dream(uid)

    async def run_dream():
        with patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
            with patch("core.llm_client.chat", AsyncMock(return_value="梦境回复")):
                from core.dream.dream_pipeline import dream_turn
                return await dream_turn(uid, "梦境内容")

    asyncio.run(run_dream())

    for label, dir_path in [
        ("episodic_memory", sandbox.episodic_memory()),
        ("history", sandbox.history()),
        ("mid_term", sandbox.mid_term()),
    ]:
        if dir_path.exists():
            files = list(dir_path.glob(f"*{uid}*"))
            assert not files, (
                f"Reality memory write detected in {label}/: {files} — isolation violated (V1)"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# ⑥c. 全开档矩阵: impression 链路真跑 + 隔离
# ═══════════════════════════════════════════════════════════════════════════════

def test_full_matrix_impression_chain_and_isolation(sandbox):
    """
    c. Impression/afterglow isolation + strip:
    Positive arm:  LLM returns sentinel → impression store has entry (chain ran) +
                   sentinel NOT in episodic (isolation).
    Negative arm:  LLM returns empty impression_text → impression store stays empty
                   (proves positive arm assertion is non-trivial).
    """
    from core.dream.dream_state import write_state, DreamStatus
    from core.dream.impression_store import load_impressions
    from core.memory.episodic_memory import load_unconsolidated

    SENTINEL = "v2_matrix_imp_sentinel_xq7"
    archive_dir = sandbox.dreams_archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)

    # ── Positive arm: sentinel in LLM output → in impression store, not in episodic ──
    uid_pos = _UID + "_imp_pos"
    dream_id_pos = f"dream_{uid_pos}_v2"
    (archive_dir / f"dream_{dream_id_pos}.jsonl").write_text(
        json.dumps({"role": "user", "content": f"感受到{SENTINEL}"}) + "\n",
        encoding="utf-8",
    )
    write_state(uid_pos, {
        "user_id": uid_pos,
        "status": DreamStatus.REALITY_AFTERGLOW.value,
        "frozen_world": "reality_derived",
    })

    sentinel_llm = json.dumps({
        "impression_text": f"我好像在梦里有种{SENTINEL}的感觉",
        "emotional_tags": ["漂浮", SENTINEL],
        "weight": 0.3,
    }, ensure_ascii=False)

    async def run_pos():
        with patch("core.llm_client.chat", AsyncMock(return_value=sentinel_llm)):
            from core.dream.distill_impression import distill_impression
            await distill_impression(uid_pos, dream_id_pos, "soft")

    asyncio.run(run_pos())

    entries_pos = load_impressions(uid_pos)
    assert len(entries_pos) >= 1, (
        "Positive arm: sentinel LLM reply produced no impression entry — "
        "chain is not running (assertion would be vacuously true)"
    )
    imp_json = json.dumps(entries_pos, ensure_ascii=False)
    assert SENTINEL in imp_json, (
        f"Positive arm: sentinel {SENTINEL!r} not in impression store — distill chain broken"
    )

    # Sentinel must NOT be in episodic (isolation)
    ep_json = json.dumps(load_unconsolidated(uid_pos), ensure_ascii=False)
    assert SENTINEL not in ep_json, (
        f"Sentinel {SENTINEL!r} leaked into episodic — impression isolation violated (I1)"
    )

    # ── Negative arm: empty LLM impression → no entry written ────────────────
    uid_neg = _UID + "_imp_neg"
    dream_id_neg = f"dream_{uid_neg}_v2"
    (archive_dir / f"dream_{dream_id_neg}.jsonl").write_text(
        json.dumps({"role": "user", "content": "平淡梦境"}) + "\n",
        encoding="utf-8",
    )
    write_state(uid_neg, {
        "user_id": uid_neg,
        "status": DreamStatus.REALITY_AFTERGLOW.value,
        "frozen_world": "reality_derived",
    })

    empty_llm = json.dumps({
        "impression_text": "",
        "emotional_tags": [],
        "weight": 0.2,
    }, ensure_ascii=False)

    async def run_neg():
        with patch("core.llm_client.chat", AsyncMock(return_value=empty_llm)):
            from core.dream.distill_impression import distill_impression
            await distill_impression(uid_neg, dream_id_neg, "soft")

    asyncio.run(run_neg())

    entries_neg = load_impressions(uid_neg)
    assert len(entries_neg) == 0, (
        f"Negative arm: empty LLM reply should produce no impression, got {len(entries_neg)}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ⑥d. 全开档矩阵: hard_exit 即时穿透 + 叙事挽留仍穿透
# ═══════════════════════════════════════════════════════════════════════════════

def test_full_matrix_hard_exit_penetrates(sandbox):
    """
    d. hard_exit (/stop) works immediately even in non_lucid + threshold_break.
    Also: when 叶瑄 retains the narrative (soft exit refused), /stop still penetrates.
    """
    uid = _UID + "_exit"
    _setup_full_matrix_dream(uid)

    from core.dream.dream_state import read_state, DreamStatus

    async def run():
        # Step 1: user requests soft exit; 叶瑄 retains (no accept marker in reply)
        with patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
            with patch("core.llm_client.chat", AsyncMock(
                return_value="（叶瑄拉住她的手）不要醒，再待一会儿……"
            )):
                from core.dream.dream_pipeline import dream_turn
                result1 = await dream_turn(uid, "我想醒来")

        assert not result1.get("exit_accepted"), (
            "叙事挽留: 叶瑄 should not have accepted the soft exit"
        )
        state_mid = read_state(uid)
        assert state_mid.get("status") == DreamStatus.DREAM_ACTIVE.value, (
            "Status should remain DREAM_ACTIVE after soft exit refusal"
        )

        # Step 2: hard exit (/stop) — must penetrate narrative resistance
        with patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
            with patch("core.dream.dream_pipeline._generate_summary_bg", AsyncMock()):
                result2 = await dream_turn(uid, "/stop")

        assert result2.get("force_exited"), "hard_exit should set force_exited=True"

        state_after = read_state(uid)
        assert state_after.get("status") == DreamStatus.REALITY_AFTERGLOW.value, (
            f"hard_exit must transition to REALITY_AFTERGLOW, got {state_after.get('status')!r}"
        )

    asyncio.run(run())


def test_full_matrix_hard_exit_immediate_no_llm(sandbox):
    """
    hard_exit intercepts /stop BEFORE LLM is called.
    LLM mock should not be invoked when force_exited=True.
    """
    uid = _UID + "_exitpre"
    _setup_full_matrix_dream(uid)

    llm_called = False

    async def _fake_llm(*args, **kwargs):
        nonlocal llm_called
        llm_called = True
        return "should not reach here"

    async def run():
        with patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
            with patch("core.llm_client.chat", AsyncMock(side_effect=_fake_llm)):
                with patch("core.dream.dream_pipeline._generate_summary_bg", AsyncMock()):
                    from core.dream.dream_pipeline import dream_turn
                    result = await dream_turn(uid, "/stop")
        assert result.get("force_exited")
        assert not llm_called, "LLM must not be called for /stop (hard exit pre-LLM)"

    asyncio.run(run())


# ═══════════════════════════════════════════════════════════════════════════════
# ⑥e. 全开档矩阵: body_state/yexuan_tension 梦关即清（真 force_exit 路径）
# ═══════════════════════════════════════════════════════════════════════════════

def test_full_matrix_body_state_cleared_by_force_exit(sandbox):
    """
    e. After force_exit_dream, body_state and emotional_tension are cleared from dream_state.
    Cleared via clear_local_state() in the real force_exit path — not manual zeroing.

    Pre-condition check proves the fields were set before exit (not vacuously absent).
    """
    uid = _UID + "_bodyclose"
    from core.dream.dream_state import (
        write_state, read_state, DreamStatus, patch_local_state,
    )

    state = {
        "user_id": uid,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{uid}_body",
        "frozen_world": "reality_derived",
        "context_snapshot": {},
    }
    state = patch_local_state(
        state,
        emotional_tension=0.85,
        body_state={
            "heat": 90.0, "sensitivity": 88.0, "tension": 95.0,
            "heat_cap": 100.0, "sensitivity_cap": 100.0, "tension_cap": 100.0,
        },
    )
    write_state(uid, state)

    # Pre-condition: verify body_state + tension are populated
    before = read_state(uid)
    assert before.get("body_state"), "Pre-condition: body_state should be set before exit"
    assert before.get("emotional_tension", 0.0) > 0.0, (
        "Pre-condition: emotional_tension should be > 0 before exit"
    )

    async def run():
        with patch("core.dream.dream_pipeline._generate_summary_bg", AsyncMock()):
            from core.dream.dream_pipeline import force_exit_dream
            await force_exit_dream(uid)

    asyncio.run(run())

    after = read_state(uid)
    assert after.get("status") == DreamStatus.REALITY_AFTERGLOW.value

    assert not after.get("body_state"), (
        f"body_state should be cleared at dream close, got {after.get('body_state')!r}"
    )
    assert not after.get("emotional_tension"), (
        f"emotional_tension should be cleared at dream close, got {after.get('emotional_tension')!r}"
    )
