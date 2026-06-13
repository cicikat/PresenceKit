import json
from unittest.mock import patch

import pytest

from core.dream.dream_afterglow import (
    _find_best_summary,
    _format_afterglow,
    load_afterglow,
)
from core.prompt_builder import _format_dream_afterglow_detail


_NOW = 2_000_000_000.0
_UID = "dream_clarity_decay_uid"


@pytest.fixture
def summary() -> dict:
    return {
        "uid": _UID,
        "afterglow": "gentle_residue",
        "summary": "我们在某个海边的场景里，你在找什么，我一直跟着你，没有离开。",
        "emotional_tags": ["warm", "longing"],
        "symbolic_fragments": ["海边", "你在找的东西", "黄昏"],
    }


def test_clear_phase_keeps_full_summary_and_symbolic_fragments(summary):
    text = _format_afterglow(summary, age_hours=1.99)

    assert "情绪摘要：我们在某个海边的场景里" in text
    assert "情绪摘要（模糊）" not in text
    assert "残留意象：海边、你在找的东西、黄昏" in text


def test_fade_phase_truncates_summary_and_drops_symbolic_fragments(summary):
    text = _format_afterglow(summary, age_hours=2.0)

    assert "情绪摘要（模糊）：" in text
    assert "情绪摘要：我们" not in text
    assert "……" in text
    assert "残留意象：" not in text
    assert "情绪色调：warm、longing" in text


def test_residue_phase_formatter_keeps_only_frame_and_tone(summary):
    text = _format_afterglow(summary, age_hours=5.0)

    assert "梦的余韵" in text
    assert "情绪色调：warm、longing" in text
    assert "情绪摘要" not in text
    assert "残留意象" not in text
    assert "现在是现实对话" in text


def test_find_best_summary_returns_newest_summary_and_age(tmp_path, summary):
    older = {**summary, "created_at": _NOW - 4 * 3600}
    newer = {**summary, "created_at": _NOW - 3 * 3600, "summary": "更新的梦"}
    (tmp_path / "dream_older.summary.json").write_text(
        json.dumps(older, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "dream_newer.summary.json").write_text(
        json.dumps(newer, ensure_ascii=False), encoding="utf-8"
    )

    with patch("core.dream.dream_afterglow._get_summaries_dir", return_value=tmp_path), \
         patch("core.dream.dream_afterglow.time.time", return_value=_NOW):
        best, age_hours = _find_best_summary(_UID)

    assert best is not None
    assert best["summary"] == "更新的梦"
    assert age_hours == pytest.approx(3.0)


@pytest.mark.parametrize("age_hours", [5.0, 7.99, 8.01])
def test_load_afterglow_hands_off_to_soft_hint_at_five_hours(
    tmp_path, summary, age_hours
):
    stored = {**summary, "created_at": _NOW - age_hours * 3600}
    (tmp_path / "dream_handoff.summary.json").write_text(
        json.dumps(stored, ensure_ascii=False), encoding="utf-8"
    )

    with patch("core.dream.dream_afterglow._get_summaries_dir", return_value=tmp_path), \
         patch("core.dream.dream_afterglow.time.time", return_value=_NOW):
        text = load_afterglow(_UID)

    assert text == ""


def test_prompt_detail_helper_forwards_scope():
    with patch(
        "core.dream.dream_afterglow.load_afterglow",
        return_value="detailed afterglow",
    ) as loader:
        text = _format_dream_afterglow_detail(_UID, char_id="character_b")

    assert text == "detailed afterglow"
    loader.assert_called_once_with(_UID, char_id="character_b")


def test_prompt_detail_helper_fails_closed():
    with patch(
        "core.dream.dream_afterglow.load_afterglow",
        side_effect=RuntimeError("broken summary"),
    ):
        text = _format_dream_afterglow_detail(_UID)

    assert text == ""
