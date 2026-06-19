"""
tests/test_chess_grounding.py

Chess Grounding 验收测试

T1.  空开局状态：turn=white、move_count=0、is_check=False
T2.  invalid FEN 不崩溃，使用默认棋盘
T3.  last_move=None 时 last_san=None、move_hint='暂无走法'
T4.  走了一步后 last_san / last_player 有值
T5.  走了 e4 后 move_hint = '普通走法'（不是将军不是吃子）
T6.  material_balance 初始为 0（均等）
T7.  format_chess_grounding_for_prompt 输出包含 <game_facts> 标签
T8.  prompt 片段包含轮次信息
T9.  prompt 片段包含子力形势
T10. 无 last_move 时 format 不包含"上一步"
"""
from __future__ import annotations

import chess

from core.activity.chess_grounding import (
    build_chess_grounding_facts,
    format_chess_grounding_for_prompt,
)


def _initial_state() -> dict:
    board = chess.Board()
    return {
        "fen": board.fen(),
        "turn": "white",
        "status": "active",
        "result": None,
        "termination": None,
        "move_history": [],
        "last_move": None,
    }


def _state_after_e4() -> dict:
    board = chess.Board()
    move = board.parse_san("e4")
    entry = {"move_no": 1, "uci": move.uci(), "san": board.san(move), "player": "white", "fen_after": ""}
    board.push(move)
    entry["fen_after"] = board.fen()
    return {
        "fen": board.fen(),
        "turn": "black",
        "status": "active",
        "result": None,
        "termination": None,
        "move_history": [entry],
        "last_move": entry,
    }


# T1
def test_initial_state_turn_white():
    facts = build_chess_grounding_facts(_initial_state())
    assert facts["turn"] == "white"
    assert facts["move_count"] == 0
    assert facts["is_check"] is False


# T2
def test_invalid_fen_no_crash():
    state = {"fen": "not_a_fen", "turn": "white", "status": "active", "move_history": [], "last_move": None}
    facts = build_chess_grounding_facts(state)
    assert "move_count" in facts


# T3
def test_no_last_move():
    facts = build_chess_grounding_facts(_initial_state())
    assert facts["last_san"] is None
    assert facts["move_hint"] == "暂无走法"


# T4
def test_last_move_populated():
    facts = build_chess_grounding_facts(_state_after_e4())
    assert facts["last_san"] is not None
    assert facts["last_player"] == "white"
    assert facts["last_uci"] == "e2e4"


# T5
def test_e4_is_normal_move():
    facts = build_chess_grounding_facts(_state_after_e4())
    assert facts["move_hint"] == "普通走法"


# T6
def test_material_balance_initial():
    facts = build_chess_grounding_facts(_initial_state())
    assert facts["material_balance"] == 0
    assert "均等" in facts["material_balance_desc"]


# T7
def test_format_has_game_facts_tags():
    facts = build_chess_grounding_facts(_initial_state())
    out = format_chess_grounding_for_prompt(facts)
    assert "<game_facts>" in out
    assert "</game_facts>" in out


# T8
def test_format_contains_turn():
    facts = build_chess_grounding_facts(_initial_state())
    out = format_chess_grounding_for_prompt(facts)
    assert "白方" in out


# T9
def test_format_contains_material():
    facts = build_chess_grounding_facts(_initial_state())
    out = format_chess_grounding_for_prompt(facts)
    assert "子力" in out


# T10
def test_format_no_last_move_no_step_section():
    facts = build_chess_grounding_facts(_initial_state())
    out = format_chess_grounding_for_prompt(facts)
    assert "上一步" not in out
