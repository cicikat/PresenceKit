"""
tests/test_gomoku_activity.py

Gomoku Activity P0 验收测试（20 用例）

覆盖：
1.  start 创建 gomoku session 成功
2.  默认 board_size=15
3.  黑棋先手
4.  合法落子后棋盘更新
5.  落子后 current_turn 切换
6.  重复落同一格报错
7.  越界坐标报错
8.  非 active/completed session 不能继续落子（session 已关闭）
9.  横向五连胜
10. 纵向五连胜
11. 左上到右下斜线五连胜
12. 右上到左下斜线五连胜
13. 胜利后 winner 正确
14. 胜利后不允许继续落子
15. move_history 顺序正确
16. uid + char_id 隔离
17. yexuan/hongcha 不共用 session
18. 不写 short_term / history / user_hidden_state
19. 非法 session_id 不能路径逃逸
20. close 后 state 不再返回 active session
"""
from __future__ import annotations

import pytest

from core.activity import gomoku as G
from core.activity import store as activity_store


# ── 工厂助手 ──────────────────────────────────────────────────────────────────

def _start(sandbox, uid="user1", char_id="yexuan", board_size=15):
    return G.start_game(uid, char_id, board_size)


def _move(uid, char_id, session_id, x, y):
    return G.make_move(uid, char_id, session_id, x, y)


def _play_sequence(uid, char_id, session_id, moves):
    """依次执行落子序列，返回最后一步的结果。"""
    result = None
    for x, y in moves:
        result = _move(uid, char_id, session_id, x, y)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# T1 — start 创建 gomoku session 成功
# ═══════════════════════════════════════════════════════════════════════════════

def test_start_creates_session(sandbox):
    session = _start(sandbox)
    assert session is not None
    assert session.session_id
    assert session.activity_type == "gomoku"
    assert session.status == "active"
    assert session.uid == "user1"
    assert session.char_id == "yexuan"

    loaded = activity_store.load_session("yexuan", "user1", "gomoku", session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id


# ═══════════════════════════════════════════════════════════════════════════════
# T2 — 默认 board_size=15
# ═══════════════════════════════════════════════════════════════════════════════

def test_default_board_size_15(sandbox):
    session = _start(sandbox)
    state = session.state
    assert state["board_size"] == 15
    assert len(state["board"]) == 15
    assert all(len(row) == 15 for row in state["board"])


# ═══════════════════════════════════════════════════════════════════════════════
# T3 — 黑棋先手
# ═══════════════════════════════════════════════════════════════════════════════

def test_black_goes_first(sandbox):
    session = _start(sandbox)
    assert session.state["current_turn"] == "black"


# ═══════════════════════════════════════════════════════════════════════════════
# T4 — 合法落子后棋盘更新
# ═══════════════════════════════════════════════════════════════════════════════

def test_legal_move_updates_board(sandbox):
    session = _start(sandbox)
    sid = session.session_id
    result = _move("user1", "yexuan", sid, 7, 7)
    assert result["board"][7][7] == "black"
    assert result["last_move"] == {"x": 7, "y": 7, "player": "black", "move_no": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# T5 — 落子后 current_turn 切换
# ═══════════════════════════════════════════════════════════════════════════════

def test_current_turn_switches_after_move(sandbox):
    session = _start(sandbox)
    sid = session.session_id
    r1 = _move("user1", "yexuan", sid, 7, 7)
    assert r1["current_turn"] == "white"

    r2 = _move("user1", "yexuan", sid, 0, 0)
    assert r2["current_turn"] == "black"


# ═══════════════════════════════════════════════════════════════════════════════
# T6 — 重复落同一格报错
# ═══════════════════════════════════════════════════════════════════════════════

def test_duplicate_move_raises(sandbox):
    session = _start(sandbox)
    sid = session.session_id
    _move("user1", "yexuan", sid, 7, 7)  # black
    _move("user1", "yexuan", sid, 0, 0)  # white

    with pytest.raises(ValueError, match="已有棋子"):
        _move("user1", "yexuan", sid, 7, 7)  # occupied


# ═══════════════════════════════════════════════════════════════════════════════
# T7 — 越界坐标报错
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("x,y", [
    (15, 0), (0, 15), (-1, 0), (0, -1), (15, 15), (100, 100),
])
def test_out_of_bounds_raises(sandbox, x, y):
    session = _start(sandbox)
    with pytest.raises(ValueError, match="超出棋盘范围"):
        _move("user1", "yexuan", session.session_id, x, y)


# ═══════════════════════════════════════════════════════════════════════════════
# T8 — 已关闭的 session 不能继续落子
# ═══════════════════════════════════════════════════════════════════════════════

def test_closed_session_cannot_move(sandbox):
    session = _start(sandbox)
    sid = session.session_id
    G.close_game("user1", "yexuan", sid)
    with pytest.raises(ValueError, match="已关闭"):
        _move("user1", "yexuan", sid, 7, 7)


# ═══════════════════════════════════════════════════════════════════════════════
# T9 — 横向五连胜
# ═══════════════════════════════════════════════════════════════════════════════

def test_horizontal_win(sandbox):
    # black: (0,0),(1,0),(2,0),(3,0),(4,0) — row 0
    # white: (0,14),(1,14),(2,14),(3,14) — dummy moves
    session = _start(sandbox)
    sid = session.session_id
    moves = [
        (0, 0), (0, 14),
        (1, 0), (1, 14),
        (2, 0), (2, 14),
        (3, 0), (3, 14),
        (4, 0),  # black wins
    ]
    result = _play_sequence("user1", "yexuan", sid, moves)
    assert result["status"] == "completed"
    assert result["winner"] == "black"
    assert "win_line" in result
    win_cells = {(c["x"], c["y"]) for c in result["win_line"]}
    assert {(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)}.issubset(win_cells)


# ═══════════════════════════════════════════════════════════════════════════════
# T10 — 纵向五连胜
# ═══════════════════════════════════════════════════════════════════════════════

def test_vertical_win(sandbox):
    # black: (7,0),(7,1),(7,2),(7,3),(7,4) — col 7
    session = _start(sandbox)
    sid = session.session_id
    moves = [
        (7, 0), (0, 14),
        (7, 1), (1, 14),
        (7, 2), (2, 14),
        (7, 3), (3, 14),
        (7, 4),
    ]
    result = _play_sequence("user1", "yexuan", sid, moves)
    assert result["status"] == "completed"
    assert result["winner"] == "black"
    win_cells = {(c["x"], c["y"]) for c in result["win_line"]}
    assert {(7, 0), (7, 1), (7, 2), (7, 3), (7, 4)}.issubset(win_cells)


# ═══════════════════════════════════════════════════════════════════════════════
# T11 — 左上到右下斜线五连胜
# ═══════════════════════════════════════════════════════════════════════════════

def test_diagonal_down_right_win(sandbox):
    # black: (0,0),(1,1),(2,2),(3,3),(4,4)
    session = _start(sandbox)
    sid = session.session_id
    moves = [
        (0, 0), (0, 14),
        (1, 1), (1, 14),
        (2, 2), (2, 14),
        (3, 3), (3, 14),
        (4, 4),
    ]
    result = _play_sequence("user1", "yexuan", sid, moves)
    assert result["status"] == "completed"
    assert result["winner"] == "black"
    win_cells = {(c["x"], c["y"]) for c in result["win_line"]}
    assert {(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)}.issubset(win_cells)


# ═══════════════════════════════════════════════════════════════════════════════
# T12 — 右上到左下斜线五连胜
# ═══════════════════════════════════════════════════════════════════════════════

def test_diagonal_down_left_win(sandbox):
    # black: (4,0),(3,1),(2,2),(1,3),(0,4) — anti-diagonal
    session = _start(sandbox)
    sid = session.session_id
    moves = [
        (4, 0), (0, 14),
        (3, 1), (1, 14),
        (2, 2), (2, 14),
        (1, 3), (3, 14),
        (0, 4),
    ]
    result = _play_sequence("user1", "yexuan", sid, moves)
    assert result["status"] == "completed"
    assert result["winner"] == "black"
    win_cells = {(c["x"], c["y"]) for c in result["win_line"]}
    assert {(4, 0), (3, 1), (2, 2), (1, 3), (0, 4)}.issubset(win_cells)


# ═══════════════════════════════════════════════════════════════════════════════
# T13 — 胜利后 winner 正确
# ═══════════════════════════════════════════════════════════════════════════════

def test_winner_is_correct_player(sandbox):
    # white 赢: black dummy moves 分散在不同行列，white 在 col 0 纵向五连
    session = _start(sandbox)
    sid = session.session_id
    moves = [
        (14, 14), (0, 0),   # black corner, white (0,0)
        (0, 14),  (0, 1),   # black different corner
        (1,  0),  (0, 2),
        (13, 14), (0, 3),
        (2,  0),  (0, 4),   # white wins col 0, y=0..4
    ]
    result = _play_sequence("user1", "yexuan", sid, moves)
    assert result["winner"] == "white"
    assert result["status"] == "completed"


# ═══════════════════════════════════════════════════════════════════════════════
# T14 — 胜利后不允许继续落子
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_move_after_win(sandbox):
    session = _start(sandbox)
    sid = session.session_id
    moves = [
        (0, 0), (0, 14),
        (1, 0), (1, 14),
        (2, 0), (2, 14),
        (3, 0), (3, 14),
        (4, 0),   # black wins
    ]
    _play_sequence("user1", "yexuan", sid, moves)

    with pytest.raises(ValueError, match="已结束"):
        _move("user1", "yexuan", sid, 5, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# T15 — move_history 顺序正确
# ═══════════════════════════════════════════════════════════════════════════════

def test_move_history_order(sandbox):
    session = _start(sandbox)
    sid = session.session_id
    coords = [(7, 7), (8, 8), (6, 6)]
    for i, (x, y) in enumerate(coords):
        _move("user1", "yexuan", sid, x, y)

    loaded = activity_store.load_session("yexuan", "user1", "gomoku", sid)
    history = loaded.state["move_history"]
    assert len(history) == 3
    for i, (x, y) in enumerate(coords):
        assert history[i]["x"] == x
        assert history[i]["y"] == y
        assert history[i]["move_no"] == i + 1
    assert history[0]["player"] == "black"
    assert history[1]["player"] == "white"
    assert history[2]["player"] == "black"


# ═══════════════════════════════════════════════════════════════════════════════
# T16 — uid + char_id 隔离
# ═══════════════════════════════════════════════════════════════════════════════

def test_uid_char_id_isolation(sandbox):
    s1 = G.start_game("user1", "yexuan")
    s2 = G.start_game("user2", "yexuan")
    s3 = G.start_game("user1", "hongcha")

    # 三个 session_id 互不相同
    assert len({s1.session_id, s2.session_id, s3.session_id}) == 3

    # find_active_session 按 char_id + uid 返回各自的 session
    a1 = G.get_active_session("user1", "yexuan")
    a2 = G.get_active_session("user2", "yexuan")
    a3 = G.get_active_session("user1", "hongcha")

    assert a1 is not None and a1.session_id == s1.session_id
    assert a2 is not None and a2.session_id == s2.session_id
    assert a3 is not None and a3.session_id == s3.session_id


# ═══════════════════════════════════════════════════════════════════════════════
# T17 — yexuan/hongcha 不共用 session
# ═══════════════════════════════════════════════════════════════════════════════

def test_yexuan_hongcha_independent_sessions(sandbox):
    sy = G.start_game("owner", "yexuan")
    sh = G.start_game("owner", "hongcha")

    assert sy.char_id == "yexuan"
    assert sh.char_id == "hongcha"
    assert sy.session_id != sh.session_id

    # 对 yexuan 落子，不影响 hongcha
    _move("owner", "yexuan", sy.session_id, 7, 7)

    loaded_y = activity_store.load_session("yexuan", "owner", "gomoku", sy.session_id)
    loaded_h = activity_store.load_session("hongcha", "owner", "gomoku", sh.session_id)

    assert loaded_y.state["board"][7][7] == "black"
    assert loaded_h.state["board"][7][7] is None


# ═══════════════════════════════════════════════════════════════════════════════
# T18 — 不写 short_term / history / user_hidden_state
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_short_term_or_hidden_state_written(sandbox):
    session = G.start_game("user1", "yexuan")
    sid = session.session_id
    _move("user1", "yexuan", sid, 7, 7)
    _move("user1", "yexuan", sid, 0, 0)

    # history 目录不得有任何文件
    history_dir = sandbox._p("history")
    chars_history = sandbox._p("chars", "yexuan", "history")
    for p in (history_dir, chars_history):
        if p.exists():
            assert list(p.iterdir()) == [], f"unexpected write in {p}"

    # user_hidden_state.json 不得存在
    hidden = sandbox._p("runtime", "memory", "yexuan", "user1", "user_hidden_state.json")
    assert not hidden.exists()


# ═══════════════════════════════════════════════════════════════════════════════
# T19 — 非法 session_id 不能路径逃逸
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("evil_id", [
    "../evil",
    "../../etc/passwd",
    "/abs/path",
    "a/b",
    "a\\b",
])
def test_evil_session_id_no_path_escape(sandbox, evil_id):
    # load_session 对沙盒拒绝的路径应返回 None 或抛 ValueError
    # make_move 在 session is None 时抛 ValueError
    with pytest.raises((ValueError, Exception)):
        G.make_move("user1", "yexuan", evil_id, 7, 7)


def test_valid_hex_session_id_accepted(sandbox):
    session = G.start_game("user1", "yexuan")
    assert session.session_id  # hex string, accepted without error
    # 能成功落子
    result = G.make_move("user1", "yexuan", session.session_id, 7, 7)
    assert result["board"][7][7] == "black"


# ═══════════════════════════════════════════════════════════════════════════════
# T20 — close 后 state 不再返回 active session
# ═══════════════════════════════════════════════════════════════════════════════

def test_close_removes_active_session(sandbox):
    session = G.start_game("user1", "yexuan")
    sid = session.session_id
    _move("user1", "yexuan", sid, 7, 7)

    G.close_game("user1", "yexuan", sid)

    active = G.get_active_session("user1", "yexuan")
    assert active is None

    # session 本身仍可加载，status=closed
    loaded = activity_store.load_session("yexuan", "user1", "gomoku", sid)
    assert loaded is not None
    assert loaded.status == "closed"
