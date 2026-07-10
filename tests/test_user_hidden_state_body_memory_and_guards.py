"""
tests/test_user_hidden_state_body_memory_and_guards.py — body_memory 强化 + 触摸
需求累积 + body_cue 集成 + 调度器 helper + 类型守卫

从 test_user_hidden_state_phase3.py 拆出（Brief 50 · 工单E，682行超过500行
上限，按 Group 边界拆为 test_user_hidden_state_decay_consolidation.py + 本文件）。

来源：Phase 3 — Long-term layer activation, scheduler wiring, type guards.

Covers:
  C  reinforce_body_memory  (10) RM-01–RM-10
  D  nudge_embodied_ease    (4)  EE-01–EE-04
  E  accrue_touch_deficit   (3)  TD-01–TD-03
  F  integrate_body_cue     (4)  BC-01–BC-04
  G  scheduler helpers      (4)  SC-01–SC-04
  H  type guards            (5)  TG-01–TG-05
"""
from __future__ import annotations

import pytest

from core.memory.user_hidden_state import (
    MAX_NUDGE_PER_EVENT,
    MEMORY_EVICT_EPS,
    SCALAR_MAX,
    SCALAR_MIN,
    TOUCH_DEFICIT_DECAY_HL_DAYS,
    WEIGHT_MAX,
    BodyMemoryEntry,
    DreamBodyStateEvent,
    UpdateSource,
    accrue_touch_deficit,
    apply_time_decay,
    default_hidden_state,
    nudge_embodied_ease,
    reinforce_body_memory,
)
from core.memory.user_hidden_state_integrator import (
    RealityEventType,
    integrate_body_cue,
    integrate_body_cue_and_save,
    integrate_event,
    integrate_event_and_save,
)
from core.memory.user_hidden_state_store import load_hidden_state
from core.write_envelope import stamp_debug, stamp_trigger, stamp_user_chat

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

NOW   = "2026-06-02T12:00:00Z"
LATER = "2026-06-07T12:00:00Z"   # 5 days later


def _open():
    return stamp_user_chat()


def _closed():
    return stamp_debug()


# ═══════════════════════════════════════════════════════════════════════════════
# Group C — reinforce_body_memory
# ═══════════════════════════════════════════════════════════════════════════════

class TestReinforceBodyMemory:
    SRC = UpdateSource.REALITY_BEHAVIOR

    def test_rm01_new_cue_appended(self):
        """RM-01: empty memory + new cue → entry appended, weight = strength."""
        s = default_hidden_state()

        reinforce_body_memory(s, "hug", "calm", 0.5, self.SRC, NOW)

        assert len(s.body_memory.entries) == 1
        assert s.body_memory.entries[0].cue == "hug"
        assert s.body_memory.entries[0].weight == pytest.approx(0.5)

    def test_rm02_existing_cue_hebbian_strengthen(self):
        """RM-02: existing cue → Hebbian: new_weight = old + strength × (1 - old)."""
        s = default_hidden_state()
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="hug", response_tag="calm", weight=0.4,
                            created_at=NOW, last_reinforced=NOW)
        )

        reinforce_body_memory(s, "hug", "calm", 0.5, self.SRC, LATER)

        expected = 0.4 + 0.5 * (WEIGHT_MAX - 0.4)
        assert s.body_memory.entries[0].weight == pytest.approx(expected, rel=1e-6)

    def test_rm03_existing_cue_last_reinforced_updated(self):
        """RM-03: existing cue → last_reinforced updated to now."""
        s = default_hidden_state()
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="hug", response_tag="calm", weight=0.4,
                            created_at=NOW, last_reinforced=NOW)
        )

        reinforce_body_memory(s, "hug", "calm", 0.5, self.SRC, LATER)

        assert s.body_memory.entries[0].last_reinforced == LATER

    def test_rm04_existing_cue_response_tag_updated(self):
        """RM-04: existing cue → response_tag overwritten with the new value."""
        s = default_hidden_state()
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="hug", response_tag="old_tag", weight=0.4,
                            created_at=NOW, last_reinforced=NOW)
        )

        reinforce_body_memory(s, "hug", "new_tag", 0.5, self.SRC, LATER)

        assert s.body_memory.entries[0].response_tag == "new_tag"

    def test_rm05_empty_cue_noop(self):
        """RM-05: empty string cue → no-op, no entry added."""
        s = default_hidden_state()

        reinforce_body_memory(s, "", "calm", 0.5, self.SRC, NOW)

        assert len(s.body_memory.entries) == 0

    def test_rm06_whitespace_cue_noop(self):
        """RM-06: whitespace-only cue → normalized to empty → no-op."""
        s = default_hidden_state()

        reinforce_body_memory(s, "   ", "calm", 0.5, self.SRC, NOW)

        assert len(s.body_memory.entries) == 0

    def test_rm07_full_capacity_evicts_weakest(self):
        """RM-07: at max_entries + weak entry below MEMORY_EVICT_EPS → weakest evicted, new cue added."""
        s = default_hidden_state()
        s.body_memory.max_entries = 3

        # Fill to capacity with healthy weights
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="a", response_tag="t", weight=0.8, created_at=NOW, last_reinforced=NOW)
        )
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="b", response_tag="t", weight=0.01, created_at=NOW, last_reinforced=NOW)  # evictable
        )
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="c", response_tag="t", weight=0.6, created_at=NOW, last_reinforced=NOW)
        )

        reinforce_body_memory(s, "d", "t", 0.5, self.SRC, LATER)

        cues = {e.cue for e in s.body_memory.entries}
        assert len(s.body_memory.entries) == 3
        assert "b" not in cues, "weakest evictable entry should have been removed"
        assert "d" in cues, "new entry should have been added"

    def test_rm08_full_capacity_no_weak_entry_silent_drop(self):
        """RM-08: at max_entries, no entry below MEMORY_EVICT_EPS → new cue silently dropped."""
        s = default_hidden_state()
        s.body_memory.max_entries = 2

        s.body_memory.entries.append(
            BodyMemoryEntry(cue="a", response_tag="t", weight=0.9, created_at=NOW, last_reinforced=NOW)
        )
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="b", response_tag="t", weight=0.8, created_at=NOW, last_reinforced=NOW)
        )

        reinforce_body_memory(s, "c", "t", 0.5, self.SRC, LATER)

        assert len(s.body_memory.entries) == 2
        cues = {e.cue for e in s.body_memory.entries}
        assert "c" not in cues, "new cue should be silently dropped when no evictable slot"

    def test_rm09_weight_never_exceeds_weight_max(self):
        """RM-09: repeated reinforcement → weight stays ≤ WEIGHT_MAX."""
        s = default_hidden_state()
        s.body_memory.entries.append(
            BodyMemoryEntry(cue="hug", response_tag="calm", weight=0.99,
                            created_at=NOW, last_reinforced=NOW)
        )

        for _ in range(20):
            reinforce_body_memory(s, "hug", "calm", 1.0, self.SRC, LATER)

        assert s.body_memory.entries[0].weight <= WEIGHT_MAX

    def test_rm10_zero_strength_new_cue_added(self):
        """RM-10: strength=0.0 new cue → entry added with weight=0.0."""
        s = default_hidden_state()

        reinforce_body_memory(s, "hug", "calm", 0.0, self.SRC, NOW)

        assert len(s.body_memory.entries) == 1
        assert s.body_memory.entries[0].weight == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Group D — nudge_embodied_ease
# ═══════════════════════════════════════════════════════════════════════════════

class TestNudgeEmbodiedEase:
    SRC = UpdateSource.REALITY_BEHAVIOR

    def test_ee01_normal_positive_delta(self):
        """EE-01: positive delta → embodied_ease increases correctly."""
        s = default_hidden_state()
        s.embodied_ease.value = 50.0

        result = nudge_embodied_ease(s, 5.0, self.SRC, NOW)

        assert result.embodied_ease.value == pytest.approx(55.0)
        assert result.embodied_ease.last_update_source == self.SRC

    def test_ee02_delta_clamped_at_max_nudge(self):
        """EE-02: delta > MAX_NUDGE_PER_EVENT → clamped to MAX_NUDGE_PER_EVENT before adding."""
        s = default_hidden_state()
        s.embodied_ease.value = 50.0
        huge_delta = MAX_NUDGE_PER_EVENT * 10

        result = nudge_embodied_ease(s, huge_delta, self.SRC, NOW)

        assert result.embodied_ease.value == pytest.approx(50.0 + MAX_NUDGE_PER_EVENT)

    def test_ee03_large_positive_delta_clamped_at_scalar_max(self):
        """EE-03: value + clamped_delta > SCALAR_MAX → result clamped at SCALAR_MAX."""
        s = default_hidden_state()
        s.embodied_ease.value = SCALAR_MAX - 1.0

        result = nudge_embodied_ease(s, MAX_NUDGE_PER_EVENT, self.SRC, NOW)

        assert result.embodied_ease.value <= SCALAR_MAX

    def test_ee04_large_negative_delta_clamped_at_scalar_min(self):
        """EE-04: large negative delta → result clamped at SCALAR_MIN."""
        s = default_hidden_state()
        s.embodied_ease.value = 1.0

        result = nudge_embodied_ease(s, -MAX_NUDGE_PER_EVENT, self.SRC, NOW)

        assert result.embodied_ease.value >= SCALAR_MIN


# ═══════════════════════════════════════════════════════════════════════════════
# Group E — accrue_touch_deficit
# ═══════════════════════════════════════════════════════════════════════════════

class TestAccrueTouchDeficit:

    def test_td01_normal_accrual_one_day(self):
        """TD-01: elapsed_days=1.0 → deficit increases by SCALAR_MAX / TOUCH_DEFICIT_DECAY_HL_DAYS."""
        s = default_hidden_state()
        s.touch_need.deficit.value = 0.0
        expected_delta = SCALAR_MAX / TOUCH_DEFICIT_DECAY_HL_DAYS

        result = accrue_touch_deficit(s, 1.0, NOW)

        assert result.touch_need.deficit.value == pytest.approx(expected_delta, rel=1e-6)
        assert result.touch_need.deficit.last_update_source == UpdateSource.REALITY_BEHAVIOR

    def test_td02_zero_elapsed_noop(self):
        """TD-02: elapsed_days=0.0 → no-op, deficit unchanged, no stamp."""
        s = default_hidden_state()
        s.touch_need.deficit.value = 30.0
        original_source = s.touch_need.deficit.last_update_source

        result = accrue_touch_deficit(s, 0.0, NOW)

        assert result.touch_need.deficit.value == pytest.approx(30.0)
        assert result.touch_need.deficit.last_update_source == original_source

    def test_td03_value_never_exceeds_scalar_max(self):
        """TD-03: starting at SCALAR_MAX → deficit stays at SCALAR_MAX after accrual."""
        s = default_hidden_state()
        s.touch_need.deficit.value = SCALAR_MAX

        result = accrue_touch_deficit(s, 100.0, NOW)

        assert result.touch_need.deficit.value <= SCALAR_MAX


# ═══════════════════════════════════════════════════════════════════════════════
# Group F — integrate_body_cue + integrate_body_cue_and_save
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegrateBodyCue:

    def test_bc01_open_envelope_valid_cue_accepted(self):
        """BC-01: open envelope + valid cue → body_memory written, accepted=True."""
        s = default_hidden_state()
        env = _open()

        s, result = integrate_body_cue("hug", "calm", 0.5, s, env, NOW)

        assert result.accepted
        assert not result.rejected
        cues = [e.cue for e in s.body_memory.entries]
        assert "hug" in cues

    def test_bc02_closed_envelope_rejected(self):
        """BC-02: closed envelope → rejected, body_memory unchanged."""
        s = default_hidden_state()
        env = _closed()

        s, result = integrate_body_cue("hug", "calm", 0.5, s, env, NOW)

        assert result.rejected
        assert not result.accepted
        assert len(s.body_memory.entries) == 0

    def test_bc03_and_save_round_trip(self, tmp_path, monkeypatch):
        """BC-03: integrate_body_cue_and_save → entry can be reloaded from disk."""
        from core.memory.user_hidden_state_store import HIDDEN_STATE_FILENAME
        from core.sandbox import get_paths
        import core.sandbox as _sandbox_mod

        uid = "uid_p3_bc03"

        # Patch get_paths to write under tmp_path
        class _FakePaths:
            def user_memory_root(self, u):
                p = tmp_path / "memory" / str(u)
                p.mkdir(parents=True, exist_ok=True)
                return p

        monkeypatch.setattr(_sandbox_mod, "get_paths", lambda: _FakePaths())

        env = _open()
        _, result = integrate_body_cue_and_save(uid, "hug", "calm", 0.7, env, NOW)

        assert result.accepted

        # Reload from disk
        reloaded = load_hidden_state(uid)
        cues = [e.cue for e in reloaded.body_memory.entries]
        assert "hug" in cues, "entry must persist to disk"

    def test_bc04_body_cue_does_not_touch_sensitivity_or_deficit(self):
        """BC-04: integrate_body_cue must not touch sensitivity.current or touch_need.deficit."""
        s = default_hidden_state()
        s.sensitivity.current.value = 65.0
        s.touch_need.deficit.value = 40.0
        env = _open()

        s, _ = integrate_body_cue("hug", "calm", 0.5, s, env, NOW)

        assert s.sensitivity.current.value == pytest.approx(65.0)
        assert s.touch_need.deficit.value == pytest.approx(40.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Group G — scheduler helpers / apply_time_decay pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchedulerHelpers:

    def test_sc01_stamp_trigger_has_can_write_memory(self):
        """SC-01: stamp_trigger() produces an envelope with can_write_memory=True."""
        env = stamp_trigger()
        assert env.can_write_memory is True

    def test_sc02_stamp_debug_cannot_write(self):
        """SC-02: stamp_debug() has can_write_memory=False — wrong envelope for decay save."""
        env = stamp_debug()
        assert env.can_write_memory is False

    def test_sc03_after_decay_tick_in_future_elapsed_positive_next_call(self):
        """SC-03: after apply_time_decay sets tick=now, a subsequent call with later now gives positive elapsed."""
        s = default_hidden_state()
        s = apply_time_decay(s, NOW)      # first run sets last_decay_tick=NOW
        assert s.last_decay_tick == NOW

        s = apply_time_decay(s, LATER)    # 5 days later
        assert s.last_decay_tick == LATER
        # current should have moved from center toward baseline (center → no change at center)
        # Just verify tick was updated
        assert s.last_decay_tick == LATER

    def test_sc04_repeated_decay_entry_count_stable(self):
        """SC-04: multiple apply_time_decay calls do not evict body_memory entries."""
        s = default_hidden_state()
        s.last_decay_tick = "2026-01-01T00:00:00Z"
        for i in range(5):
            s.body_memory.entries.append(
                BodyMemoryEntry(cue=f"cue_{i}", response_tag="t", weight=0.8,
                                created_at=NOW, last_reinforced=NOW)
            )

        ticks = [
            "2026-02-15T00:00:00Z",
            "2026-04-01T00:00:00Z",
            "2026-07-01T00:00:00Z",
        ]
        for tick in ticks:
            s = apply_time_decay(s, tick)

        assert len(s.body_memory.entries) == 5, "decay must never evict entries"


# ═══════════════════════════════════════════════════════════════════════════════
# Group H — type guards
# ═══════════════════════════════════════════════════════════════════════════════

class TestTypeGuards:

    def test_tg01_integrate_event_wrong_type_raises_type_error(self):
        """TG-01: passing DreamBodyStateEvent to integrate_event raises TypeError (not AttributeError)."""
        s = default_hidden_state()
        wrong = DreamBodyStateEvent(heat=0.5, sensitivity=0.5, tension=0.5, arousal=0.5, duration_min=10.0)
        env = _open()

        with pytest.raises(TypeError) as exc_info:
            integrate_event(wrong, s, env, NOW)  # type: ignore

        assert "RealityEventType" in str(exc_info.value), "error message must mention RealityEventType"
        # state must be untouched
        assert s.touch_need.deficit.value == pytest.approx(0.0)

    def test_tg02_integrate_impression_wrong_type_raises_type_error(self):
        """TG-02: passing a plain str to integrate_impression raises TypeError."""
        s = default_hidden_state()
        env = _open()

        with pytest.raises(TypeError) as exc_info:
            integrate_impression_wrong = __import__(
                "core.memory.user_hidden_state_integrator",
                fromlist=["integrate_impression"],
            ).integrate_impression
            integrate_impression_wrong("not an impression", s, env, NOW)  # type: ignore

        assert "ImpressionInput" in str(exc_info.value)

    def test_tg03_event_and_save_none_uid_raises_type_error(self):
        """TG-03: passing uid=None to integrate_event_and_save raises TypeError."""
        env = _open()

        with pytest.raises(TypeError) as exc_info:
            integrate_event_and_save(None, RealityEventType.SEEK_COMPANIONSHIP, env, NOW)  # type: ignore

        assert "uid" in str(exc_info.value)

    def test_tg04_nudge_embodied_ease_none_source_raises_type_error(self):
        """TG-04: passing source=None to nudge_embodied_ease raises TypeError."""
        s = default_hidden_state()

        with pytest.raises(TypeError) as exc_info:
            nudge_embodied_ease(s, 5.0, None, NOW)  # type: ignore

        assert "UpdateSource" in str(exc_info.value)

    def test_tg05_reinforce_body_memory_string_source_raises_type_error(self):
        """TG-05: passing source='raw_string' to reinforce_body_memory raises TypeError."""
        s = default_hidden_state()

        with pytest.raises(TypeError) as exc_info:
            reinforce_body_memory(s, "hug", "calm", 0.5, "raw_string", NOW)  # type: ignore

        assert "UpdateSource" in str(exc_info.value)
