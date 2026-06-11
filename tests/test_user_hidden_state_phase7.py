"""
tests/test_user_hidden_state_phase7.py
=======================================
Phase 7 — Reality Prompt Afterglow Soft Hint.

Tests:
  A  No residue → no injection               P7-A-01
  B  TTL expired → no injection              P7-B-01
  C  Neutral + empty tags → no injection     P7-C-01
  D  Positive afterglow → inject hint        P7-D-01  P7-D-02
  E  Negative afterglow → inject hint        P7-E-01  P7-E-02
  F  Injected text cleanliness               P7-F-01  P7-F-02
  G  Read exception → no crash              P7-G-01
  H  Write isolation (no write calls)        P7-H-01
  I  Layer name is dream_afterglow_soft_hint P7-I-01
  J  Layer in _DROPPABLE list               P7-J-01
  K  No hidden-state file modification       P7-K-01
  L  Neutral-tone with non-empty tags        P7-L-01
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.prompt_builder import _AG_TONE_DESC, _format_afterglow_soft_hint
from core.memory.user_hidden_state import AfterglowResidueInput


# ─── shared helpers ───────────────────────────────────────────────────────────

def _residue(tone: str, tags: list[str] | None = None, age_hours: float = 1.0) -> AfterglowResidueInput:
    return AfterglowResidueInput(
        tone=tone,
        emotional_tags=tags if tags is not None else [tone],
        age_hours=age_hours,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A. No residue → no injection
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoResidue:
    def test_P7_A01_absent_residue_returns_empty(self):
        """P7-A-01: read_afterglow_residue returns None → helper returns ''."""
        with patch(
            "core.prompt_builder.read_afterglow_residue",
            return_value=None,
            create=True,
        ):
            # Patch inside the lazy import scope
            with patch(
                "core.memory.user_hidden_state.read_afterglow_residue",
                return_value=None,
            ):
                result = _format_afterglow_soft_hint("uid_a01")
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════════
# B. TTL expired → no injection
# ═══════════════════════════════════════════════════════════════════════════════

class TestTTLExpired:
    def test_P7_B01_ttl_expired_returns_empty(self):
        """P7-B-01: TTL expired residue (read_afterglow_residue → None) → ''."""
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=None,
        ):
            result = _format_afterglow_soft_hint("uid_b01")
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════════
# C. Neutral + empty tags → no injection
# ═══════════════════════════════════════════════════════════════════════════════

class TestNeutralEmptyTags:
    def test_P7_C01_neutral_no_tags_returns_empty(self):
        """P7-C-01: tone=neutral, tags=[] → helper returns ''."""
        residue = AfterglowResidueInput(tone="neutral", emotional_tags=[], age_hours=1.0)
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            result = _format_afterglow_soft_hint("uid_c01")
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════════
# D. Positive afterglow → inject soft hint
# ═══════════════════════════════════════════════════════════════════════════════

class TestPositiveAfterglow:
    def test_P7_D01_comfort_tone_injects_hint(self):
        """P7-D-01: tone=comfort → non-empty soft hint injected."""
        residue = _residue("comfort")
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            result = _format_afterglow_soft_hint("uid_d01")
        assert result != ""
        assert "warm" in result or "calm" in result

    def test_P7_D02_calm_tone_injects_hint(self):
        """P7-D-02: tone=calm → non-empty soft hint injected."""
        residue = _residue("calm")
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            result = _format_afterglow_soft_hint("uid_d02")
        assert result != ""
        assert "calm" in result


# ═══════════════════════════════════════════════════════════════════════════════
# E. Negative afterglow → inject soft hint (non-factual)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNegativeAfterglow:
    def test_P7_E01_stress_tone_injects_hint(self):
        """P7-E-01: tone=stress → non-empty soft hint injected."""
        residue = _residue("stress")
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            result = _format_afterglow_soft_hint("uid_e01")
        assert result != ""
        assert "uneasy" in result

    def test_P7_E02_threat_tone_injects_hint(self):
        """P7-E-02: tone=threat → non-empty soft hint injected."""
        residue = _residue("threat", tags=["threat"])
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            result = _format_afterglow_soft_hint("uid_e02")
        assert result != ""
        assert "uneasy" in result


# ═══════════════════════════════════════════════════════════════════════════════
# F. Injected text cleanliness
# ═══════════════════════════════════════════════════════════════════════════════

class TestTextCleanliness:
    def test_P7_F01_no_raw_dream_content(self):
        """P7-F-01: hint must not contain raw dream transcript text."""
        residue = AfterglowResidueInput(
            tone="comfort",
            emotional_tags=["comfort"],
            age_hours=1.0,
        )
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            result = _format_afterglow_soft_hint("uid_f01")
        # No raw dream scenario vocabulary from transcript
        # Note: [recent_dream_afterglow] marker is intentional and NOT forbidden.
        forbidden_substrings = ["transcript", "dream_summary", "archive", "dream_body"]
        for s in forbidden_substrings:
            assert s not in result, f"Hint contains forbidden string {s!r}: {result!r}"

    def test_P7_F02_no_uid_path_or_float(self):
        """P7-F-02: hint must not contain uid, file path, or raw float values."""
        uid = "uid_f02_12345"
        residue = _residue("comfort")
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            result = _format_afterglow_soft_hint(uid)
        assert uid not in result, "Hint must not contain uid"
        assert "\\" not in result, "Hint must not contain path separator"
        assert "/" not in result or result.count("/") == 0 or not any(
            c.isdigit() for c in result
        ), "Hint must not contain numeric path fragment"
        # No raw float (e.g., "1.0", "0.8")
        import re
        assert not re.search(r"\d+\.\d+", result), f"Hint contains float: {result!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# G. Read exception → no crash
# ═══════════════════════════════════════════════════════════════════════════════

class TestReadException:
    def test_P7_G01_exception_returns_empty_does_not_raise(self):
        """P7-G-01: read_afterglow_residue raises → helper returns '', no propagation."""
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            side_effect=RuntimeError("disk error"),
        ):
            result = _format_afterglow_soft_hint("uid_g01")
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════════
# H. Write isolation — no write functions called
# ═══════════════════════════════════════════════════════════════════════════════

class TestWriteIsolation:
    _FORBIDDEN_WRITE_FUNCS = [
        "save_afterglow_residue",
        "save_hidden_state",
        "integrate_afterglow",
        "integrate_afterglow_and_save",
        "integrate_event",
        "integrate_impression",
        "integrate_body_cue",
        "apply_time_decay",
        "consolidate_baselines",
    ]

    def test_P7_H01_no_write_function_called(self):
        """P7-H-01: _format_afterglow_soft_hint never calls any write function."""
        residue = _residue("comfort")
        mock_writes: dict[str, MagicMock] = {}

        patches = []
        for fname in self._FORBIDDEN_WRITE_FUNCS:
            m = MagicMock(return_value=None, name=fname)
            mock_writes[fname] = m
            patches.append(
                patch(
                    f"core.memory.user_hidden_state_integrator.{fname}",
                    m,
                    create=True,
                )
            )
            patches.append(
                patch(
                    f"core.memory.user_hidden_state_store.{fname}",
                    m,
                    create=True,
                )
            )

        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            for p in patches:
                p.start()
            try:
                _format_afterglow_soft_hint("uid_h01")
            finally:
                for p in patches:
                    p.stop()

        for fname, mock in mock_writes.items():
            assert not mock.called, f"Write function {fname!r} was called — must not be"


# ═══════════════════════════════════════════════════════════════════════════════
# I. Layer name is dream_afterglow_soft_hint
# ═══════════════════════════════════════════════════════════════════════════════

class TestLayerName:
    def test_P7_I01_layer_name_in_prompt_builder_source(self):
        """P7-I-01: prompt_builder.py contains '_layer': 'dream_afterglow_soft_hint'."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "core" / "prompt_builder.py").read_text(encoding="utf-8")
        assert "dream_afterglow_soft_hint" in src, (
            "prompt_builder.py must contain the layer name 'dream_afterglow_soft_hint'"
        )
        # Verify the _layer assignment specifically
        assert '"dream_afterglow_soft_hint"' in src or "'dream_afterglow_soft_hint'" in src

    def test_P7_I02_hint_text_contains_marker(self):
        """P7-I-02: injected hint text contains [recent_dream_afterglow] marker."""
        residue = _residue("comfort")
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            result = _format_afterglow_soft_hint("uid_i02")
        assert "[recent_dream_afterglow]" in result


# ═══════════════════════════════════════════════════════════════════════════════
# J. Layer in _DROPPABLE list
# ═══════════════════════════════════════════════════════════════════════════════

class TestLayerDroppable:
    def test_P7_J01_dream_afterglow_in_droppable(self):
        """P7-J-01: 'dream_afterglow_soft_hint' layer has a _drop_priority for token pruning.

        R4-B replaced the old _DROPPABLE list with per-layer _drop_priority fields.
        Verify that the dream_afterglow_soft_hint layer sets _drop_priority.
        """
        from pathlib import Path
        src = (Path(__file__).parent.parent / "core" / "prompt_builder.py").read_text(encoding="utf-8")
        assert "dream_afterglow_soft_hint" in src, \
            "'dream_afterglow_soft_hint' layer not found in prompt_builder.py"
        # In R4-B layout, droppability is expressed via _drop_priority on the layer dict.
        # Verify the layer assignment block contains _drop_priority.
        import re as _re
        match = _re.search(
            r'"_layer":\s*"dream_afterglow_soft_hint"[^}]*"_drop_priority"',
            src,
            _re.DOTALL,
        )
        assert match, (
            "'dream_afterglow_soft_hint' layer must include '_drop_priority' for token pruning"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# K. No hidden state file modification
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoFileModification:
    def test_P7_K01_hidden_state_file_unchanged_after_hint(self):
        """P7-K-01: afterglow residue file is unchanged after _format_afterglow_soft_hint."""
        residue_data = {
            "emotional_tags": ["comfort"],
            "tone": "comfort",
            "created_at": "2026-06-03T08:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            residue_path = Path(tmpdir) / "afterglow_residue.json"
            residue_path.write_text(json.dumps(residue_data), encoding="utf-8")
            original_mtime = residue_path.stat().st_mtime
            original_content = residue_path.read_text(encoding="utf-8")

            residue_obj = AfterglowResidueInput(
                tone="comfort", emotional_tags=["comfort"], age_hours=1.0
            )
            with patch(
                "core.memory.user_hidden_state.read_afterglow_residue",
                return_value=residue_obj,
            ):
                _format_afterglow_soft_hint("uid_k01")

            # File must be unchanged
            assert residue_path.read_text(encoding="utf-8") == original_content, (
                "afterglow_residue.json was modified — Phase 7 must be read-only"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# L. Neutral tone with non-empty tags — edge case
# ═══════════════════════════════════════════════════════════════════════════════

class TestNeutralWithTags:
    def test_P7_L01_neutral_tone_with_non_whitelisted_tags_returns_empty(self):
        """P7-L-01: tone=neutral, tags not in _AG_TONE_DESC whitelist → no injection."""
        residue = AfterglowResidueInput(
            tone="neutral",
            emotional_tags=["longing", "warmth"],  # not in _AG_TONE_DESC
            age_hours=1.0,
        )
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            result = _format_afterglow_soft_hint("uid_l01")
        assert result == "", (
            "tone=neutral with non-whitelisted tags must not inject — "
            "only explicit tone or whitelisted afterglow tag is allowed"
        )

    def test_P7_L02_neutral_tone_with_whitelisted_tag_injects_hint(self):
        """P7-L-02: tone=neutral but a whitelisted tag present → inject using that tag's desc."""
        residue = AfterglowResidueInput(
            tone="neutral",
            emotional_tags=["comfort"],  # whitelisted in _AG_TONE_DESC
            age_hours=1.0,
        )
        with patch(
            "core.memory.user_hidden_state.read_afterglow_residue",
            return_value=residue,
        ):
            result = _format_afterglow_soft_hint("uid_l02")
        assert result != "", "tone=neutral with whitelisted tag 'comfort' should inject"
        assert "warm" in result or "calm" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Tone mapping coverage
# ═══════════════════════════════════════════════════════════════════════════════

class TestToneMapping:
    @pytest.mark.parametrize("tone,expected_fragment", [
        ("comfort",  "warm"),
        ("warm",     "warm"),
        ("safe",     "warm"),
        ("trusted",  "warm"),
        ("calm",     "calm"),
        ("stress",   "uneasy"),
        ("fear",     "uneasy"),
        ("threat",   "uneasy"),
    ])
    def test_known_tone_mapped_to_desc(self, tone: str, expected_fragment: str):
        """All known tones map to a non-empty description containing expected text."""
        assert tone in _AG_TONE_DESC
        desc = _AG_TONE_DESC[tone]
        assert expected_fragment in desc, f"Tone {tone!r} → {desc!r}, expected '{expected_fragment}'"
