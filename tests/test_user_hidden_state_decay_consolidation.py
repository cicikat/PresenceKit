"""
tests/test_user_hidden_state_decay_consolidation.py — 长期层时间衰减 + baseline 固化

从 test_user_hidden_state_phase3.py 拆出（Brief 50 · 工单E，682行超过500行
上限，按 Group 边界拆为本文件 + test_user_hidden_state_body_memory_and_guards.py）。

来源：Phase 3 — Long-term layer activation, scheduler wiring, type guards.

Covers:
  A  apply_time_decay       (9)  AT-01–AT-09
  B  consolidate_baselines  (5)  CB-01–CB-05
"""
from __future__ import annotations

import math
import pytest

from core.memory.user_hidden_state import (
    BASELINE_LEARN_RATE,
    CURRENT_SENS_REGRESS_HL_DAYS,
    EMBODIED_EASE_CENTER_HL_DAYS,
    MEMORY_EXTINCTION_HL_DAYS,
    SCALAR_CENTER,
    TOUCH_DEFICIT_DECAY_HL_DAYS,
    BodyMemoryEntry,
    UpdateSource,
    UserHiddenState,
    apply_time_decay,
    consolidate_baselines,
    default_hidden_state,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

NOW   = "2026-06-02T12:00:00Z"
LATER = "2026-06-07T12:00:00Z"   # 5 days later
D10   = "2026-06-12T12:00:00Z"   # 10 days later
D45   = "2026-07-17T12:00:00Z"   # 45 days later
D90   = "2026-08-31T12:00:00Z"   # 90 days later


def _state_with_decay_tick(tick: str | None = NOW) -> UserHiddenState:
    s = default_hidden_state()
    s.last_decay_tick = tick
    return s


def _half_life_expected(start: float, target: float, elapsed: float, hl: float) -> float:
    """Expected value after exponential regression for one half-life interval."""
    factor = math.pow(0.5, elapsed / hl) if hl > 0 and elapsed > 0 else 1.0
    return start + (target - start) * (1.0 - factor)


# ═══════════════════════════════════════════════════════════════════════════════
# Group A — apply_time_decay
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyTimeDecay:

    def test_at01_first_run_no_value_change_tick_updated(self):
        """AT-01: last_decay_tick=None → all scalar values unchanged, last_decay_tick set."""
        s = default_hidden_state()
        s.sensitivity.current.value = 70.0
        s.touch_need.deficit.value = 30.0
        s.embodied_ease.value = 60.0
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="touch", response_tag="warm", weight=0.8,
                            created_at=NOW, last_reinforced=NOW)
        )
        assert s.last_decay_tick is None

        result = apply_time_decay(s, NOW)

        assert result.sensitivity.current.value == pytest.approx(70.0), "first-run must not change current"
        assert result.touch_need.deficit.value == pytest.approx(30.0), "first-run must not change deficit"
        assert result.embodied_ease.value == pytest.approx(60.0), "first-run must not change embodied_ease"
        assert result.body_memory.entries[0].weight == pytest.approx(0.8), "first-run must not change weight"
        assert result.last_decay_tick == NOW, "last_decay_tick must be set to now"

    def test_at02_zero_elapsed_no_value_change(self):
        """AT-02: elapsed=0.0 (just ticked) → scalar values unchanged."""
        s = _state_with_decay_tick(NOW)
        s.sensitivity.current.value = 70.0
        s.touch_need.deficit.value = 30.0

        result = apply_time_decay(s, NOW)

        assert result.sensitivity.current.value == pytest.approx(70.0)
        assert result.touch_need.deficit.value == pytest.approx(30.0)

    def test_at03_sensitivity_current_regresses_toward_baseline(self):
        """AT-03: elapsed=5d (1× CURRENT_SENS_REGRESS_HL=5) → current moves ~50% toward baseline."""
        s = _state_with_decay_tick(NOW)
        s.sensitivity.current.value = 80.0
        s.sensitivity.baseline.value = 50.0  # SCALAR_CENTER

        result = apply_time_decay(s, LATER)

        expected = _half_life_expected(80.0, 50.0, 5.0, CURRENT_SENS_REGRESS_HL_DAYS)
        assert result.sensitivity.current.value == pytest.approx(expected, rel=1e-4)
        assert result.sensitivity.current.value < 80.0
        assert result.sensitivity.current.value > 50.0

    def test_at04_touch_deficit_regresses_toward_zero(self):
        """AT-04: elapsed=10d (1× TOUCH_DEFICIT_DECAY_HL=10) → deficit moves ~50% toward 0."""
        s = _state_with_decay_tick(NOW)
        s.touch_need.deficit.value = 80.0

        result = apply_time_decay(s, D10)

        expected = _half_life_expected(80.0, 0.0, 10.0, TOUCH_DEFICIT_DECAY_HL_DAYS)
        assert result.touch_need.deficit.value == pytest.approx(expected, rel=1e-4)
        assert result.touch_need.deficit.value < 80.0

    def test_at05_embodied_ease_regresses_toward_center(self):
        """AT-05: elapsed=90d (1× EMBODIED_EASE_CENTER_HL=90) → ease moves ~50% toward SCALAR_CENTER."""
        s = _state_with_decay_tick(NOW)
        s.embodied_ease.value = 80.0

        result = apply_time_decay(s, D90)

        expected = _half_life_expected(80.0, SCALAR_CENTER, 90.0, EMBODIED_EASE_CENTER_HL_DAYS)
        assert result.embodied_ease.value == pytest.approx(expected, rel=1e-4)
        assert result.embodied_ease.value < 80.0

    def test_at06_body_memory_weight_decays(self):
        """AT-06: elapsed=45d (1× MEMORY_EXTINCTION_HL=45) → weight ≈ original × 0.5."""
        s = _state_with_decay_tick(NOW)
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="hug", response_tag="calm", weight=0.8,
                            created_at=NOW, last_reinforced=NOW)
        )

        result = apply_time_decay(s, D45)

        expected = _half_life_expected(0.8, 0.0, 45.0, MEMORY_EXTINCTION_HL_DAYS)
        assert result.body_memory.entries[0].weight == pytest.approx(expected, rel=1e-4)
        assert result.body_memory.entries[0].weight < 0.8

    def test_at07_last_decay_tick_updated(self):
        """AT-07: after decay, last_decay_tick equals the `now` argument."""
        s = _state_with_decay_tick(NOW)

        result = apply_time_decay(s, LATER)

        assert result.last_decay_tick == LATER

    def test_at08_clock_rollback_no_decay(self):
        """AT-08: last_decay_tick in the future (clock rollback) → elapsed=0 → no decay."""
        s = _state_with_decay_tick(LATER)   # tick is AFTER now
        s.sensitivity.current.value = 80.0
        s.touch_need.deficit.value = 60.0

        result = apply_time_decay(s, NOW)   # now is BEFORE tick

        assert result.sensitivity.current.value == pytest.approx(80.0)
        assert result.touch_need.deficit.value == pytest.approx(60.0)

    def test_at09_decay_does_not_evict_body_memory_entries(self):
        """AT-09: decay lowers weights but does NOT remove entries."""
        s = _state_with_decay_tick(NOW)
        for i in range(5):
            s.body_memory.entries.append(
                BodyMemoryEntry(cue=f"cue_{i}", response_tag="tag", weight=0.8,
                                created_at=NOW, last_reinforced=NOW)
            )

        result = apply_time_decay(s, D45)

        assert len(result.body_memory.entries) == 5, "decay must not evict entries"
        for entry in result.body_memory.entries:
            assert entry.weight < 0.8, "weights should have decayed"


# ═══════════════════════════════════════════════════════════════════════════════
# Group B — consolidate_baselines
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsolidateBaselines:

    def test_cb01_sensitivity_baseline_pushed_toward_center(self):
        """CB-01: sensitivity.baseline above center → nudged toward SCALAR_CENTER."""
        s = default_hidden_state()
        s.sensitivity.baseline.value = 80.0

        result = consolidate_baselines(s, NOW)

        expected = 80.0 + BASELINE_LEARN_RATE * (SCALAR_CENTER - 80.0)
        assert result.sensitivity.baseline.value == pytest.approx(expected, rel=1e-6)
        assert result.sensitivity.baseline.value < 80.0

    def test_cb02_touch_baseline_pushed_toward_center(self):
        """CB-02: touch_need.baseline below center → nudged toward SCALAR_CENTER."""
        s = default_hidden_state()
        s.touch_need.baseline.value = 20.0

        result = consolidate_baselines(s, NOW)

        expected = 20.0 + BASELINE_LEARN_RATE * (SCALAR_CENTER - 20.0)
        assert result.touch_need.baseline.value == pytest.approx(expected, rel=1e-6)
        assert result.touch_need.baseline.value > 20.0

    def test_cb03_already_at_center_no_change(self):
        """CB-03: both baselines already at SCALAR_CENTER → consolidate is a no-op on values."""
        s = default_hidden_state()
        # default_hidden_state sets both baselines to SCALAR_CENTER
        assert s.sensitivity.baseline.value == SCALAR_CENTER
        assert s.touch_need.baseline.value == SCALAR_CENTER

        result = consolidate_baselines(s, NOW)

        assert result.sensitivity.baseline.value == pytest.approx(SCALAR_CENTER)
        assert result.touch_need.baseline.value == pytest.approx(SCALAR_CENTER)

    def test_cb04_does_not_touch_midterm_or_body_memory(self):
        """CB-04: consolidate must not touch sensitivity.current, deficit, embodied_ease, body_memory."""
        s = default_hidden_state()
        s.sensitivity.current.value = 75.0
        s.touch_need.deficit.value = 40.0
        s.embodied_ease.value = 60.0
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="hug", response_tag="calm", weight=0.5,
                            created_at=NOW, last_reinforced=NOW)
        )

        result = consolidate_baselines(s, NOW)

        assert result.sensitivity.current.value == pytest.approx(75.0)
        assert result.touch_need.deficit.value == pytest.approx(40.0)
        assert result.embodied_ease.value == pytest.approx(60.0)
        assert len(result.body_memory.entries) == 1
        assert result.body_memory.entries[0].weight == pytest.approx(0.5)

    def test_cb05_source_stamped_as_consolidation(self):
        """CB-05: after consolidate, last_update_source on both baselines is CONSOLIDATION."""
        s = default_hidden_state()
        s.sensitivity.baseline.value = 70.0
        s.touch_need.baseline.value = 30.0

        result = consolidate_baselines(s, NOW)

        assert result.sensitivity.baseline.last_update_source == UpdateSource.CONSOLIDATION
        assert result.touch_need.baseline.last_update_source == UpdateSource.CONSOLIDATION
        assert result.sensitivity.baseline.last_updated == NOW
        assert result.touch_need.baseline.last_updated == NOW
