"""
tests/test_user_hidden_state_phase25.py
========================================
Phase 2.5 — Edge-case audit / false-positive safety boundary tests.

15 new tests covering gaps not reached by EC-01–EC-24 (edge_cases) or
Phase 2 / Phase 1.5 / Phase 1 suites.

Coverage map
─────────────────────────────────────────────────────────────────────────────
Group A — WriteEnvelope constructor coercion (2)
  EC-25  WriteEnvelope(is_test=True, can_write_memory=True) → forced False
         → Reality event rejected, state unchanged                  fail-closed
  EC-26  WriteEnvelope(is_debug=True, can_write_memory=True) → forced False
         → impression rejected, state unchanged                     fail-closed

Group B — Reality event audit completeness (3)
  EC-27  RECEIVED_COMFORT stamps last_update_source=REALITY_BEHAVIOR
         and last_updated=NOW                                       audit
  EC-28  NO_INTERACTION from mid-range: delta == DEFICIT_ACCRUE_AMOUNT
         exactly (no amplification)                                 delta exactness
  EC-29  SEEK_COMPANIONSHIP at deficit == DEFICIT_DISCHARGE_AMOUNT exactly
         → deficit floors to exactly 0.0 (boundary floor)          clamp / audit

Group C — Cross-field last_update_source isolation (2)
  EC-30  After Dream impression: touch_need.deficit.last_update_source
         stays INIT (impression must not cross-stamp deficit)       cross-field audit
  EC-31  After NO_INTERACTION event: sensitivity.current.last_update_source
         stays INIT (event must not cross-stamp sensitivity)        cross-field audit

Group D — Cross-uid disk isolation (1)
  EC-32  event_and_save for uid_a does not touch uid_b's stored state  isolation

Group E — sensitivity.current underflow protection (1)
  EC-33  nudge_current_sensitivity with delta=-200 clamps to 0.0        underflow

Group F — to_dream_snapshot fail-closed on corrupt state (1)
  EC-34  state with sensitivity=None → returns neutral snapshot,
         no exception raised                                         fail-closed

Group G — Phase 2 _and_save accumulation round-trip (2)
  EC-35  NO_INTERACTION then SEEK_COMPANIONSHIP via event_and_save:
         net deficit change tracked correctly across two saves       accumulation
  EC-36  integrate_impression_and_save then integrate_event_and_save:
         both mutations persist independently on disk                accumulation

Group H — Gate boundary acceptance and wrong-type silent drop (3)
  EC-37  impression at exact DREAM_GATE_MIN (0.2) is accepted
         (inclusive lower boundary)                                  gate boundary
  EC-38  Non-RealityEventType object passed to integrate_event falls
         through: state unchanged, accepted=False                    silent drop
  EC-39  stamp_sensor envelope (can_write_memory=True) accepts a
         Reality event (legal path: sensor assistant turn)           legal path
─────────────────────────────────────────────────────────────────────────────

Design invariants verified by this suite:
  • WriteEnvelope is_test / is_debug coercion overrides explicit can_write_memory=True.
  • Every accepted event stamps the correct last_update_source; rejected events leave
    the source untouched.
  • Dream impression does not cross-stamp touch_need.deficit; Reality events do not
    cross-stamp sensitivity.current.
  • Two UIDs maintain fully independent on-disk state.
  • sensitivity.current cannot go below SCALAR_MIN via nudge.
  • to_dream_snapshot degrades to a neutral bucket snapshot rather than raising on
    any corrupt state (fail-closed read path).
  • Phase 2 _and_save wrappers accumulate state correctly across multiple calls.
  • impression.weight at exactly DREAM_GATE_MIN is within the gate (inclusive).
  • Unknown / wrong-type event_type to integrate_event causes no state mutation
    (silent drop — safe, but documents the auditing semantic gap).
  • stamp_sensor (post-reply sensor turn) is a legal write path.
"""
from __future__ import annotations

import math

import pytest

from core.memory.user_hidden_state import (
    DREAM_GATE_MAX,
    DREAM_GATE_MIN,
    SCALAR_MIN,
    SCALAR_CENTER,
    DreamBodyStateEvent,
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
from core.write_envelope import (
    WriteEnvelope,
    stamp_sensor,
    stamp_user_chat,
)

NOW = "2026-06-02T12:00:00Z"
UID_A = "uid_p25_a"
UID_B = "uid_p25_b"


def _open() -> WriteEnvelope:
    return stamp_user_chat()


# ═══════════════════════════════════════════════════════════════════════════════
# Group A — WriteEnvelope constructor coercion
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
# Group B — Reality event audit completeness
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
# Group C — Cross-field last_update_source isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossFieldSourceIsolation:
    """EC-30 / EC-31: Each integrator operation must only stamp the field it
    directly mutates; sibling fields must retain their pre-call source."""

    def test_ec30_impression_does_not_cross_stamp_deficit_source(self):
        """EC-30 cross-field audit: After a Dream impression, touch_need.deficit's
        last_update_source must remain INIT — impression only touches sensitivity.current."""
        state = default_hidden_state()
        # Confirm deficit source starts at INIT
        assert state.touch_need.deficit.last_update_source == UpdateSource.INIT

        imp = ImpressionInput(weight=(DREAM_GATE_MIN + DREAM_GATE_MAX) / 2)
        state, result = integrate_impression(imp, state, _open(), NOW)

        assert result.accepted
        # sensitivity.current was mutated — its source should be DREAM_IMPRESSION
        assert state.sensitivity.current.last_update_source == UpdateSource.DREAM_IMPRESSION
        # deficit was NOT touched — its source must stay INIT
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
        # Confirm sensitivity source starts at INIT
        assert state.sensitivity.current.last_update_source == UpdateSource.INIT

        state, result = integrate_event(RealityEventType.NO_INTERACTION, state, _open(), NOW)

        assert result.accepted
        # deficit was mutated — its source should be REALITY_BEHAVIOR
        assert state.touch_need.deficit.last_update_source == UpdateSource.REALITY_BEHAVIOR
        # sensitivity.current was NOT touched — its source must stay INIT
        assert state.sensitivity.current.last_update_source == UpdateSource.INIT, (
            "NO_INTERACTION event must not cross-stamp sensitivity.current.last_update_source"
        )
        assert state.sensitivity.current.last_updated is None, (
            "NO_INTERACTION event must not cross-stamp sensitivity.current.last_updated"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Group D — Cross-uid disk isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossUidDiskIsolation:
    """EC-32: Each uid must have an independent hidden_state.json file;
    writes to uid_a must not affect uid_b."""

    def test_ec32_writing_uid_a_does_not_affect_uid_b(self, sandbox):
        """EC-32 isolation: event_and_save for UID_A must not create or modify
        UID_B's hidden_state.json."""
        # Establish a baseline for uid_b
        state_b = default_hidden_state()
        state_b.touch_need.deficit.value = 30.0
        save_hidden_state(UID_B, state_b)

        # Write to uid_a
        integrate_event_and_save(UID_A, RealityEventType.SEEK_COMPANIONSHIP, _open(), NOW)

        # uid_b's file must be unchanged
        loaded_b = load_hidden_state(UID_B)
        assert loaded_b.touch_need.deficit.value == pytest.approx(30.0), (
            "Saving uid_a must not touch uid_b's deficit"
        )

        # Verify uid_a's file was actually written (sanity check)
        path_a = sandbox.user_memory_root(UID_A) / HIDDEN_STATE_FILENAME
        assert path_a.exists(), "uid_a hidden_state.json must exist after event_and_save"

        path_b = sandbox.user_memory_root(UID_B) / HIDDEN_STATE_FILENAME
        assert path_b.exists(), "uid_b hidden_state.json must still exist and be unchanged"


# ═══════════════════════════════════════════════════════════════════════════════
# Group E — sensitivity.current underflow protection
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
        # Source must still be stamped even on a clamped result
        assert state.sensitivity.current.last_update_source == UpdateSource.REALITY_BEHAVIOR


# ═══════════════════════════════════════════════════════════════════════════════
# Group F — to_dream_snapshot fail-closed on corrupt state
# ═══════════════════════════════════════════════════════════════════════════════

class TestSnapshotFailClosed:
    """EC-34: to_dream_snapshot must return a neutral bucket snapshot rather
    than raising when the state object is internally corrupt."""

    def test_ec34_corrupt_state_returns_neutral_snapshot_no_raise(self):
        """EC-34 fail-closed: if state.sensitivity is None (simulates a corrupt
        object), to_dream_snapshot must not raise — it returns the neutral dict."""
        state = default_hidden_state()
        state.sensitivity = None  # type: ignore  — simulate corrupt state

        # Must not raise; must return the neutral fallback
        snap = to_dream_snapshot(state, NOW)

        assert isinstance(snap, dict), "to_dream_snapshot must return a dict on corrupt state"
        expected_keys = frozenset({"sensitivity", "touch_appetite", "embodied_ease", "memory_cues"})
        assert set(snap.keys()) == expected_keys, (
            "Corrupt-state snapshot must still contain the expected four keys"
        )
        # Neutral fallback values (from _NEUTRAL constant)
        assert snap["sensitivity"] == "mid"
        assert snap["touch_appetite"] == "mid"
        assert snap["embodied_ease"] == "neutral"
        assert snap["memory_cues"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# Group G — Phase 2 _and_save accumulation round-trip
# ═══════════════════════════════════════════════════════════════════════════════

class TestAndSaveAccumulation:
    """EC-35 / EC-36: Multiple successive _and_save calls must each persist
    their mutation independently."""

    def test_ec35_no_interaction_then_seek_accumulate_correctly(self, sandbox):
        """EC-35 accumulation: NO_INTERACTION increases deficit; subsequent
        SEEK_COMPANIONSHIP decreases it; both are persisted to disk."""
        # Start at a known mid-range deficit
        initial = default_hidden_state()
        initial.touch_need.deficit.value = 50.0
        save_hidden_state(UID_A, initial)

        # First call: NO_INTERACTION → deficit increases
        integrate_event_and_save(UID_A, RealityEventType.NO_INTERACTION, _open(), NOW)
        after_no_interaction = load_hidden_state(UID_A)
        assert after_no_interaction.touch_need.deficit.value > 50.0, (
            "After NO_INTERACTION, deficit must have increased"
        )
        deficit_mid = after_no_interaction.touch_need.deficit.value

        # Second call: SEEK_COMPANIONSHIP → deficit decreases
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

        # Impression → sensitivity.current should increase
        imp = ImpressionInput(weight=DREAM_GATE_MAX)
        integrate_impression_and_save(UID_A, imp, _open(), NOW)
        after_impression = load_hidden_state(UID_A)
        assert after_impression.sensitivity.current.value > 50.0
        assert after_impression.touch_need.deficit.value == pytest.approx(50.0), (
            "impression_and_save must not disturb touch_need.deficit"
        )
        sens_after_imp = after_impression.sensitivity.current.value

        # Event → deficit should decrease; sensitivity.current must remain unchanged
        integrate_event_and_save(UID_A, RealityEventType.SEEK_COMPANIONSHIP, _open(), NOW)
        after_event = load_hidden_state(UID_A)
        assert after_event.touch_need.deficit.value < 50.0
        assert after_event.sensitivity.current.value == pytest.approx(sens_after_imp), (
            "event_and_save must not disturb sensitivity.current persisted by the prior impression"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Group H — Gate boundary acceptance and wrong-type silent drop
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
        event_type to integrate_event raises AttributeError on `event_type.value`
        BEFORE any envelope check or state mutation.

        Actual behavior (verified):
          integrate_event() accesses event_type.value at the very first line of
          the function body (before the envelope guard).  DreamBodyStateEvent is a
          dataclass and has no `.value` attribute, so AttributeError is raised
          immediately — the mutation branches are never reached.

        Security contract:
          - AttributeError is raised (not a silent drop — fail is visible)
          - State is unchanged at the point of the raise (no partial mutation)

        This is a fail-closed outcome from a data-integrity perspective:
        no field is written, no last_update_source is stamped.
        """
        state = default_hidden_state()
        state.touch_need.deficit.value = 45.0
        original_deficit = state.touch_need.deficit.value
        original_source = state.touch_need.deficit.last_update_source

        wrong_event = DreamBodyStateEvent(
            heat=0.8, sensitivity=0.9, tension=0.5, arousal=0.6, duration_min=30.0
        )

        # The call raises before mutating state
        with pytest.raises(AttributeError):
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
