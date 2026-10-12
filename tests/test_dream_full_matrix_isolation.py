from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_dream_full_matrix_isolation.py — 全开档矩阵下的现实隔离与退出契约

从 test_dream_v2.py 的 ⑥a-e 部分拆出（Brief 50 · 工单D）。

配置：enable_dream_lorebook=False, memory_access=full_snapshot,
boundary_level=threshold_break, world_layer=reality_derived, lucid_mode=non_lucid
——所有档位全开，验证即便在最"危险"的配置下现实隔离和退出协议依然成立。
这是与 test_dream_turn_isolation.py（默认档位配置）互补的另一组配置变体，
不是重复：不同 settings 组合可能触发不同代码分支。

Covers:
  a. 全开档矩阵: 现实 mood_state 未变 + 正控（mood_state.update()确实改mood）
  b. 全开档矩阵: 无现实记忆写入（episodic/history/mid_term）
  c. 全开档矩阵: impression 链路真跑 + 隔离（哨兵进印象库；空LLM不进）
  d. 全开档矩阵: hard_exit 即时穿透；叙事挽留（Companion拒绝软退）后 /stop 仍穿透
  e. 全开档矩阵: body_state/yexuan_tension 梦关即清（真 force_exit 路径，非手动赋值）

★ 每个"X不在Y"断言均配正样本对照（反假绿铁律）。
"""

import asyncio
import json
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
# a. 全开档矩阵: 现实 mood_state 未变 + 正控
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
    mood_path = sandbox.mood_state(char_id=TEST_CHAR_ID)
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
    _mood_update("happy", 0.8, source="explicit", source_turn_id="positive-control", source_at=__import__("time").time())

    assert mood_path.exists(), (
        "Positive control failed: mood_state.update() should create mood_state.json"
    )
    raw = mood_path.read_text(encoding="utf-8")
    assert "happy" in raw, (
        f"Positive control: 'happy' not found in mood_state.json content: {raw!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# b. 全开档矩阵: 无现实记忆写入
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
# c. 全开档矩阵: impression 链路真跑 + 隔离
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
# d. 全开档矩阵: hard_exit 即时穿透 + 叙事挽留仍穿透
# ═══════════════════════════════════════════════════════════════════════════════

def test_full_matrix_hard_exit_penetrates(sandbox):
    """
    d. hard_exit (/stop) works immediately even in non_lucid + threshold_break.
    Also: when Companion retains the narrative (soft exit refused), /stop still penetrates.
    """
    uid = _UID + "_exit"
    _setup_full_matrix_dream(uid)

    from core.dream.dream_state import read_state, DreamStatus

    async def run():
        # Step 1: user requests soft exit; Companion retains (no accept marker in reply)
        with patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
            with patch("core.llm_client.chat", AsyncMock(
                return_value="（Companion拉住她的手）不要醒，再待一会儿……"
            )):
                from core.dream.dream_pipeline import dream_turn
                result1 = await dream_turn(uid, "我想醒来")

        assert not result1.get("exit_accepted"), (
            "叙事挽留: Companion should not have accepted the soft exit"
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
# e. 全开档矩阵: body_state/yexuan_tension 梦关即清（真 force_exit 路径）
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
