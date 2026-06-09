"""
tests/test_gomoku_ai.py

Gomoku Activity P1 验收测试（15 用例）

覆盖：
P21. opponent=human 保持 P0 行为：用户落子后无 AI 自动落子
P22. opponent=yexuan_ai：用户落黑后 AI 自动落白，move_history 含 2 条记录
P23. AI 落子合法：在棋盘内、格子为空
P24. move_history 中 AI 落子包含 source/style 字段
P25. AI 有一步成五时主动赢（serious 模式）
P26. 对手有一步成五时 AI 堵（serious 模式）
P27. 空棋盘 AI 走中心 (7, 7)
P28. serious 选择最高分候选（活四位置）
P29. gentle 不主动一步杀（无对手威胁时避开赢招）
P30. gentle 必须防守时仍堵
P31. 用户赢后 AI 不再落子
P32. AI 赢后 status=completed / winner=white
P33. completed 后不允许继续落子
P34. opponent=yexuan_ai 不写 short_term / user_hidden_state
P35. start 接口 P0 兼容（不传 opponent/ai_style 时默认字段正确）
"""
from __future__ import annotations

import pytest

from core.activity import gomoku as G
from core.activity.store import update_state
from core.activity.gomoku_ai import choose_gomoku_ai_move


# ── 工厂助手 ──────────────────────────────────────────────────────────────────

def _start_ai(sandbox, uid="user1", char_id="yexuan", ai_style="serious"):
    return G.start_game(uid, char_id, opponent="yexuan_ai", ai_style=ai_style)


def _move(uid, char_id, session_id, x, y):
    return G.make_move(uid, char_id, session_id, x, y)


def _inject_board(session, board, current_turn="black", move_history=None):
    """替换 session 的棋盘状态并持久化（测试专用）。"""
    session.state["board"] = board
    session.state["current_turn"] = current_turn
    if move_history is not None:
        session.state["move_history"] = move_history
    update_state(session.char_id, session.uid, "gomoku", session.session_id, session.state)


def _make_board():
    return [[None] * 15 for _ in range(15)]


# ═══════════════════════════════════════════════════════════════════════════════
# P21 — opponent=human 不触发 AI 自动落子
# ═══════════════════════════════════════════════════════════════════════════════

def test_human_opponent_no_ai_automove(sandbox):
    session = G.start_game("user1", "yexuan")  # opponent="human" by default
    result = G.make_move("user1", "yexuan", session.session_id, 7, 7)
    assert result["current_turn"] == "white"
    assert len(result["move_history"]) == 1
    assert result["move_history"][0]["player"] == "black"


# ═══════════════════════════════════════════════════════════════════════════════
# P22 — opponent=yexuan_ai 用户落黑后 AI 自动落白
# ═══════════════════════════════════════════════════════════════════════════════

def test_ai_auto_moves_after_user(sandbox):
    session = _start_ai(sandbox)
    result = G.make_move("user1", "yexuan", session.session_id, 7, 7)
    assert len(result["move_history"]) == 2
    assert result["move_history"][0]["player"] == "black"
    assert result["move_history"][1]["player"] == "white"
    assert result["current_turn"] == "black"


# ═══════════════════════════════════════════════════════════════════════════════
# P23 — AI 落子合法
# ═══════════════════════════════════════════════════════════════════════════════

def test_ai_move_is_valid(sandbox):
    session = _start_ai(sandbox)
    result = G.make_move("user1", "yexuan", session.session_id, 7, 7)
    ai_mv = result["move_history"][-1]
    ax, ay = ai_mv["x"], ai_mv["y"]
    assert 0 <= ax < 15
    assert 0 <= ay < 15
    assert result["board"][ay][ax] == "white"
    assert not (ax == 7 and ay == 7)


# ═══════════════════════════════════════════════════════════════════════════════
# P24 — move_history 中 AI 落子包含 source/style 字段
# ═══════════════════════════════════════════════════════════════════════════════

def test_ai_move_history_has_source_style(sandbox):
    session = _start_ai(sandbox, ai_style="balanced")
    result = G.make_move("user1", "yexuan", session.session_id, 7, 7)
    ai_mv = result["move_history"][-1]
    assert ai_mv["source"] == "ai"
    assert ai_mv["style"] == "balanced"
    assert ai_mv["player"] == "white"


# ═══════════════════════════════════════════════════════════════════════════════
# P25 — AI 有一步成五时主动赢（serious）
# ═══════════════════════════════════════════════════════════════════════════════

def test_ai_takes_winning_move(sandbox):
    """
    白棋已在 (0,0)-(3,0) 有 4 子，(4,0) 为空。
    用户落黑在无害位置，AI serious 应补 (4,0) 赢棋。
    """
    session = _start_ai(sandbox, ai_style="serious")
    board = session.state["board"]
    for x in range(4):
        board[0][x] = "white"
    for i, (bx, by) in enumerate([(14, 14), (13, 14), (12, 14), (11, 14)]):
        board[by][bx] = "black"
    history = [
        {"x": 14, "y": 14, "player": "black", "move_no": 1},
        {"x": 0,  "y": 0,  "player": "white", "move_no": 2},
        {"x": 13, "y": 14, "player": "black", "move_no": 3},
        {"x": 1,  "y": 0,  "player": "white", "move_no": 4},
        {"x": 12, "y": 14, "player": "black", "move_no": 5},
        {"x": 2,  "y": 0,  "player": "white", "move_no": 6},
        {"x": 11, "y": 14, "player": "black", "move_no": 7},
        {"x": 3,  "y": 0,  "player": "white", "move_no": 8},
    ]
    _inject_board(session, board, current_turn="black", move_history=history)

    result = G.make_move("user1", "yexuan", session.session_id, 7, 7)
    assert result["winner"] == "white"
    assert result["status"] == "completed"
    ai_mv = result["move_history"][-1]
    assert ai_mv["x"] == 4 and ai_mv["y"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# P26 — 对手有一步成五时 AI 堵（serious）
# ═══════════════════════════════════════════════════════════════════════════════

def test_ai_blocks_opponent_win(sandbox):
    """
    黑棋已在 (0,0)-(3,0) 有 4 子，(4,0) 为空。
    用户落黑在无害位置，AI serious 必须堵 (4,0)。
    """
    session = _start_ai(sandbox, ai_style="serious")
    board = session.state["board"]
    for x in range(4):
        board[0][x] = "black"
    for bx, by in [(8, 8), (9, 9), (10, 10)]:
        board[by][bx] = "white"
    history = [
        {"x": 0,  "y": 0,  "player": "black", "move_no": 1},
        {"x": 8,  "y": 8,  "player": "white", "move_no": 2},
        {"x": 1,  "y": 0,  "player": "black", "move_no": 3},
        {"x": 9,  "y": 9,  "player": "white", "move_no": 4},
        {"x": 2,  "y": 0,  "player": "black", "move_no": 5},
        {"x": 10, "y": 10, "player": "white", "move_no": 6},
        {"x": 3,  "y": 0,  "player": "black", "move_no": 7},
    ]
    _inject_board(session, board, current_turn="black", move_history=history)

    result = G.make_move("user1", "yexuan", session.session_id, 14, 14)
    ai_mv = result["move_history"][-1]
    assert ai_mv["x"] == 4 and ai_mv["y"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# P27 — 空棋盘 AI 走中心 (7, 7)
# ═══════════════════════════════════════════════════════════════════════════════

def test_opening_ai_plays_center():
    board = _make_board()
    x, y = choose_gomoku_ai_move(board, "white", "serious")
    assert x == 7 and y == 7


# ═══════════════════════════════════════════════════════════════════════════════
# P28 — serious 选择最高分候选
# ═══════════════════════════════════════════════════════════════════════════════

def test_serious_picks_winning_candidate():
    """
    白棋 (0,0)-(3,0) 活四，(4,0) 应是最高分位置。
    serious 必须选 (4,0)。
    """
    board = _make_board()
    for x in range(4):
        board[0][x] = "white"
    board[14][14] = "black"

    ax, ay = choose_gomoku_ai_move(board, "white", "serious")
    assert ax == 4 and ay == 0


# ═══════════════════════════════════════════════════════════════════════════════
# P29 — gentle 不主动一步杀（无对手威胁时）
# ═══════════════════════════════════════════════════════════════════════════════

def test_gentle_avoids_winning_move_when_not_needed():
    """
    白棋 (0,0)-(3,0) 活四，黑棋无威胁。
    gentle 不应选 (4,0)（一步赢招）。
    """
    board = _make_board()
    for x in range(4):
        board[0][x] = "white"
    board[14][14] = "black"

    ax, ay = choose_gomoku_ai_move(board, "white", "gentle")
    assert not (ax == 4 and ay == 0), "gentle 不应主动一步赢"


# ═══════════════════════════════════════════════════════════════════════════════
# P30 — gentle 必须防守时堵对手
# ═══════════════════════════════════════════════════════════════════════════════

def test_gentle_must_block_when_opponent_wins():
    """
    黑棋 (0,0)-(3,0) 活四，(4,0) 为空。
    即使 gentle 风格，AI (白) 也必须堵 (4,0)。
    """
    board = _make_board()
    for x in range(4):
        board[0][x] = "black"
    board[8][8] = "white"

    ax, ay = choose_gomoku_ai_move(board, "white", "gentle")
    assert ax == 4 and ay == 0


# ═══════════════════════════════════════════════════════════════════════════════
# P31 — 用户赢后 AI 不再落子
# ═══════════════════════════════════════════════════════════════════════════════

def test_user_wins_ai_does_not_move(sandbox):
    """
    黑棋在 (0,0)-(3,0) 有 4 子，用户落 (4,0) 赢。
    AI 不应追加落子。
    """
    session = _start_ai(sandbox, ai_style="serious")
    board = session.state["board"]
    for x in range(4):
        board[0][x] = "black"
    for bx, by in [(8, 8), (9, 9), (10, 10)]:
        board[by][bx] = "white"
    history = [
        {"x": 0, "y": 0, "player": "black", "move_no": 1},
        {"x": 8, "y": 8, "player": "white", "move_no": 2},
        {"x": 1, "y": 0, "player": "black", "move_no": 3},
        {"x": 9, "y": 9, "player": "white", "move_no": 4},
        {"x": 2, "y": 0, "player": "black", "move_no": 5},
        {"x": 10, "y": 10, "player": "white", "move_no": 6},
        {"x": 3, "y": 0, "player": "black", "move_no": 7},
    ]
    _inject_board(session, board, current_turn="black", move_history=history)

    result = G.make_move("user1", "yexuan", session.session_id, 4, 0)
    assert result["winner"] == "black"
    assert result["status"] == "completed"
    # AI 不追加落子：最后一条是用户的黑棋
    assert result["move_history"][-1]["player"] == "black"
    ai_moves = [m for m in result["move_history"] if m.get("source") == "ai"]
    assert len(ai_moves) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# P32 — AI 赢后 status=completed / winner=white
# ═══════════════════════════════════════════════════════════════════════════════

def test_ai_wins_status_completed(sandbox):
    session = _start_ai(sandbox, ai_style="serious")
    board = session.state["board"]
    for x in range(4):
        board[0][x] = "white"
    for bx, by in [(14, 14), (13, 14), (12, 14), (11, 14)]:
        board[by][bx] = "black"
    history = [
        {"x": 14, "y": 14, "player": "black", "move_no": 1},
        {"x": 0,  "y": 0,  "player": "white", "move_no": 2},
        {"x": 13, "y": 14, "player": "black", "move_no": 3},
        {"x": 1,  "y": 0,  "player": "white", "move_no": 4},
        {"x": 12, "y": 14, "player": "black", "move_no": 5},
        {"x": 2,  "y": 0,  "player": "white", "move_no": 6},
        {"x": 11, "y": 14, "player": "black", "move_no": 7},
        {"x": 3,  "y": 0,  "player": "white", "move_no": 8},
    ]
    _inject_board(session, board, current_turn="black", move_history=history)

    result = G.make_move("user1", "yexuan", session.session_id, 7, 7)
    assert result["status"] == "completed"
    assert result["winner"] == "white"
    assert "win_line" in result


# ═══════════════════════════════════════════════════════════════════════════════
# P33 — completed 后不允许继续落子
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_move_after_ai_wins(sandbox):
    session = _start_ai(sandbox, ai_style="serious")
    board = session.state["board"]
    for x in range(4):
        board[0][x] = "white"
    for bx, by in [(14, 14), (13, 14), (12, 14), (11, 14)]:
        board[by][bx] = "black"
    history = [
        {"x": 14, "y": 14, "player": "black", "move_no": 1},
        {"x": 0,  "y": 0,  "player": "white", "move_no": 2},
        {"x": 13, "y": 14, "player": "black", "move_no": 3},
        {"x": 1,  "y": 0,  "player": "white", "move_no": 4},
        {"x": 12, "y": 14, "player": "black", "move_no": 5},
        {"x": 2,  "y": 0,  "player": "white", "move_no": 6},
        {"x": 11, "y": 14, "player": "black", "move_no": 7},
        {"x": 3,  "y": 0,  "player": "white", "move_no": 8},
    ]
    _inject_board(session, board, current_turn="black", move_history=history)

    G.make_move("user1", "yexuan", session.session_id, 7, 7)  # AI 赢
    with pytest.raises(ValueError, match="已结束"):
        G.make_move("user1", "yexuan", session.session_id, 0, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# P34 — AI 模式不写 short_term / user_hidden_state
# ═══════════════════════════════════════════════════════════════════════════════

def test_ai_mode_no_short_term_written(sandbox):
    session = _start_ai(sandbox, ai_style="serious")
    G.make_move("user1", "yexuan", session.session_id, 7, 7)
    G.make_move("user1", "yexuan", session.session_id, 6, 6)

    history_dir = sandbox._p("history")
    chars_history = sandbox._p("chars", "yexuan", "history")
    for p in (history_dir, chars_history):
        if p.exists():
            assert list(p.iterdir()) == [], f"unexpected write in {p}"

    hidden = sandbox._p("runtime", "memory", "yexuan", "user1", "user_hidden_state.json")
    assert not hidden.exists()


# ═══════════════════════════════════════════════════════════════════════════════
# P35 — start 接口 P0 兼容：不传 opponent/ai_style 时默认字段正确
# ═══════════════════════════════════════════════════════════════════════════════

def test_start_p0_compat_default_fields(sandbox):
    session = G.start_game("user1", "yexuan")
    assert session.state["opponent"] == "human"
    assert session.state["ai_player"] == "white"
    assert session.state["ai_style"] == "balanced"
