"""
tests/test_coplay_game_state.py — Brief 41: per-game 进度档 + coplay_context 层文本。
"""

from unittest.mock import patch

import pytest

from core.coplay import game_state, session
from core.coplay.observer import GameMoment

UID = "u1"
CHAR = "yexuan"
GAME_ID = "steam:123"


def test_default_game_state_roundtrip(sandbox):
    state = game_state.read_game_state(UID, GAME_ID, char_id=CHAR)
    assert state["progress_markers"] == []
    assert state["highlights"] == []
    assert state["aliases"] == []


def test_add_progress_marker_dedups(sandbox):
    game_state.add_progress_marker(UID, GAME_ID, "第一章", char_id=CHAR)
    game_state.add_progress_marker(UID, GAME_ID, "第一章", char_id=CHAR)
    state = game_state.add_progress_marker(UID, GAME_ID, "第二章", char_id=CHAR)
    assert state["progress_markers"] == ["第一章", "第二章"]


def test_add_highlight(sandbox):
    state = game_state.add_highlight(UID, GAME_ID, "打败了第一个 boss", char_id=CHAR)
    assert len(state["highlights"]) == 1
    assert state["highlights"][0]["summary"] == "打败了第一个 boss"


def test_set_aliases(sandbox):
    state = game_state.set_aliases(UID, GAME_ID, ["黑魂", " ", "Dark Souls"], char_id=CHAR)
    assert state["aliases"] == ["黑魂", "Dark Souls"]


def test_game_id_with_colon_does_not_raise(sandbox):
    # game_id 里的 ':' 会经 DataPaths.coplay_game_dir() 消毒，不应抛 OSError
    state = game_state.read_game_state(UID, "steam:987654", char_id=CHAR)
    game_state.write_game_state(UID, "steam:987654", state, char_id=CHAR)


# ═══════════════════════════════════════════════════════════════════════════
# build_coplay_context_text
# ═══════════════════════════════════════════════════════════════════════════

def test_context_text_empty_when_off(sandbox):
    assert game_state.build_coplay_context_text(UID, char_id=CHAR) == ""


def test_context_text_empty_when_armed(sandbox):
    session.arm(UID, char_id=CHAR)
    assert game_state.build_coplay_context_text(UID, char_id=CHAR) == ""


def test_context_text_populated_when_active(sandbox):
    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id=GAME_ID, game_name="黑暗之魂", char_id=CHAR)
    game_state.add_progress_marker(UID, GAME_ID, "传火祭祀场", char_id=CHAR)

    with patch(
        "core.coplay.observer.peek_moments",
        return_value=[GameMoment(kind="death", summary="她被BOSS秒了")],
    ):
        text = game_state.build_coplay_context_text(UID, char_id=CHAR)

    assert "<陪玩状态>" in text and "</陪玩状态>" in text
    assert "黑暗之魂" in text
    assert "传火祭祀场" in text
    assert "她被BOSS秒了" in text
    assert "禁止预测后续剧情" in text


def test_context_text_empty_when_closing(sandbox):
    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id=GAME_ID, game_name="黑暗之魂", char_id=CHAR)
    session.enter_closing(UID, char_id=CHAR)
    assert game_state.build_coplay_context_text(UID, char_id=CHAR) == ""


def test_context_text_fail_open_on_exception(sandbox):
    with patch("core.coplay.session.read_state", side_effect=RuntimeError("boom")):
        assert game_state.build_coplay_context_text(UID, char_id=CHAR) == ""
