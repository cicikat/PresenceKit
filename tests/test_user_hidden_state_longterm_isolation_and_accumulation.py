"""
tests/test_user_hidden_state_longterm_isolation_and_accumulation.py — 长期层
隔离守卫 + 跨字段/跨uid隔离审计 + 累积持久化 + 高频事件安全边界

合并自 test_user_hidden_state_edge_cases.py（EC-01–EC-24）+
test_user_hidden_state_phase25.py（EC-25–EC-39，与前者互补不重复，Brief 50 ·
工单E）。是与 test_user_hidden_state_envelope_and_gate_boundary.py 配对拆分的
另一半（长期层隔离/累积/审计主题，20个测试）。

Covers:
  - 高频/连续事件不腐化长期层字段、审计来源正确（原 EC-06–09）
  - Dream 印象直接尝试写长期字段一律无效（原 EC-19–22）
  - 弱证据 afterglow/impression 只产生小幅推动、长期层免疫（原 EC-23–24）
  - Reality 事件审计完整性：来源/时间戳/delta 精确值（原 EC-27–29）
  - 跨字段 last_update_source 隔离：impression 不串戳 deficit，event 不串戳 sensitivity（原 EC-30–31）
  - 跨 uid 磁盘隔离（原 EC-32）
  - sensitivity.current 下溢保护（原 EC-33）
  - to_dream_snapshot 对损坏 state 的 fail-closed 降级（原 EC-34）
  - Phase 2 _and_save 系列多次调用的累积持久化正确性（原 EC-35–36）
"""
from __future__ import annotations

import pytest

from core.memory.user_hidden_state import (
    BodyMemory,
    BodyMemoryEntry,
    DREAM_GATE_MAX,
    DREAM_GATE_MIN,
    SCALAR_MAX,
    SCALAR_MIN,
    SCALAR_CENTER,
    ImpressionInput,
    UpdateSource,
    default_hidden_state,
    nudge_current_sensitivity,
    to_dream_snapshot,
)
from core.memory.user_hidden_state_integrator import (
    DEFICIT_ACCRUE_AMOUNT,
    DEFICIT_DISCHARGE_AMOUNT,
    IMPRESSION_MAX_NUDGE,
    RealityEventType,
    integrate_event,
    integrate_event_and_save,
    integrate_impression,
    integrate_impression_and_save,
)
from core.memory.user_hidden_state_store import (
    HIDDEN_STATE_FILENAME,
    load_hidden_state,
    save_hidden_state,
)
from core.write_envelope import WriteEnvelope, stamp_user_chat

NOW = "2026-06-02T12:00:00Z"
UID_A = "uid_p25_a"
UID_B = "uid_p25_b"


def _open() -> WriteEnvelope:
    return stamp_user_chat()


def _long_term_snapshot(state):
    """Capture all four long-term field values for before/after comparison."""
    return (
        state.sensitivity.baseline.value,
        state.touch_need.baseline.value,
        state.embodied_ease.value,
        [e.cue for e in state.body_memory.entries],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 高频/连续事件不腐化长期层、审计来源正确（原 edge_cases EC-06–09）
# ═══════════════════════════════════════════════════════════════════════════════

class TestHighFrequencyNoBodyContact:
    """EC-06 – EC-09: Bursty events must not corrupt state."""

    def test_ec06_50_no_interaction_deficit_capped_at_scalar_max(self):
        """EC-06 clamp: 50 × NO_INTERACTION — deficit is capped at SCALAR_MAX."""
        state = default_hidden_state()
        state.touch_need.deficit.value = 0.0
        envelope = _open()

        for _ in range(50):
            state, _ = integrate_event(RealityEventType.NO_INTERACTION, state, envelope, NOW)

        assert state.touch_need.deficit.value <= SCALAR_MAX
        assert state.touch_need.deficit.value == pytest.approx(SCALAR_MAX), \
            "50 × accrue should saturate deficit at SCALAR_MAX"

    def test_ec07_100_seek_companionship_floored_at_zero(self):
        """EC-07 clamp: 100 × SEEK_COMPANIONSHIP from high deficit — never goes negative."""
        state = default_hidden_state()
        state.touch_need.deficit.value = 80.0
        envelope = _open()

        for _ in range(100):
            state, _ = integrate_event(RealityEventType.SEEK_COMPANIONSHIP, state, envelope, NOW)

        assert state.touch_need.deficit.value >= 0.0
        assert state.touch_need.deficit.value == pytest.approx(0.0), \
            "repeated discharge must floor deficit at 0"

    def test_ec08_consecutive_events_long_term_fields_never_touched(self):
        """EC-08 long-term guard: 20 mixed Reality events — all four long-term fields unchanged."""
        state = default_hidden_state()
        state.sensitivity.baseline.value = 55.0
        state.touch_need.baseline.value = 45.0
        state.embodied_ease.value = 60.0
        state.body_memory = BodyMemory(
            entries=[BodyMemoryEntry(cue="cue_a", weight=0.7, response_tag="r",
                                     created_at=NOW, last_reinforced=NOW)],
            max_entries=32,
        )
        baseline_before = _long_term_snapshot(state)
        envelope = _open()

        event_sequence = (
            [RealityEventType.SEEK_COMPANIONSHIP] * 7
            + [RealityEventType.NO_INTERACTION] * 7
            + [RealityEventType.RECEIVED_COMFORT] * 6
        )
        for event in event_sequence:
            state, _ = integrate_event(event, state, envelope, NOW)

        assert _long_term_snapshot(state) == baseline_before, \
            "20 mixed Reality events must not touch any long-term field"

    def test_ec09_no_interaction_audit_source_is_reality_behavior(self):
        """EC-09 audit: NO_INTERACTION sets last_update_source = REALITY_BEHAVIOR."""
        state = default_hidden_state()
        state.touch_need.deficit.value = 10.0

        state, result = integrate_event(RealityEventType.NO_INTERACTION, state, _open(), NOW)

        assert result.accepted
        assert state.touch_need.deficit.last_update_source == UpdateSource.REALITY_BEHAVIOR
        assert state.touch_need.deficit.last_updated == NOW


# ═══════════════════════════════════════════════════════════════════════════════
# Dream 印象直接尝试写长期字段一律无效（原 edge_cases EC-19–22）
# ═══════════════════════════════════════════════════════════════════════════════

class TestDreamDirectLongTermWrite:
    """EC-19 – EC-22: Dream-derived impressions must only touch sensitivity.current."""

    def _apply_impressions(self, n: int = 5):
        """Run n valid Dream impressions against a fresh state; return final state."""
        state = default_hidden_state()
        envelope = _open()
        mid_weight = (DREAM_GATE_MIN + DREAM_GATE_MAX) / 2
        imp = ImpressionInput(
            weight=mid_weight,
            emotional_tags=["warm"],
            impression_text="a close moment",
        )
        for _ in range(n):
            state, _ = integrate_impression(imp, state, envelope, NOW)
        return state

    def test_ec19_dream_impression_leaves_sensitivity_baseline_unchanged(self):
        """EC-19 long-term guard: Dream impression must not alter sensitivity.baseline."""
        original = default_hidden_state().sensitivity.baseline.value
        state = self._apply_impressions(5)
        assert state.sensitivity.baseline.value == pytest.approx(original), \
            "sensitivity.baseline is a long-term field; Dream impression must not touch it"

    def test_ec20_dream_impression_leaves_embodied_ease_unchanged(self):
        """EC-20 long-term guard: Dream impression must not alter embodied_ease."""
        original = default_hidden_state().embodied_ease.value
        state = self._apply_impressions(5)
        assert state.embodied_ease.value == pytest.approx(original), \
            "embodied_ease is a long-term constitution field; Dream impression must not touch it"

    def test_ec21_dream_impression_leaves_body_memory_unchanged(self):
        """EC-21 long-term guard: Dream impression must not add entries to body_memory."""
        state = self._apply_impressions(5)
        assert state.body_memory.entries == [], \
            "body_memory requires Reality corroboration (Phase 3+); Dream impression must not write it"

    def test_ec22_dream_impression_leaves_touch_need_baseline_unchanged(self):
        """EC-22 long-term guard: Dream impression must not alter touch_need.baseline."""
        original = default_hidden_state().touch_need.baseline.value
        state = self._apply_impressions(5)
        assert state.touch_need.baseline.value == pytest.approx(original), \
            "touch_need.baseline is a long-term field; Dream impression must not touch it"


# ═══════════════════════════════════════════════════════════════════════════════
# 弱证据 afterglow/impression 只产生小幅推动、长期层免疫（原 edge_cases EC-23–24）
# ═══════════════════════════════════════════════════════════════════════════════

class TestAfterglowImpressionBounded:
    """EC-23 – EC-24: Weak evidence produces weak nudge; long-term fields immune."""

    def test_ec23_mid_gate_impression_nudge_smaller_than_max(self):
        """EC-23 weak evidence: mid-gate weight gives proportionally small delta (< IMPRESSION_MAX_NUDGE).

        At weight=0.3 (mid of [0.2, 0.4]):
          ratio = (0.3 − 0.2) / (0.4 − 0.2) = 0.5
          delta = 0.5 × IMPRESSION_MAX_NUDGE = 1.5  (NOT the full 3.0)

        This verifies that weak dream evidence only weakly pushes mid-term state.
        """
        state = default_hidden_state()
        state.sensitivity.current.value = 50.0
        mid_weight = (DREAM_GATE_MIN + DREAM_GATE_MAX) / 2  # 0.3

        imp = ImpressionInput(weight=mid_weight)
        state, result = integrate_impression(imp, state, _open(), NOW)

        assert result.accepted
        delta = result.touched_fields[0].new_value - result.touched_fields[0].old_value
        expected_delta = ((mid_weight - DREAM_GATE_MIN) / (DREAM_GATE_MAX - DREAM_GATE_MIN)) * IMPRESSION_MAX_NUDGE
        assert delta == pytest.approx(expected_delta, abs=1e-6), \
            f"mid-gate nudge must be {expected_delta:.3f}, not the full {IMPRESSION_MAX_NUDGE}"
        assert delta < IMPRESSION_MAX_NUDGE, \
            "mid-gate impression must give less than IMPRESSION_MAX_NUDGE"

    def test_ec24_multiple_impressions_only_move_sensitivity_current(self):
        """EC-24 long-term guard: 5 valid Dream impressions accumulate in sensitivity.current only.

        Verifies:
          - sensitivity.current increases (mid-term layer is responsive)
          - All four long-term fields remain at default values
          - touch_need.deficit is unaffected (cross-field isolation)
        """
        state = default_hidden_state()
        long_term_before = _long_term_snapshot(state)
        original_deficit = state.touch_need.deficit.value
        envelope = _open()
        max_weight = DREAM_GATE_MAX
        imp = ImpressionInput(weight=max_weight)

        for _ in range(5):
            state, result = integrate_impression(imp, state, envelope, NOW)
            assert result.accepted

        # 中期层: sensitivity.current should have increased
        assert state.sensitivity.current.value > SCALAR_CENTER, \
            "sensitivity.current (中期层) must accumulate across repeated impressions"
        assert state.sensitivity.current.value <= SCALAR_MAX

        # 中期层: touch_need.deficit must be untouched by impression
        assert state.touch_need.deficit.value == pytest.approx(original_deficit), \
            "impression must not affect touch_need.deficit"

        # 长期层: all four long-term fields unchanged
        assert _long_term_snapshot(state) == long_term_before, \
            "5 Dream impressions must not touch any long-term field"

        # Audit: update source on sensitivity.current is DREAM_IMPRESSION
        assert state.sensitivity.current.last_update_source == UpdateSource.DREAM_IMPRESSION


# ═══════════════════════════════════════════════════════════════════════════════
# Reality 事件审计完整性：来源/时间戳/delta 精确值（原 phase25 EC-27–29）
# ═══════════════════════════════════════════════════════════════════════════════

class TestRealityEventAudit:
    """EC-27 / EC-28 / EC-29: Accepted events must stamp provenance correctly;
    delta magnitude must not exceed the declared constant."""

    def test_ec27_received_comfort_stamps_source_and_timestamp(self):
        """EC-27 audit: RECEIVED_COMFORT must stamp
        last_update_source=REALITY_BEHAVIOR and last_updated=NOW."""
        state = default_hidden_state()
        state.touch_need.deficit.value = 50.0

        state, result = integrate_event(RealityEventType.RECEIVED_COMFORT, state, _open(), NOW)

        assert result.accepted
        assert state.touch_need.deficit.last_update_source == UpdateSource.REALITY_BEHAVIOR, (
            "RECEIVED_COMFORT must stamp REALITY_BEHAVIOR, not any Dream-derived source"
        )
        assert state.touch_need.deficit.last_updated == NOW, (
            "RECEIVED_COMFORT must stamp last_updated = NOW"
        )

    def test_ec28_no_interaction_delta_is_exactly_accrue_amount(self):
        """EC-28 delta exactness: NO_INTERACTION from a mid-range deficit
        must add exactly DEFICIT_ACCRUE_AMOUNT — no more, no less."""
        state = default_hidden_state()
        state.touch_need.deficit.value = 50.0  # well inside [0, 100], no clamp

        state, result = integrate_event(RealityEventType.NO_INTERACTION, state, _open(), NOW)

        assert result.accepted
        delta = result.touched_fields[0]
        actual_increase = delta.new_value - delta.old_value
        assert actual_increase == pytest.approx(DEFICIT_ACCRUE_AMOUNT), (
            f"NO_INTERACTION must add exactly DEFICIT_ACCRUE_AMOUNT={DEFICIT_ACCRUE_AMOUNT}, "
            f"got {actual_increase}"
        )

    def test_ec29_seek_at_exact_discharge_amount_floors_to_zero(self):
        """EC-29 clamp: SEEK_COMPANIONSHIP when deficit == DEFICIT_DISCHARGE_AMOUNT
        must floor deficit to exactly 0.0 (boundary floor, not negative)."""
        state = default_hidden_state()
        state.touch_need.deficit.value = DEFICIT_DISCHARGE_AMOUNT  # exactly 8.0

        state, result = integrate_event(RealityEventType.SEEK_COMPANIONSHIP, state, _open(), NOW)

        assert result.accepted
        assert state.touch_need.deficit.value == pytest.approx(0.0), (
            "deficit == DEFICIT_DISCHARGE_AMOUNT after SEEK must floor to exactly 0.0"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 跨字段 last_update_source 隔离（原 phase25 EC-30–31）
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossFieldSourceIsolation:
    """EC-30 / EC-31: Each integrator operation must only stamp the field it
    directly mutates; sibling fields must retain their pre-call source."""

    def test_ec30_impression_does_not_cross_stamp_deficit_source(self):
        """EC-30 cross-field audit: After a Dream impression, touch_need.deficit's
        last_update_source must remain INIT — impression only touches sensitivity.current."""
        state = default_hidden_state()
        assert state.touch_need.deficit.last_update_source == UpdateSource.INIT

        imp = ImpressionInput(weight=(DREAM_GATE_MIN + DREAM_GATE_MAX) / 2)
        state, result = integrate_impression(imp, state, _open(), NOW)

        assert result.accepted
        assert state.sensitivity.current.last_update_source == UpdateSource.DREAM_IMPRESSION
        assert state.touch_need.deficit.last_update_source == UpdateSource.INIT, (
            "Dream impression must not cross-stamp touch_need.deficit.last_update_source"
        )
        assert state.touch_need.deficit.last_updated is None, (
            "Dream impression must not cross-stamp touch_need.deficit.last_updated"
        )

    def test_ec31_reality_event_does_not_cross_stamp_sensitivity_source(self):
        """EC-31 cross-field audit: After a NO_INTERACTION event, sensitivity.current's
        last_update_source must remain INIT — Reality event only touches touch_need.deficit."""
        state = default_hidden_state()
        assert state.sensitivity.current.last_update_source == UpdateSource.INIT

        state, result = integrate_event(RealityEventType.NO_INTERACTION, state, _open(), NOW)

        assert result.accepted
        assert state.touch_need.deficit.last_update_source == UpdateSource.REALITY_BEHAVIOR
        assert state.sensitivity.current.last_update_source == UpdateSource.INIT, (
            "NO_INTERACTION event must not cross-stamp sensitivity.current.last_update_source"
        )
        assert state.sensitivity.current.last_updated is None, (
            "NO_INTERACTION event must not cross-stamp sensitivity.current.last_updated"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 跨 uid 磁盘隔离（原 phase25 EC-32）
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossUidDiskIsolation:
    """EC-32: Each uid must have an independent hidden_state.json file;
    writes to uid_a must not affect uid_b."""

    def test_ec32_writing_uid_a_does_not_affect_uid_b(self, sandbox):
        """EC-32 isolation: event_and_save for UID_A must not create or modify
        UID_B's hidden_state.json."""
        state_b = default_hidden_state()
        state_b.touch_need.deficit.value = 30.0
        save_hidden_state(UID_B, state_b)

        integrate_event_and_save(UID_A, RealityEventType.SEEK_COMPANIONSHIP, _open(), NOW)

        loaded_b = load_hidden_state(UID_B)
        assert loaded_b.touch_need.deficit.value == pytest.approx(30.0), (
            "Saving uid_a must not touch uid_b's deficit"
        )

        path_a = sandbox.user_memory_root(UID_A) / HIDDEN_STATE_FILENAME
        assert path_a.exists(), "uid_a hidden_state.json must exist after event_and_save"

        path_b = sandbox.user_memory_root(UID_B) / HIDDEN_STATE_FILENAME
        assert path_b.exists(), "uid_b hidden_state.json must still exist and be unchanged"


# ═══════════════════════════════════════════════════════════════════════════════
# sensitivity.current 下溢保护（原 phase25 EC-33）
# ═══════════════════════════════════════════════════════════════════════════════

class TestSensitivityUnderflow:
    """EC-33: nudge_current_sensitivity with a very large negative delta must
    clamp to SCALAR_MIN (0.0) and never go negative."""

    def test_ec33_large_negative_nudge_clamps_to_scalar_min(self):
        """EC-33 underflow: nudge_current_sensitivity with delta=-200 must clamp
        sensitivity.current to SCALAR_MIN=0.0."""
        state = default_hidden_state()
        state.sensitivity.current.value = SCALAR_CENTER  # 50.0

        state = nudge_current_sensitivity(state, -200.0, UpdateSource.REALITY_BEHAVIOR, NOW)

        assert state.sensitivity.current.value == pytest.approx(SCALAR_MIN), (
            f"Large negative nudge must clamp to SCALAR_MIN={SCALAR_MIN}, "
            f"got {state.sensitivity.current.value}"
        )
        assert state.sensitivity.current.value >= SCALAR_MIN, (
            "sensitivity.current must never go below SCALAR_MIN"
        )
        assert state.sensitivity.current.last_update_source == UpdateSource.REALITY_BEHAVIOR


# ═══════════════════════════════════════════════════════════════════════════════
# to_dream_snapshot 对损坏 state 的 fail-closed 降级（原 phase25 EC-34）
# ═══════════════════════════════════════════════════════════════════════════════

class TestSnapshotFailClosed:
    """EC-34: to_dream_snapshot must return a neutral bucket snapshot rather
    than raising when the state object is internally corrupt."""

    def test_ec34_corrupt_state_returns_neutral_snapshot_no_raise(self):
        """EC-34 fail-closed: if state.sensitivity is None (simulates a corrupt
        object), to_dream_snapshot must not raise — it returns the neutral dict."""
        state = default_hidden_state()
        state.sensitivity = None  # type: ignore  — simulate corrupt state

        snap = to_dream_snapshot(state, NOW)

        assert isinstance(snap, dict), "to_dream_snapshot must return a dict on corrupt state"
        expected_keys = frozenset({"sensitivity", "touch_appetite", "embodied_ease", "memory_cues"})
        assert set(snap.keys()) == expected_keys, (
            "Corrupt-state snapshot must still contain the expected four keys"
        )
        assert snap["sensitivity"] == "mid"
        assert snap["touch_appetite"] == "mid"
        assert snap["embodied_ease"] == "neutral"
        assert snap["memory_cues"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 _and_save 系列多次调用的累积持久化正确性（原 phase25 EC-35–36）
# ═══════════════════════════════════════════════════════════════════════════════

class TestAndSaveAccumulation:
    """EC-35 / EC-36: Multiple successive _and_save calls must each persist
    their mutation independently."""

    def test_ec35_no_interaction_then_seek_accumulate_correctly(self, sandbox):
        """EC-35 accumulation: NO_INTERACTION increases deficit; subsequent
        SEEK_COMPANIONSHIP decreases it; both are persisted to disk."""
        initial = default_hidden_state()
        initial.touch_need.deficit.value = 50.0
        save_hidden_state(UID_A, initial)

        integrate_event_and_save(UID_A, RealityEventType.NO_INTERACTION, _open(), NOW)
        after_no_interaction = load_hidden_state(UID_A)
        assert after_no_interaction.touch_need.deficit.value > 50.0, (
            "After NO_INTERACTION, deficit must have increased"
        )
        deficit_mid = after_no_interaction.touch_need.deficit.value

        integrate_event_and_save(UID_A, RealityEventType.SEEK_COMPANIONSHIP, _open(), NOW)
        after_seek = load_hidden_state(UID_A)
        assert after_seek.touch_need.deficit.value < deficit_mid, (
            "After SEEK_COMPANIONSHIP, deficit must have decreased from the post-NO_INTERACTION level"
        )

    def test_ec36_impression_and_save_then_event_and_save_persist_independently(self, sandbox):
        """EC-36 accumulation: impression_and_save moves sensitivity.current;
        a subsequent event_and_save moves touch_need.deficit; both persist
        without disturbing each other's field."""
        initial = default_hidden_state()
        initial.sensitivity.current.value = 50.0
        initial.touch_need.deficit.value = 50.0
        save_hidden_state(UID_A, initial)

        imp = ImpressionInput(weight=DREAM_GATE_MAX)
        integrate_impression_and_save(UID_A, imp, _open(), NOW)
        after_impression = load_hidden_state(UID_A)
        assert after_impression.sensitivity.current.value > 50.0
        assert after_impression.touch_need.deficit.value == pytest.approx(50.0), (
            "impression_and_save must not disturb touch_need.deficit"
        )
        sens_after_imp = after_impression.sensitivity.current.value

        integrate_event_and_save(UID_A, RealityEventType.SEEK_COMPANIONSHIP, _open(), NOW)
        after_event = load_hidden_state(UID_A)
        assert after_event.touch_need.deficit.value < 50.0
        assert after_event.sensitivity.current.value == pytest.approx(sens_after_imp), (
            "event_and_save must not disturb sensitivity.current persisted by the prior impression"
        )
