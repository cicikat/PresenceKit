"""
tests/test_hidden_state_char_scope.py — P0-T06: hidden_state / afterglow char_id plumbing

Covers:
1.  hidden_state store 写入指定 char_id 路径
2.  hidden_state store 读取指定 char_id 路径（隔离两份不同内容）
3.  afterglow_residue 写入指定 char_id 路径，不污染另一桶
4.  integrator 透传 char_id 到 store（monkeypatch 捕获 kwargs）
5.  Dream afterglow 回流写入 session char_id（wire_afterglow_from_summary）
6.  切换 active 后旧 dream afterglow 仍写回入梦角色（不读 active_character）
7.  legacy dream_state 缺 char_id：WARN + fallback yexuan，不崩溃
8.  hidden_state_decay P1: 遍历注册角色，对每个 char_id 调用 load/save（不依赖 active_character）
9.  隔离验收：yexuan afterglow 不写入 character_b 桶
10. legacy 默认兼容：不传 char_id 默认 yexuan
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


_UID = "hs_char_scope_u1"
_NOW = datetime.now(timezone.utc).isoformat()


# ── 1. hidden_state store 写入指定 char_id 路径 ────────────────────────────────

def test_save_hidden_state_writes_to_char_id_path(sandbox):
    """save_hidden_state(uid, state, char_id='character_b') 只写 character_b 桶。"""
    from core.memory.user_hidden_state import default_hidden_state
    from core.memory.user_hidden_state_store import save_hidden_state

    state = default_hidden_state()
    save_hidden_state(_UID, state, char_id="character_b")

    character_b_path = sandbox.user_memory_root(_UID, char_id="character_b") / "hidden_state.json"
    yexuan_path  = sandbox.user_memory_root(_UID, char_id=TEST_CHAR_ID)  / "hidden_state.json"

    assert character_b_path.exists(), "character_b hidden_state.json should be written"
    assert not yexuan_path.exists(), f'{TEST_CHAR_ID} hidden_state.json must NOT be written'


# ── 2. hidden_state store 读取指定 char_id 路径 ────────────────────────────────

def test_load_hidden_state_reads_from_char_id_path(sandbox):
    """load_hidden_state 分别读取 yexuan / character_b 桶，返回各自内容。"""
    from core.memory.user_hidden_state import default_hidden_state
    from core.memory.user_hidden_state_store import load_hidden_state, save_hidden_state

    state_y = default_hidden_state()
    state_y.sensitivity.baseline.value = 42.0

    state_h = default_hidden_state()
    state_h.sensitivity.baseline.value = 77.0

    save_hidden_state(_UID, state_y, char_id=TEST_CHAR_ID)
    save_hidden_state(_UID, state_h, char_id="character_b")

    loaded_y = load_hidden_state(_UID, char_id=TEST_CHAR_ID)
    loaded_h = load_hidden_state(_UID, char_id="character_b")

    assert loaded_y.sensitivity.baseline.value == pytest.approx(42.0), \
        f'{TEST_CHAR_ID} 桶应返回 baseline=42'
    assert loaded_h.sensitivity.baseline.value == pytest.approx(77.0), \
        "character_b 桶应返回 baseline=77"
    assert loaded_y.sensitivity.baseline.value != loaded_h.sensitivity.baseline.value, \
        "两桶内容必须隔离"


# ── 3. afterglow_residue 写入指定 char_id 路径 ────────────────────────────────

def test_save_afterglow_residue_writes_to_char_id_path(sandbox):
    """save_afterglow_residue(uid, residue, created_at, char_id='character_b') 只写 character_b 桶。"""
    from core.memory.user_hidden_state import AfterglowResidueInput
    from core.memory.user_hidden_state_store import save_afterglow_residue

    residue = AfterglowResidueInput(emotional_tags=["温柔"], tone="calm", age_hours=0.0)
    save_afterglow_residue(_UID, residue, created_at=_NOW, char_id="character_b")

    character_b_path = sandbox.user_memory_root(_UID, char_id="character_b") / "afterglow_residue.json"
    yexuan_path  = sandbox.user_memory_root(_UID, char_id=TEST_CHAR_ID)  / "afterglow_residue.json"

    assert character_b_path.exists(), "character_b afterglow_residue.json should be written"
    assert not yexuan_path.exists(), f'{TEST_CHAR_ID} afterglow_residue.json must NOT be written'

    data = json.loads(character_b_path.read_text(encoding="utf-8"))
    assert data.get("tone") == "calm"


# ── 4. integrator 透传 char_id 到 store ───────────────────────────────────────

def test_integrate_afterglow_and_save_passes_char_id(sandbox):
    """integrate_afterglow_and_save 必须把 char_id 透传给 load/save。"""
    from core.memory.user_hidden_state import AfterglowResidueInput
    from core.memory.user_hidden_state_integrator import integrate_afterglow_and_save
    from core.write_envelope import stamp_dream_afterglow

    load_calls: list[dict] = []
    save_calls: list[dict] = []

    from core.memory.user_hidden_state import default_hidden_state
    def _mock_load(uid, *, char_id=TEST_CHAR_ID):
        load_calls.append({"uid": uid, "char_id": char_id})
        return default_hidden_state()

    def _mock_save(uid, state, *, char_id=TEST_CHAR_ID):
        save_calls.append({"uid": uid, "char_id": char_id})
        return True

    residue = AfterglowResidueInput(emotional_tags=["温柔"], tone="comfort", age_hours=0.0)
    envelope = stamp_dream_afterglow()

    with patch("core.memory.user_hidden_state_integrator.load_hidden_state", _mock_load), \
         patch("core.memory.user_hidden_state_integrator.save_hidden_state", _mock_save):
        integrate_afterglow_and_save(_UID, residue, envelope, _NOW, char_id="character_b")

    assert load_calls, "load_hidden_state must be called"
    assert load_calls[0]["char_id"] == "character_b", \
        f"load expected char_id='character_b', got {load_calls[0]['char_id']!r}"

    assert save_calls, "save_hidden_state must be called (envelope accepted)"
    assert save_calls[0]["char_id"] == "character_b", \
        f"save expected char_id='character_b', got {save_calls[0]['char_id']!r}"


# ── 5. Dream afterglow 回流写入 session char_id ───────────────────────────────

def test_wire_afterglow_passes_char_id_to_integrator(sandbox):
    """wire_afterglow_from_summary(char_id='character_b') 把 character_b 传给 integrate_afterglow_and_save。"""
    import time as _time
    from core.dream.dream_exit_afterglow import wire_afterglow_from_summary

    dream_id = f"dream_{_UID}_wire_test"

    summaries_dir = sandbox.dreams_summaries_dir(char_id="character_b")
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "dream_id": dream_id,
        "uid": _UID,
        "char_id": "character_b",
        "created_at": _time.time(),
        "exit_type": "soft",
        "afterglow": "gentle_residue",
        "summary_weight": 0.8,
        "emotional_tags": ["温柔"],
        "reality_boundary": "dream_only",
        "never_retrieve": True,
        "not_memory_source": True,
    }
    (summaries_dir / f"dream_{dream_id}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )

    integrate_calls: list[dict] = []

    def _mock_integrate(uid, residue, *, write_envelope, now=None, char_id=TEST_CHAR_ID, ref=""):
        integrate_calls.append({"uid": uid, "char_id": char_id, "tone": residue.tone})
        return MagicMock(), MagicMock(accepted=True, rejected=False,
                                      touched_fields=[], rejected_reasons=[])

    # save_afterglow_residue / integrate_afterglow_and_save are lazy-imported inside _do_wire
    # so we patch them at their source modules.
    with patch("core.memory.user_hidden_state_store.save_afterglow_residue",
               return_value=True), \
         patch("core.memory.user_hidden_state_integrator.integrate_afterglow_and_save",
               _mock_integrate), \
         patch("core.write_envelope.stamp_dream_afterglow",
               return_value=MagicMock()):
        wire_afterglow_from_summary(_UID, dream_id, "soft", char_id="character_b")

    assert integrate_calls, "integrate_afterglow_and_save must be called"
    assert integrate_calls[0]["char_id"] == "character_b", \
        f"integrator expected char_id='character_b', got {integrate_calls[0]['char_id']!r}"


# ── 6. active 切换后，旧 dream afterglow 仍写回入梦角色 ───────────────────────

def test_wire_afterglow_uses_session_char_id_not_active(sandbox):
    """afterglow 回流读取 session char_id=TEST_CHAR_ID，不管 active 已切到 character_b。"""
    import time as _time
    from core.dream.dream_exit_afterglow import wire_afterglow_from_summary

    dream_id = f"dream_{_UID}_session_lock"

    # dream 入梦时是 yexuan
    summaries_dir = sandbox.dreams_summaries_dir(char_id=TEST_CHAR_ID)
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "dream_id": dream_id,
        "uid": _UID,
        "char_id": TEST_CHAR_ID,
        "created_at": _time.time(),
        "exit_type": "soft",
        "afterglow": "gentle_residue",
        "summary_weight": 0.5,
        "emotional_tags": [],
        "reality_boundary": "dream_only",
        "never_retrieve": True,
        "not_memory_source": True,
    }
    (summaries_dir / f"dream_{dream_id}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )

    integrate_calls: list[dict] = []

    def _mock_integrate(uid, residue, *, write_envelope, now=None, char_id=TEST_CHAR_ID, ref=""):
        integrate_calls.append({"char_id": char_id})
        return MagicMock(), MagicMock(accepted=True, rejected=False,
                                      touched_fields=[], rejected_reasons=[])

    # 模拟 active 已切到 character_b（不会影响 wire，因为 char_id 来自显式参数）
    with patch("core.memory.user_hidden_state_store.save_afterglow_residue",
               return_value=True), \
         patch("core.memory.user_hidden_state_integrator.integrate_afterglow_and_save",
               _mock_integrate), \
         patch("core.write_envelope.stamp_dream_afterglow",
               return_value=MagicMock()):
        # char_id 来自调用方（T-05.5 锁定的 dream_state.char_id），不是 active_character
        wire_afterglow_from_summary(_UID, dream_id, "soft", char_id=TEST_CHAR_ID)

    assert integrate_calls, "integrate_afterglow_and_save must be called"
    assert integrate_calls[0]["char_id"] == TEST_CHAR_ID, \
        "Dream 回流必须用入梦时的 char_id=TEST_CHAR_ID，不能改为当前 active"


# ── 7. legacy dream_state 缺 char_id — WARN + fallback yexuan ─────────────────

def test_legacy_dream_state_missing_char_id_warns_and_fallbacks(caplog):
    """_state_char_id 处理旧 dream_state（无 char_id）时 WARN + 返回 yexuan，不崩溃。"""
    from core.dream.dream_pipeline import _state_char_id

    legacy_state = {"status": "dream_active", "dream_id": "old_dream_123"}

    with caplog.at_level(logging.WARNING, logger="core.dream.dream_pipeline"):
        result = _state_char_id(legacy_state, "test_handler", uid="u99", dream_id="old_dream_123")

    assert result == TEST_CHAR_ID, f"legacy fallback must be TEST_CHAR_ID, got {result!r}"
    warn_text = caplog.text.lower()
    assert any(
        kw in warn_text
        for kw in ("legacy", "fallback", TEST_CHAR_ID)
    ), f"WARN must mention legacy/fallback/yexuan, got: {caplog.text!r}"


# ── 8. hidden_state_decay P1: 遍历注册角色而非读 active char ──────────────────

def test_hidden_state_decay_iterates_registered_chars(sandbox):
    """P1: _check_hidden_state_decay 遍历注册角色，对每个 char_id 调用 load/save，不依赖 active_character。"""
    from core.scheduler.triggers import hidden_state_decay as _hsd
    from core.memory.user_hidden_state import default_hidden_state

    uid_dir = sandbox.memory_char_root(char_id="character_b") / "test_uid"
    uid_dir.mkdir(parents=True, exist_ok=True)
    (uid_dir / "hidden_state.json").write_text("{}", encoding="utf-8")

    load_calls: list[dict] = []
    save_calls: list[dict] = []

    def _mock_load(uid, *, char_id=TEST_CHAR_ID):
        load_calls.append({"uid": uid, "char_id": char_id})
        return default_hidden_state()

    def _mock_save(uid, state, *, char_id=TEST_CHAR_ID):
        save_calls.append({"uid": uid, "char_id": char_id})
        return True

    mock_reg = MagicMock()
    mock_reg.list_all.return_value = [MagicMock(id="character_b")]

    with patch("core.scheduler.loop._is_ready", return_value=True), \
         patch("core.scheduler.loop._mark"), \
         patch("core.asset_registry.get_registry", return_value=mock_reg), \
         patch("core.memory.user_hidden_state_store.load_hidden_state", _mock_load), \
         patch("core.memory.user_hidden_state_store.save_hidden_state", _mock_save), \
         patch("core.memory.user_hidden_state.apply_time_decay",
               side_effect=lambda s, _n: s):
        asyncio.run(_hsd._check_hidden_state_decay())

    assert load_calls, "load_hidden_state must be called for registered char"
    assert load_calls[0]["char_id"] == "character_b", \
        f"decay load expected char_id='character_b', got {load_calls[0]['char_id']!r}"
    assert save_calls, "save_hidden_state must be called"
    assert save_calls[0]["char_id"] == "character_b", \
        f"decay save expected char_id='character_b', got {save_calls[0]['char_id']!r}"


def test_hidden_state_decay_not_blocked_by_missing_active(sandbox):
    """P1: active_prompt_assets 缺失不影响 decay，仍对所有注册角色运行。"""
    from core.scheduler.triggers import hidden_state_decay as _hsd
    from core.memory.user_hidden_state import default_hidden_state

    uid_dir = sandbox.memory_char_root(char_id=TEST_CHAR_ID) / "owner1"
    uid_dir.mkdir(parents=True, exist_ok=True)
    (uid_dir / "hidden_state.json").write_text("{}", encoding="utf-8")

    save_called = []

    def _spy_save(uid, state, *, char_id=TEST_CHAR_ID):
        save_called.append(char_id)
        return True

    mock_reg = MagicMock()
    mock_reg.list_all.return_value = [MagicMock(id=TEST_CHAR_ID)]

    with patch("core.scheduler.loop._is_ready", return_value=True), \
         patch("core.scheduler.loop._mark"), \
         patch("core.asset_registry.get_registry", return_value=mock_reg), \
         patch("core.memory.user_hidden_state_store.load_hidden_state",
               return_value=default_hidden_state()), \
         patch("core.memory.user_hidden_state_store.save_hidden_state", _spy_save), \
         patch("core.memory.user_hidden_state.apply_time_decay",
               side_effect=lambda s, _n: s):
        asyncio.run(_hsd._check_hidden_state_decay())

    assert save_called, "save MUST be called — missing active must NOT block decay"
    assert save_called[0] == TEST_CHAR_ID


# ── 9. 隔离验收：yexuan afterglow 不写入 character_b 桶 ────────────────────────────

def test_afterglow_isolation_yexuan_does_not_pollute_character_b(sandbox):
    """
    yexuan 触发 afterglow 落盘 + integrate → character_b 桶不受影响。
    直接调用同步函数，不使用 sleep。
    """
    from core.memory.user_hidden_state import (
        AfterglowResidueInput,
        default_hidden_state,
    )
    from core.memory.user_hidden_state_store import (
        load_hidden_state,
        save_afterglow_residue,
        save_hidden_state,
    )
    from core.memory.user_hidden_state_integrator import integrate_afterglow_and_save
    from core.write_envelope import stamp_dream_afterglow

    # 预置两桶 baseline 不同，方便验证隔离
    state_y = default_hidden_state()
    state_y.sensitivity.current.value = 50.0
    save_hidden_state(_UID, state_y, char_id=TEST_CHAR_ID)

    state_h = default_hidden_state()
    state_h.sensitivity.current.value = 50.0
    save_hidden_state(_UID, state_h, char_id="character_b")

    # yexuan afterglow 落盘
    residue = AfterglowResidueInput(
        emotional_tags=["warm"], tone="comfort", age_hours=0.0
    )
    save_afterglow_residue(_UID, residue, created_at=_NOW, char_id=TEST_CHAR_ID)

    # yexuan integrate（comfort tone → sensitivity.current 上涨）
    envelope = stamp_dream_afterglow()
    integrate_afterglow_and_save(_UID, residue, envelope, _NOW, char_id=TEST_CHAR_ID)

    # character_b 桶应当未被写入
    character_b_after = load_hidden_state(_UID, char_id="character_b")
    yexuan_after  = load_hidden_state(_UID, char_id=TEST_CHAR_ID)

    assert character_b_after.sensitivity.current.value == pytest.approx(50.0), \
        f'character_b 桶 sensitivity.current 不应被 {TEST_CHAR_ID} afterglow 改动'
    assert yexuan_after.sensitivity.current.value > 50.0, \
        f'{TEST_CHAR_ID} 桶 sensitivity.current 应已被 afterglow 上调'


# ── 10. legacy 默认兼容：不传 char_id 默认 yexuan ─────────────────────────────

def test_legacy_call_without_char_id_defaults_to_yexuan(sandbox):
    """不传 char_id 的旧调用写入 yexuan 桶（兼容默认行为）。"""
    from core.memory.user_hidden_state import default_hidden_state
    from core.memory.user_hidden_state_store import (
        load_hidden_state,
        save_hidden_state,
    )

    state = default_hidden_state()
    state.sensitivity.baseline.value = 55.0

    # 不传 char_id — legacy 调用
    save_hidden_state(_UID, state)
    loaded = load_hidden_state(_UID)

    yexuan_path = sandbox.user_memory_root(_UID, char_id=TEST_CHAR_ID) / "hidden_state.json"
    assert yexuan_path.exists(), f'legacy 默认应写入 {TEST_CHAR_ID} 桶'
    assert loaded.sensitivity.baseline.value == pytest.approx(55.0)


def test_production_path_with_explicit_char_id_writes_correct_bucket(sandbox):
    """生产主路径用显式 char_id='character_b' 确保写入正确桶，不依赖默认值。"""
    from core.memory.user_hidden_state import default_hidden_state
    from core.memory.user_hidden_state_store import (
        load_hidden_state,
        save_hidden_state,
    )

    state = default_hidden_state()
    state.sensitivity.baseline.value = 88.0

    # 生产路径必须显式传 char_id
    save_hidden_state(_UID, state, char_id="character_b")
    loaded = load_hidden_state(_UID, char_id="character_b")

    assert loaded.sensitivity.baseline.value == pytest.approx(88.0)
    # 确认未污染 yexuan
    yexuan_path = sandbox.user_memory_root(_UID, char_id=TEST_CHAR_ID) / "hidden_state.json"
    assert not yexuan_path.exists(), f'生产路径不应写入 {TEST_CHAR_ID} 桶'
