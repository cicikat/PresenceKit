"""
tests/test_user_hidden_state_phase6.py
=======================================
Phase 6 — Dream Exit Afterglow Wiring.

Tests cover:
  A  Tone extraction             (5)   ET-01–ET-05
  B  Summary loading             (3)   LS-01–LS-03
  C  Full wiring — residue write (2)   RW-01–RW-02
  D  Numeric effect on state     (4)   NE-01–NE-04
  E  Fail-closed safety          (2)   FC-01–FC-02
  F  Isolation guarantees        (4)   IG-01–IG-04
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.dream.dream_exit_afterglow import (
    _extract_tone_and_tags,
    _load_summary,
    _do_wire,
    wire_afterglow_from_summary,
)
from core.memory.user_hidden_state import (
    AfterglowResidueInput,
    default_hidden_state,
    read_afterglow_residue,
)
from core.memory.user_hidden_state_store import (
    load_hidden_state,
    save_afterglow_residue,
)
from core.write_envelope import SourceType, stamp_dream_afterglow

_UID = "9999"
_DREAM_ID = f"dream_{_UID}_1234567890"
_NOW = "2026-06-03T10:00:00+00:00"


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_summary(sandbox, dream_id: str, data: dict[str, Any]) -> None:
    """Write a fake summary.json to the sandbox summaries dir."""
    from core.sandbox import get_paths
    d = get_paths().dreams_summaries_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"dream_{dream_id}.summary.json"
    dest.write_text(json.dumps(data), encoding="utf-8")


def _make_gentle_summary(weight: float = 0.5) -> dict[str, Any]:
    return {
        "emotional_tags": ["温柔", "亲密"],
        "afterglow": "gentle_residue",
        "summary_weight": weight,
        "summary": "轻柔梦境",
    }


def _make_hurt_summary() -> dict[str, Any]:
    return {
        "emotional_tags": ["惊醒", "不安"],
        "afterglow": "hurt_reluctance",
        "summary_weight": 0.6,
        "summary": "强制退出",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# A. Tone extraction
# ═══════════════════════════════════════════════════════════════════════════════

class TestToneExtraction:
    """ET-01–ET-05: _extract_tone_and_tags maps summary + exit_type to tone vocab."""

    def test_ET01_hard_exit_gives_stress(self):
        """ET-01: exit_type=hard_exit → tone='stress' regardless of afterglow field."""
        summary = _make_gentle_summary(weight=0.9)
        tags, tone = _extract_tone_and_tags(summary, "hard_exit")
        assert tone == "stress"
        assert tags == summary["emotional_tags"]

    def test_ET02_hurt_reluctance_gives_stress(self):
        """ET-02: hurt_reluctance afterglow → tone='stress' (soft exit still feels bad)."""
        summary = _make_hurt_summary()
        tags, tone = _extract_tone_and_tags(summary, "soft")
        assert tone == "stress"

    def test_ET03_gentle_high_weight_gives_comfort(self):
        """ET-03: gentle_residue + weight≥0.7 → tone='comfort' (also nudges ease)."""
        summary = _make_gentle_summary(weight=0.7)
        _, tone = _extract_tone_and_tags(summary, "soft")
        assert tone == "comfort"

    def test_ET04_gentle_low_weight_gives_calm(self):
        """ET-04: gentle_residue + weight<0.7 → tone='calm' (positive, no ease nudge)."""
        summary = _make_gentle_summary(weight=0.5)
        _, tone = _extract_tone_and_tags(summary, "soft")
        assert tone == "calm"

    def test_ET05_empty_summary_gives_neutral(self):
        """ET-05: empty summary dict → fallback neutral (zero numeric effect)."""
        tags, tone = _extract_tone_and_tags({}, "soft")
        assert tone == "neutral"
        assert tags == []


# ═══════════════════════════════════════════════════════════════════════════════
# B. Summary loading
# ═══════════════════════════════════════════════════════════════════════════════

class TestSummaryLoading:
    """LS-01–LS-03: _load_summary handles missing, corrupt, and valid files."""

    def test_LS01_missing_file_returns_empty(self, tmp_path):
        """LS-01: non-existent path → returns {}."""
        result = _load_summary(tmp_path / "nonexistent.json")
        assert result == {}

    def test_LS02_corrupt_json_returns_empty(self, tmp_path):
        """LS-02: corrupt JSON → returns {} without raising."""
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json,,", encoding="utf-8")
        result = _load_summary(bad)
        assert result == {}

    def test_LS03_valid_summary_loaded(self, tmp_path):
        """LS-03: valid summary → dict with expected fields."""
        data = {"afterglow": "gentle_residue", "summary_weight": 0.8}
        f = tmp_path / "summary.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        result = _load_summary(f)
        assert result["afterglow"] == "gentle_residue"
        assert result["summary_weight"] == 0.8


# ═══════════════════════════════════════════════════════════════════════════════
# C. Full wiring — residue written to disk
# ═══════════════════════════════════════════════════════════════════════════════

class TestResidueWrite:
    """RW-01–RW-02: _do_wire / wire_afterglow_from_summary write residue to disk."""

    def test_RW01_residue_file_written(self, sandbox):
        """RW-01: after _do_wire(), afterglow_residue.json exists under user_memory_root."""
        from core.sandbox import get_paths
        _write_summary(sandbox, _DREAM_ID, _make_gentle_summary(weight=0.8))
        _do_wire(_UID, _DREAM_ID, "soft")
        residue_path = get_paths().user_memory_root(_UID) / "afterglow_residue.json"
        assert residue_path.exists(), "afterglow_residue.json must be written at Dream exit"

    def test_RW02_residue_readable_after_wire(self, sandbox):
        """RW-02: written residue is readable via read_afterglow_residue() within TTL."""
        _write_summary(sandbox, _DREAM_ID, _make_gentle_summary(weight=0.8))
        _do_wire(_UID, _DREAM_ID, "soft")
        result = read_afterglow_residue(_UID, _NOW)
        assert result is not None, "residue should be within TTL immediately after writing"
        assert result.age_hours >= 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# D. Numeric effect on hidden state
# ═══════════════════════════════════════════════════════════════════════════════

class TestNumericEffect:
    """NE-01–NE-04: afterglow wiring produces expected numeric changes."""

    def test_NE01_neutral_tone_no_numeric_change(self, sandbox):
        """NE-01: neutral tone (empty summary) → sensitivity.current and embodied_ease unchanged."""
        _write_summary(sandbox, _DREAM_ID, {})  # no afterglow field → neutral
        state_before = default_hidden_state()
        old_sens = state_before.sensitivity.current.value
        old_ease = state_before.embodied_ease.value
        _do_wire(_UID, _DREAM_ID, "soft")
        state_after = load_hidden_state(_UID)
        assert state_after.sensitivity.current.value == old_sens
        assert state_after.embodied_ease.value == old_ease

    def test_NE02_comfort_tone_raises_embodied_ease(self, sandbox):
        """NE-02: comfort tone (gentle + high weight) → embodied_ease increases."""
        _write_summary(sandbox, _DREAM_ID, _make_gentle_summary(weight=0.8))
        state_before = default_hidden_state()
        old_ease = state_before.embodied_ease.value
        _do_wire(_UID, _DREAM_ID, "soft")
        state_after = load_hidden_state(_UID)
        assert state_after.embodied_ease.value > old_ease, (
            "comfort afterglow must nudge embodied_ease upward"
        )

    def test_NE03_stress_tone_moves_sensitivity_current(self, sandbox):
        """NE-03: stress tone (hard_exit) → sensitivity.current decreases."""
        _write_summary(sandbox, _DREAM_ID, _make_hurt_summary())
        state_before = default_hidden_state()
        old_sens = state_before.sensitivity.current.value
        _do_wire(_UID, _DREAM_ID, "hard_exit")
        state_after = load_hidden_state(_UID)
        assert state_after.sensitivity.current.value < old_sens, (
            "stress afterglow must nudge sensitivity.current downward"
        )

    def test_NE04_stress_tone_does_not_touch_baseline(self, sandbox):
        """NE-04: stress afterglow must not modify sensitivity.baseline."""
        _write_summary(sandbox, _DREAM_ID, _make_hurt_summary())
        state_before = default_hidden_state()
        old_baseline = state_before.sensitivity.baseline.value
        _do_wire(_UID, _DREAM_ID, "hard_exit")
        state_after = load_hidden_state(_UID)
        assert state_after.sensitivity.baseline.value == old_baseline, (
            "afterglow must never touch sensitivity.baseline"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# E. Fail-closed safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailClosed:
    """FC-01–FC-02: failures in save and integrate must not block Dream exit."""

    def test_FC01_save_failure_does_not_block(self, sandbox):
        """FC-01: save_afterglow_residue raising → _do_wire completes without raising.

        Patch at the source module because _do_wire uses lazy imports.
        """
        _write_summary(sandbox, _DREAM_ID, _make_gentle_summary(weight=0.8))
        with patch(
            "core.memory.user_hidden_state_store.save_afterglow_residue",
            side_effect=RuntimeError("disk full"),
        ):
            try:
                _do_wire(_UID, _DREAM_ID, "soft")
            except Exception as exc:
                pytest.fail(f"_do_wire raised when save failed: {exc}")

    def test_FC02_integrate_failure_does_not_block(self, sandbox):
        """FC-02: integrate_afterglow_and_save raising → _do_wire completes without raising.

        Patch at the source module because _do_wire uses lazy imports.
        """
        _write_summary(sandbox, _DREAM_ID, _make_gentle_summary(weight=0.8))
        with patch(
            "core.memory.user_hidden_state_integrator.integrate_afterglow_and_save",
            side_effect=RuntimeError("state corrupted"),
        ):
            try:
                _do_wire(_UID, _DREAM_ID, "soft")
            except Exception as exc:
                pytest.fail(f"_do_wire raised when integrate failed: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# F. Isolation guarantees
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsolationGuarantees:
    """IG-01–IG-04: Dream exit isolation invariants."""

    def test_IG01_residue_survives_clear_local_state(self, sandbox):
        """IG-01: clear_local_state on dream_state dict does not delete the residue file.

        Residue lives in user_memory_root/afterglow_residue.json; dream_state is a
        separate dict/file.  Clearing one must not affect the other.
        """
        from core.dream.dream_state import clear_local_state

        _write_summary(sandbox, _DREAM_ID, _make_gentle_summary(weight=0.8))
        _do_wire(_UID, _DREAM_ID, "soft")

        # Simulate clear_local_state being called after wiring
        fake_state = {
            "user_id": _UID,
            "status": "DREAM_CLOSING",
            "body_state": {"foo": 1},
            "emotional_tension": 0.5,
            "scene_state": "forest",
            "context_snapshot": {"a": 1},
            "dream_id": _DREAM_ID,
            "frozen_world": "reality_derived",
            "lucid_mode": "lucid_shared",
        }
        cleared = clear_local_state(fake_state)
        assert "body_state" not in cleared

        # Residue must still be readable
        residue = read_afterglow_residue(_UID, _NOW)
        assert residue is not None, "residue must survive clear_local_state"

    def test_IG02_no_direct_save_hidden_state_call(self, sandbox):
        """IG-02: _do_wire must not call save_hidden_state directly.

        All writes must flow through integrate_afterglow_and_save (Reality-side integrator).
        Patch both at their source modules (lazy imports inside _do_wire).
        """
        _write_summary(sandbox, _DREAM_ID, _make_gentle_summary(weight=0.8))
        integrate_mock = MagicMock(return_value=(
            default_hidden_state(),
            MagicMock(accepted=False, rejected=False, rejected_reasons=[], touched_fields=[]),
        ))
        with patch(
            "core.memory.user_hidden_state_integrator.integrate_afterglow_and_save",
            integrate_mock,
        ):
            with patch("core.memory.user_hidden_state_store.save_hidden_state") as save_mock:
                _do_wire(_UID, _DREAM_ID, "soft")
                # save_hidden_state must NOT have been called directly by _do_wire;
                # it may only be reached via integrate_afterglow_and_save (which is mocked).
                save_mock.assert_not_called()
        integrate_mock.assert_called_once()

    def test_IG03_uses_stamp_dream_afterglow_envelope(self, sandbox):
        """IG-03: the envelope passed to integrate_afterglow_and_save has source=DREAM_AFTERGLOW.

        Patch at source module (lazy import inside _do_wire).
        """
        _write_summary(sandbox, _DREAM_ID, _make_gentle_summary(weight=0.8))
        captured_envelopes: list = []

        def _capture(uid, residue, write_envelope, now):
            captured_envelopes.append(write_envelope)
            return default_hidden_state(), MagicMock(
                accepted=False, rejected=False, rejected_reasons=[], touched_fields=[]
            )

        with patch(
            "core.memory.user_hidden_state_integrator.integrate_afterglow_and_save",
            side_effect=_capture,
        ):
            _do_wire(_UID, _DREAM_ID, "soft")

        assert len(captured_envelopes) == 1
        env = captured_envelopes[0]
        assert env.source == SourceType.DREAM_AFTERGLOW, (
            f"envelope source must be DREAM_AFTERGLOW, got {env.source!r}"
        )
        assert env.can_write_memory is True

    def test_IG04_long_term_layers_unchanged(self, sandbox):
        """IG-04: after full wiring, baseline/touch_need/body_memory are not touched.

        These are the permanently protected long-term layer fields.
        """
        _write_summary(sandbox, _DREAM_ID, _make_gentle_summary(weight=0.9))
        state_before = default_hidden_state()
        baseline_before = state_before.sensitivity.baseline.value
        touch_baseline_before = state_before.touch_need.baseline.value
        touch_deficit_before = state_before.touch_need.deficit.value
        body_entries_before = len(state_before.body_memory.entries)

        _do_wire(_UID, _DREAM_ID, "soft")

        state_after = load_hidden_state(_UID)
        assert state_after.sensitivity.baseline.value == baseline_before, \
            "sensitivity.baseline must not be touched by afterglow"
        assert state_after.touch_need.baseline.value == touch_baseline_before, \
            "touch_need.baseline must not be touched by afterglow"
        assert state_after.touch_need.deficit.value == touch_deficit_before, \
            "touch_need.deficit must not be touched by afterglow"
        assert len(state_after.body_memory.entries) == body_entries_before, \
            "body_memory must not be touched by afterglow"
