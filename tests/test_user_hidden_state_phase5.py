"""
tests/test_user_hidden_state_phase5.py
=======================================
Phase 5 — Dream Afterglow → Reality-side integrator → Hidden State writeback.

Tests cover:
  A  TTL enforcement           (3)   TL-01–TL-03
  B  Envelope rejection        (3)   ER-01–ER-03
  C  Valid afterglow           (3)   VA-01–VA-03
  D  Tone-specific effects     (4)   TE-01–TE-04
  E  Protected field isolation (4)   PI-01–PI-04
  F  and_save wiring           (2)   AS-01–AS-02
  G  read_afterglow_residue    (3)   RR-01–RR-03
  H  Write isolation           (2)   WI-01–WI-02
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from core.memory.user_hidden_state import (
    AFTERGLOW_TTL_HOURS,
    SCALAR_CENTER,
    AfterglowResidueInput,
    UpdateSource,
    default_hidden_state,
    read_afterglow_residue,
)
from core.memory.user_hidden_state_integrator import (
    AFTERGLOW_EASE_NUDGE,
    AFTERGLOW_SENS_NUDGE_NEGATIVE,
    AFTERGLOW_SENS_NUDGE_POSITIVE,
    integrate_afterglow,
    integrate_afterglow_and_save,
)
from core.write_envelope import SourceType, stamp_debug, stamp_dream_afterglow, stamp_user_chat

# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────

NOW = "2026-06-03T10:00:00Z"


def _open_afterglow():
    """Correct envelope: can_write_memory=True, source=DREAM_AFTERGLOW."""
    return stamp_dream_afterglow()


def _closed():
    """Closed envelope (debug/test)."""
    return stamp_debug()


def _wrong_source():
    """can_write_memory=True but source != DREAM_AFTERGLOW."""
    return stamp_user_chat()


def _residue(tone: str = "comfort", tags: list[str] | None = None, age_hours: float = 1.0) -> AfterglowResidueInput:
    return AfterglowResidueInput(
        emotional_tags=tags if tags is not None else [tone],
        tone=tone,
        age_hours=age_hours,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A. TTL enforcement
# ═══════════════════════════════════════════════════════════════════════════════

class TestTTLEnforcement:
    """TL-01–TL-03: residues beyond the 8-hour window are rejected."""

    def test_TL01_within_ttl_accepted(self):
        """TL-01: age_hours = 7.9 → within TTL → accepted."""
        ag = _residue(tone="comfort", age_hours=7.9)
        state = default_hidden_state()
        state, result = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        assert result.accepted, f"expected accepted, rejected_reasons={result.rejected_reasons}"

    def test_TL02_just_over_ttl_rejected(self):
        """TL-02: age_hours = AFTERGLOW_TTL_HOURS + 0.01 (just past limit) → rejected.

        TTL is a strict inequality: age_hours > AFTERGLOW_TTL_HOURS → expired.
        Exactly at the boundary (== 8h) is still within the valid window.
        """
        ag = _residue(tone="comfort", age_hours=AFTERGLOW_TTL_HOURS + 0.01)
        state = default_hidden_state()
        _, result = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        assert result.rejected, f"expected rejected, touched={result.touched_fields}"
        assert any("TTL" in r for r in result.rejected_reasons)

    def test_TL03_expired_ttl_state_unchanged(self):
        """TL-03: expired afterglow must not mutate any field."""
        ag = _residue(tone="comfort", age_hours=AFTERGLOW_TTL_HOURS + 0.1)
        state = default_hidden_state()
        old_sens = state.sensitivity.current.value
        old_ease = state.embodied_ease.value
        state_after, result = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        assert state_after.sensitivity.current.value == old_sens
        assert state_after.embodied_ease.value == old_ease
        assert result.rejected


# ═══════════════════════════════════════════════════════════════════════════════
# B. Envelope rejection
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnvelopeRejection:
    """ER-01–ER-03: envelope gates must refuse unauthorized callers."""

    def test_ER01_closed_envelope_rejected(self):
        """ER-01: can_write_memory=False → rejected."""
        ag = _residue(tone="comfort")
        state = default_hidden_state()
        _, result = integrate_afterglow(ag, state, _closed(), NOW)
        assert result.rejected
        assert any("can_write_memory" in r for r in result.rejected_reasons)

    def test_ER02_wrong_source_rejected(self):
        """ER-02: source != DREAM_AFTERGLOW → rejected even if can_write_memory=True."""
        ag = _residue(tone="comfort")
        state = default_hidden_state()
        _, result = integrate_afterglow(ag, state, _wrong_source(), NOW)
        assert result.rejected
        assert any("dream_afterglow" in r for r in result.rejected_reasons)

    def test_ER03_wrong_source_state_unchanged(self):
        """ER-03: wrong-source rejection must leave state completely unchanged."""
        ag = _residue(tone="comfort")
        state = default_hidden_state()
        old_sens = state.sensitivity.current.value
        old_ease = state.embodied_ease.value
        state_after, _ = integrate_afterglow(ag, state, _wrong_source(), NOW)
        assert state_after.sensitivity.current.value == old_sens
        assert state_after.embodied_ease.value == old_ease


# ═══════════════════════════════════════════════════════════════════════════════
# C. Valid afterglow accepted
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidAfterglow:
    """VA-01–VA-03: valid comfort afterglow produces expected mutations."""

    def test_VA01_comfort_raises_sensitivity(self):
        """VA-01: comfort tone → sensitivity.current increases."""
        ag = _residue(tone="comfort", age_hours=1.0)
        state = default_hidden_state()
        old = state.sensitivity.current.value
        state_after, result = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        assert result.accepted
        assert state_after.sensitivity.current.value > old

    def test_VA02_comfort_raises_ease(self):
        """VA-02: comfort tone → embodied_ease increases."""
        ag = _residue(tone="comfort", age_hours=1.0)
        state = default_hidden_state()
        old_ease = state.embodied_ease.value
        state_after, result = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        assert result.accepted
        assert state_after.embodied_ease.value > old_ease

    def test_VA03_source_stamp_on_touched_fields(self):
        """VA-03: touched fields carry DREAM_AFTERGLOW source stamp."""
        ag = _residue(tone="comfort", age_hours=1.0)
        state = default_hidden_state()
        state_after, result = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        for delta in result.touched_fields:
            assert delta.source == UpdateSource.DREAM_AFTERGLOW.value


# ═══════════════════════════════════════════════════════════════════════════════
# D. Tone-specific effects
# ═══════════════════════════════════════════════════════════════════════════════

class TestToneSpecificEffects:
    """TE-01–TE-04: each tone class produces the expected directional effect."""

    def test_TE01_comfort_pushes_sensitivity_up(self):
        """TE-01: comfort → sensitivity.current increases by ~AFTERGLOW_SENS_NUDGE_POSITIVE."""
        ag = _residue(tone="comfort", age_hours=1.0)
        state = default_hidden_state()
        old = state.sensitivity.current.value
        state_after, _ = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        assert pytest.approx(state_after.sensitivity.current.value, abs=0.01) == old + AFTERGLOW_SENS_NUDGE_POSITIVE

    def test_TE02_threat_pushes_sensitivity_down(self):
        """TE-02: threat tone → sensitivity.current decreases."""
        ag = _residue(tone="threat", age_hours=1.0)
        state = default_hidden_state()
        old = state.sensitivity.current.value
        state_after, result = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        assert result.accepted
        assert state_after.sensitivity.current.value < old
        assert pytest.approx(state_after.sensitivity.current.value, abs=0.01) == old + AFTERGLOW_SENS_NUDGE_NEGATIVE

    def test_TE03_neutral_tone_no_nudge(self):
        """TE-03: unrecognised/neutral tone → no sensitivity or ease change."""
        ag = AfterglowResidueInput(emotional_tags=[], tone="neutral_unknown", age_hours=1.0)
        state = default_hidden_state()
        old_sens = state.sensitivity.current.value
        old_ease = state.embodied_ease.value
        state_after, result = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        # neutral tone: result may be accepted=False (no touched fields)
        assert state_after.sensitivity.current.value == old_sens
        assert state_after.embodied_ease.value == old_ease

    def test_TE04_safe_trusted_raises_ease(self):
        """TE-04: trusted tone → embodied_ease increases (ease-qualifying tone)."""
        ag = _residue(tone="trusted", age_hours=2.0)
        state = default_hidden_state()
        old_ease = state.embodied_ease.value
        state_after, result = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        assert result.accepted
        assert state_after.embodied_ease.value > old_ease
        assert pytest.approx(state_after.embodied_ease.value, abs=0.01) == old_ease + AFTERGLOW_EASE_NUDGE


# ═══════════════════════════════════════════════════════════════════════════════
# E. Protected field isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtectedFieldIsolation:
    """PI-01–PI-04: prohibited fields must never be mutated by afterglow."""

    def _apply_comfort(self, state=None):
        if state is None:
            state = default_hidden_state()
        ag = _residue(tone="comfort", age_hours=1.0)
        state_after, result = integrate_afterglow(ag, state, _open_afterglow(), NOW)
        return state_after, result

    def test_PI01_baseline_unchanged(self):
        """PI-01: sensitivity.baseline must not change after afterglow."""
        state = default_hidden_state()
        old_baseline = state.sensitivity.baseline.value
        state_after, _ = self._apply_comfort(state)
        assert state_after.sensitivity.baseline.value == old_baseline

    def test_PI02_touch_need_baseline_unchanged(self):
        """PI-02: touch_need.baseline must not change after afterglow."""
        state = default_hidden_state()
        old_tn_baseline = state.touch_need.baseline.value
        state_after, _ = self._apply_comfort(state)
        assert state_after.touch_need.baseline.value == old_tn_baseline

    def test_PI03_touch_deficit_unchanged(self):
        """PI-03: touch_need.deficit must not change after afterglow."""
        state = default_hidden_state()
        state.touch_need.deficit.value = 30.0
        old_deficit = state.touch_need.deficit.value
        state_after, _ = self._apply_comfort(state)
        assert state_after.touch_need.deficit.value == old_deficit

    def test_PI04_body_memory_unchanged(self):
        """PI-04: body_memory entries must not be added/modified by afterglow."""
        state = default_hidden_state()
        old_entry_count = len(state.body_memory.entries)
        state_after, _ = self._apply_comfort(state)
        assert len(state_after.body_memory.entries) == old_entry_count


# ═══════════════════════════════════════════════════════════════════════════════
# F. integrate_afterglow_and_save wiring
# ═══════════════════════════════════════════════════════════════════════════════

class TestAndSaveWiring:
    """AS-01–AS-02: _and_save variant calls save only on acceptance."""

    def test_AS01_accepted_calls_save(self, tmp_path):
        """AS-01: valid afterglow + open envelope → save_hidden_state is called once."""
        ag = _residue(tone="comfort", age_hours=1.0)
        save_calls: list[str] = []

        def _fake_load(uid):
            return default_hidden_state()

        def _fake_save(uid, state):
            save_calls.append(str(uid))
            return True

        with patch("core.memory.user_hidden_state_integrator.load_hidden_state", _fake_load), \
             patch("core.memory.user_hidden_state_integrator.save_hidden_state", _fake_save):
            state, result = integrate_afterglow_and_save("p5_uid", ag, _open_afterglow(), NOW)

        assert result.accepted
        assert save_calls == ["p5_uid"]

    def test_AS02_rejected_does_not_save(self, tmp_path):
        """AS-02: rejected afterglow (wrong source) → save_hidden_state is never called."""
        ag = _residue(tone="comfort", age_hours=1.0)
        save_calls: list[str] = []

        def _fake_load(uid):
            return default_hidden_state()

        def _fake_save(uid, state):
            save_calls.append(str(uid))
            return True

        with patch("core.memory.user_hidden_state_integrator.load_hidden_state", _fake_load), \
             patch("core.memory.user_hidden_state_integrator.save_hidden_state", _fake_save):
            _, result = integrate_afterglow_and_save("p5_uid", ag, _wrong_source(), NOW)

        assert result.rejected
        assert save_calls == [], "save must not be called on rejection"


# ═══════════════════════════════════════════════════════════════════════════════
# G. read_afterglow_residue
# ═══════════════════════════════════════════════════════════════════════════════

class TestReadAfterglowResidue:
    """RR-01–RR-03: read_afterglow_residue applies TTL and returns None/residue."""

    def _make_raw(self, tone: str, created_at: str, tags: list[str] | None = None) -> dict:
        return {
            "emotional_tags": tags if tags is not None else [tone],
            "tone": tone,
            "created_at": created_at,
        }

    def test_RR01_absent_file_returns_none(self):
        """RR-01: no stored file → read_afterglow_residue returns None."""
        with patch("core.memory.user_hidden_state_store._load_afterglow_raw", return_value=None):
            result = read_afterglow_residue("p5_uid", NOW)
        assert result is None

    def test_RR02_fresh_residue_returned(self):
        """RR-02: residue created 2h ago → returned with age_hours ≈ 2."""
        raw = self._make_raw("comfort", "2026-06-03T08:00:00Z")  # 2h before NOW
        with patch("core.memory.user_hidden_state_store._load_afterglow_raw", return_value=raw):
            result = read_afterglow_residue("p5_uid", NOW)
        assert result is not None
        assert result.tone == "comfort"
        assert pytest.approx(result.age_hours, abs=0.01) == 2.0

    def test_RR03_expired_residue_returns_none(self):
        """RR-03: residue created 9h ago (> 8h TTL) → returns None."""
        raw = self._make_raw("calm", "2026-06-03T01:00:00Z")  # 9h before NOW
        with patch("core.memory.user_hidden_state_store._load_afterglow_raw", return_value=raw):
            result = read_afterglow_residue("p5_uid", NOW)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# H. Write isolation — Dream must never directly write
# ═══════════════════════════════════════════════════════════════════════════════

class TestWriteIsolation:
    """WI-01–WI-02: Dream modules must not import or call afterglow write functions."""

    _FORBIDDEN_WRITE_NAMES = [
        "integrate_afterglow",
        "integrate_afterglow_and_save",
        "save_afterglow_residue",
        "save_hidden_state",
    ]

    def test_WI01_dream_prompt_module_has_no_afterglow_write(self):
        """WI-01: dream_prompt.py must not reference any afterglow write function."""
        import core.dream.dream_prompt as _dpm
        with open(_dpm.__file__, encoding="utf-8") as f:
            text = f.read()
        for name in self._FORBIDDEN_WRITE_NAMES:
            assert name not in text, (
                f"dream_prompt.py must not reference write function: {name}"
            )

    def test_WI02_dream_context_module_has_no_afterglow_write(self):
        """WI-02: dream_context.py must not reference any afterglow write function."""
        import core.dream.dream_context as _dmc
        with open(_dmc.__file__, encoding="utf-8") as f:
            text = f.read()
        for name in self._FORBIDDEN_WRITE_NAMES:
            assert name not in text, (
                f"dream_context.py must not reference write function: {name}"
            )
