"""
tests/test_user_hidden_state_envelope_and_gate_boundary.py — WriteEnvelope 拒绝/
强制关闭 + dream gate 边界 + 数值 clamp/outlier 安全边界

合并自 test_user_hidden_state_edge_cases.py（EC-01–EC-24）+
test_user_hidden_state_phase25.py（EC-25–EC-39，明确是"覆盖 EC-01–EC-24 未
触及的缺口"，与前者不重复，Brief 50 · 工单E）。40 个测试按主题拆分为本文件
（envelope 拒绝/gate 边界/数值 clamp，20个）+
test_user_hidden_state_longterm_isolation_and_accumulation.py（长期层隔离/
累积/审计，20个）。

Covers:
  - 单次假阳性输入（空/临界/传感器误报权重）一律 fail-closed（原 EC-01–05）
  - 极端初始值下的数值 clamp 不溢出/不下溢（原 EC-10–13）
  - 缺失或关闭的 envelope 一律拒绝写入（原 EC-14–18）
  - WriteEnvelope 构造器强制关闭 is_test/is_debug 覆盖显式 can_write_memory=True（原 EC-25–26）
  - dream gate 边界值精确接受/拒绝、错类型 event 静默丢弃、stamp_sensor 合法路径（原 EC-37–39）
"""
from __future__ import annotations

import pytest

from core.memory.user_hidden_state import (
    DREAM_GATE_MAX,
    DREAM_GATE_MIN,
    MAX_NUDGE_PER_EVENT,
    SCALAR_MAX,
    SCALAR_CENTER,
    DreamBodyStateEvent,
    ImpressionInput,
    UpdateSource,
    default_hidden_state,
)
from core.memory.user_hidden_state_integrator import (
    IMPRESSION_MAX_NUDGE,
    RealityEventType,
    integrate_event,
    integrate_event_and_save,
    integrate_impression,
)
from core.memory.user_hidden_state_store import (
    HIDDEN_STATE_FILENAME,
    load_hidden_state,
    save_hidden_state,
)
from core.write_envelope import (
    WriteEnvelope,
    stamp_debug,
    stamp_sensor,
    stamp_sensor_watch,
    stamp_test,
    stamp_user_chat,
)

NOW = "2026-06-02T12:00:00Z"
TEST_UID = "user_edge_case"


def _open() -> WriteEnvelope:
    return stamp_user_chat()


# ═══════════════════════════════════════════════════════════════════════════════
# 单次假阳性输入必须 fail-closed（原 edge_cases EC-01–05）
# ═══════════════════════════════════════════════════════════════════════════════

class TestFalsePositiveEvents:
    """EC-01 – EC-05: Single random bad inputs must all fail-closed."""

    def test_ec01_null_impression_weight_rejected(self):
        """EC-01 fail-closed: weight=0.0 is a hallucinated null dream → below gate."""
        state = default_hidden_state()
        imp = ImpressionInput(weight=0.0)
        _, result = integrate_impression(imp, state, _open(), NOW)

        assert result.rejected, "null weight must be rejected"
        assert not result.accepted
        assert any("gate" in r for r in result.rejected_reasons)
        # State must be unchanged
        assert state.sensitivity.current.value == SCALAR_CENTER

    def test_ec02_borderline_subgate_weight_rejected(self):
        """EC-02 fail-closed: weight just below DREAM_GATE_MIN (sensor borderline false-report)."""
        state = default_hidden_state()
        subgate = DREAM_GATE_MIN - 0.001
        imp = ImpressionInput(weight=subgate)
        _, result = integrate_impression(imp, state, _open(), NOW)

        assert result.rejected, f"weight {subgate:.4f} is below gate, must be rejected"
        assert state.sensitivity.current.value == SCALAR_CENTER

    def test_ec03_sensor_watch_blocks_reality_event(self):
        """EC-03 fail-closed: stamp_sensor_watch() has can_write_memory=False → event blocked."""
        state = default_hidden_state()
        state.touch_need.deficit.value = 50.0
        watch_envelope = stamp_sensor_watch()

        _, result = integrate_event(RealityEventType.SEEK_COMPANIONSHIP, state, watch_envelope, NOW)

        assert result.rejected
        assert state.touch_need.deficit.value == pytest.approx(50.0), \
            "sensor_watch envelope must not discharge deficit"

    def test_ec04_sensor_watch_blocks_impression(self):
        """EC-04 fail-closed: stamp_sensor_watch() → impression blocked."""
        state = default_hidden_state()
        original_sens = state.sensitivity.current.value
        watch_envelope = stamp_sensor_watch()
        mid_weight = (DREAM_GATE_MIN + DREAM_GATE_MAX) / 2

        imp = ImpressionInput(weight=mid_weight)
        _, result = integrate_impression(imp, state, watch_envelope, NOW)

        assert result.rejected
        assert state.sensitivity.current.value == pytest.approx(original_sens), \
            "sensor_watch envelope must not nudge sensitivity"

    def test_ec05_overweight_hallucination_rejected(self):
        """EC-05 fail-closed: weight=0.99 (far above DREAM_GATE_MAX) → rejected."""
        state = default_hidden_state()
        imp = ImpressionInput(weight=0.99)
        _, result = integrate_impression(imp, state, _open(), NOW)

        assert result.rejected, "weight 0.99 is far above DREAM_GATE_MAX, must be rejected"
        assert state.sensitivity.current.value == SCALAR_CENTER


# ═══════════════════════════════════════════════════════════════════════════════
# 极端初始值下的数值 clamp 不溢出/不下溢（原 edge_cases EC-10–13）
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutlierRealityEvent:
    """EC-10 – EC-13: Extreme initial states must not break clamping guarantees."""

    def test_ec10_discharge_from_zero_stays_at_zero(self):
        """EC-10 clamp: deficit=0 + SEEK_COMPANIONSHIP → deficit stays at 0 (no underflow)."""
        state = default_hidden_state()
        state.touch_need.deficit.value = 0.0

        state, result = integrate_event(RealityEventType.SEEK_COMPANIONSHIP, state, _open(), NOW)

        assert result.accepted
        assert state.touch_need.deficit.value == pytest.approx(0.0), \
            "discharging from 0 must not produce negative deficit"

    def test_ec11_accrue_from_scalar_max_stays_at_max(self):
        """EC-11 clamp: deficit=100 + NO_INTERACTION → deficit stays at SCALAR_MAX (no overflow)."""
        state = default_hidden_state()
        state.touch_need.deficit.value = SCALAR_MAX

        state, result = integrate_event(RealityEventType.NO_INTERACTION, state, _open(), NOW)

        assert result.accepted
        assert state.touch_need.deficit.value == pytest.approx(SCALAR_MAX), \
            "accruing at SCALAR_MAX must not exceed 100"

    def test_ec12_max_gate_impression_delta_equals_impression_max_nudge(self):
        """EC-12 nudge cap: weight=DREAM_GATE_MAX → delta exactly equals IMPRESSION_MAX_NUDGE."""
        state = default_hidden_state()
        state.sensitivity.current.value = 50.0

        imp = ImpressionInput(weight=DREAM_GATE_MAX)
        state, result = integrate_impression(imp, state, _open(), NOW)

        delta = result.touched_fields[0].new_value - result.touched_fields[0].old_value
        assert result.accepted
        assert delta == pytest.approx(IMPRESSION_MAX_NUDGE), \
            "max-gate impression must produce exactly IMPRESSION_MAX_NUDGE delta"
        assert delta <= MAX_NUDGE_PER_EVENT, \
            "delta must always stay within global MAX_NUDGE_PER_EVENT cap"

    def test_ec13_sensitivity_at_scalar_max_does_not_overflow(self):
        """EC-13 clamp: sensitivity.current=100 + impression → stays at SCALAR_MAX."""
        state = default_hidden_state()
        state.sensitivity.current.value = SCALAR_MAX

        imp = ImpressionInput(weight=DREAM_GATE_MAX)
        state, result = integrate_impression(imp, state, _open(), NOW)

        assert result.accepted
        assert state.sensitivity.current.value == pytest.approx(SCALAR_MAX), \
            "sensitivity.current must not exceed SCALAR_MAX"


# ═══════════════════════════════════════════════════════════════════════════════
# 缺失或关闭的 envelope 一律拒绝写入（原 edge_cases EC-14–18）
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingOrClosedEnvelope:
    """EC-14 – EC-18: Any path that lacks can_write_memory=True must fail-closed."""

    def test_ec14_stamp_test_envelope_rejected(self):
        """EC-14 fail-closed: stamp_test() forces can_write_memory=False → event rejected."""
        state = default_hidden_state()
        state.touch_need.deficit.value = 30.0
        test_envelope = stamp_test()

        assert test_envelope.can_write_memory is False, \
            "stamp_test must auto-close can_write_memory"

        _, result = integrate_event(RealityEventType.SEEK_COMPANIONSHIP, state, test_envelope, NOW)
        assert result.rejected
        assert state.touch_need.deficit.value == pytest.approx(30.0)

    def test_ec15_stamp_debug_envelope_rejected(self):
        """EC-15 fail-closed: stamp_debug() forces can_write_memory=False → impression rejected."""
        state = default_hidden_state()
        debug_envelope = stamp_debug()

        assert debug_envelope.can_write_memory is False, \
            "stamp_debug must auto-close can_write_memory"

        mid_weight = (DREAM_GATE_MIN + DREAM_GATE_MAX) / 2
        imp = ImpressionInput(weight=mid_weight)
        _, result = integrate_impression(imp, state, debug_envelope, NOW)
        assert result.rejected

    def test_ec16_stamp_sensor_watch_has_can_write_memory_false(self):
        """EC-16 envelope check: stamp_sensor_watch() envelope property is can_write_memory=False."""
        env = stamp_sensor_watch()
        assert env.can_write_memory is False, \
            "stamp_sensor_watch must hard-close can_write_memory"
        assert env.can_affect_mood is False

    def test_ec17_null_envelope_event_and_save_no_disk_write(self, sandbox):
        """EC-17 fail-closed disk: WriteEnvelope() zero-value + event_and_save → file not created."""
        null_envelope = WriteEnvelope()  # zero-value, most restrictive
        _, result = integrate_event_and_save(
            TEST_UID, RealityEventType.SEEK_COMPANIONSHIP, null_envelope, NOW
        )

        assert result.rejected
        path = sandbox.user_memory_root(TEST_UID) / HIDDEN_STATE_FILENAME
        assert not path.exists(), \
            "rejected event_and_save must not create hidden_state.json on disk"

    def test_ec18_rejected_event_last_update_source_stays_init(self):
        """EC-18 audit: rejected event must not stamp last_update_source (stays INIT)."""
        state = default_hidden_state()
        # Confirm initial source is INIT
        assert state.touch_need.deficit.last_update_source == UpdateSource.INIT

        closed_envelope = WriteEnvelope(can_write_memory=False)
        state, result = integrate_event(RealityEventType.NO_INTERACTION, state, closed_envelope, NOW)

        assert result.rejected
        assert state.touch_need.deficit.last_update_source == UpdateSource.INIT, \
            "rejected call must not overwrite last_update_source"
        assert state.touch_need.deficit.last_updated is None, \
            "rejected call must not stamp last_updated"


# ═══════════════════════════════════════════════════════════════════════════════
# WriteEnvelope 构造器强制关闭覆盖显式 can_write_memory=True（原 phase25 EC-25–26）
# ═══════════════════════════════════════════════════════════════════════════════

class TestWriteEnvelopeConstructorCoercion:
    """EC-25 / EC-26: __post_init__ must override explicit can_write_memory=True
    when is_test or is_debug is True."""

    def test_ec25_is_test_true_overrides_can_write_memory(self):
        """EC-25 fail-closed: WriteEnvelope(is_test=True, can_write_memory=True)
        must force can_write_memory to False, so the Reality event is rejected."""
        env = WriteEnvelope(is_test=True, can_write_memory=True)

        # __post_init__ override must have fired
        assert env.can_write_memory is False, (
            "is_test=True must force can_write_memory=False "
            "even when caller explicitly passed can_write_memory=True"
        )

        state = default_hidden_state()
        state.touch_need.deficit.value = 40.0
        _, result = integrate_event(RealityEventType.SEEK_COMPANIONSHIP, state, env, NOW)

        assert result.rejected
        assert not result.accepted
        assert state.touch_need.deficit.value == pytest.approx(40.0), (
            "is_test coerced envelope must not mutate deficit"
        )

    def test_ec26_is_debug_true_overrides_can_write_memory(self):
        """EC-26 fail-closed: WriteEnvelope(is_debug=True, can_write_memory=True)
        must force can_write_memory to False, so the impression is rejected."""
        env = WriteEnvelope(is_debug=True, can_write_memory=True)

        assert env.can_write_memory is False, (
            "is_debug=True must force can_write_memory=False "
            "even when caller explicitly passed can_write_memory=True"
        )

        state = default_hidden_state()
        original_sens = state.sensitivity.current.value
        mid_weight = (DREAM_GATE_MIN + DREAM_GATE_MAX) / 2
        imp = ImpressionInput(weight=mid_weight)
        _, result = integrate_impression(imp, state, env, NOW)

        assert result.rejected
        assert not result.accepted
        assert state.sensitivity.current.value == pytest.approx(original_sens), (
            "is_debug coerced envelope must not nudge sensitivity.current"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# dream gate 边界值 + 错类型静默丢弃 + 合法 sensor 路径（原 phase25 EC-37–39）
# ═══════════════════════════════════════════════════════════════════════════════

class TestGateBoundaryAndSilentDrop:
    """EC-37 / EC-38 / EC-39."""

    def test_ec37_impression_at_exact_gate_min_is_accepted(self):
        """EC-37 gate boundary: impression.weight == DREAM_GATE_MIN (0.2) is within
        the inclusive gate [DREAM_GATE_MIN, DREAM_GATE_MAX] and must NOT be rejected.

        Note — delta at the exact lower boundary:
          ratio = (DREAM_GATE_MIN − DREAM_GATE_MIN) / (DREAM_GATE_MAX − DREAM_GATE_MIN) = 0.0
          delta = 0.0 × IMPRESSION_MAX_NUDGE = 0.0

        So sensitivity.current does not numerically change, but the call is accepted
        (touched_fields is populated) and no rejection reason is produced.
        This is correct gating behaviour: weight=0.2 is *inside* the gate.
        """
        state = default_hidden_state()
        state.sensitivity.current.value = 50.0

        imp = ImpressionInput(weight=DREAM_GATE_MIN)  # exactly 0.2 — inclusive lower bound
        state, result = integrate_impression(imp, state, _open(), NOW)

        assert result.accepted, (
            f"weight={DREAM_GATE_MIN} is at the inclusive gate lower bound and must be accepted; "
            f"got rejected_reasons={result.rejected_reasons}"
        )
        assert not result.rejected_reasons, (
            "Gate-min impression must produce no rejection reasons"
        )
        # Delta at gate-min is 0.0 — no value movement, but no rejection either
        if result.touched_fields:
            nudge = result.touched_fields[0].new_value - result.touched_fields[0].old_value
            assert nudge >= 0.0, "Impression nudge must never be negative"

    def test_ec37_impression_at_exact_gate_min_is_not_rejected(self):
        """EC-37 gate boundary (clean version): weight exactly at DREAM_GATE_MIN
        must NOT appear in rejected_reasons."""
        state = default_hidden_state()
        imp = ImpressionInput(weight=DREAM_GATE_MIN)
        _, result = integrate_impression(imp, state, _open(), NOW)

        assert not result.rejected_reasons, (
            f"weight=DREAM_GATE_MIN must not produce a rejection; "
            f"got: {result.rejected_reasons}"
        )
        # Delta must be >= 0 (no negative nudge from impression path)
        if result.touched_fields:
            delta_val = result.touched_fields[0].new_value - result.touched_fields[0].old_value
            assert delta_val >= 0.0, "Impression delta must never be negative"

    def test_ec38_wrong_type_event_raises_before_mutation(self):
        """EC-38 wrong-type guard: passing a DreamBodyStateEvent (wrong type) as
        event_type to integrate_event raises TypeError BEFORE any envelope check
        or state mutation.

        Security contract:
          - TypeError is raised (clear, explicit, not an AttributeError)
          - State is unchanged at the point of the raise (no partial mutation)
        """
        state = default_hidden_state()
        state.touch_need.deficit.value = 45.0
        original_deficit = state.touch_need.deficit.value
        original_source = state.touch_need.deficit.last_update_source

        wrong_event = DreamBodyStateEvent(
            heat=0.8, sensitivity=0.9, tension=0.5, arousal=0.6, duration_min=30.0
        )

        with pytest.raises(TypeError):
            integrate_event(wrong_event, state, _open(), NOW)  # type: ignore

        # State must be fully unchanged (raise occurred before any mutation)
        assert state.touch_need.deficit.value == pytest.approx(original_deficit), (
            "Wrong event_type raise must not have mutated touch_need.deficit"
        )
        assert state.touch_need.deficit.last_update_source == original_source, (
            "Wrong event_type raise must not have cross-stamped last_update_source"
        )

    def test_ec39_stamp_sensor_envelope_accepts_reality_event(self):
        """EC-39 legal path: stamp_sensor() has can_write_memory=True (it represents
        a sensor assistant turn that has already produced a reply).  A Reality event
        under this envelope must be accepted and must mutate touch_need.deficit."""
        sensor_env = stamp_sensor()
        assert sensor_env.can_write_memory is True, (
            "stamp_sensor must have can_write_memory=True (sensor reply path)"
        )

        state = default_hidden_state()
        state.touch_need.deficit.value = 60.0

        state, result = integrate_event(
            RealityEventType.SEEK_COMPANIONSHIP, state, sensor_env, NOW
        )

        assert result.accepted, (
            "stamp_sensor envelope must allow a Reality event through — "
            "it is a legal write path"
        )
        assert state.touch_need.deficit.value < 60.0, (
            "Deficit must decrease under stamp_sensor envelope"
        )
        assert state.touch_need.deficit.last_update_source == UpdateSource.REALITY_BEHAVIOR
