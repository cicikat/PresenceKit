"""
tests/test_chess_activity.py

Chess Activity P0 验收测试 (24 tests)

覆盖：
 1. start 创建 chess session 成功
 2. 默认初始 FEN 正确
 3. 白方先手
 4. 合法 UCI move e2e4 后 FEN 更新
 5. 落子后 turn 切换到 black
 6. 非法 move 报错
 7. 当前方不能走对方棋子
 8. 支持升变 UCI 格式（e7e8q）
 9. 支持王车易位规则
10. 支持吃过路兵规则
11. checkmate 后 status=completed
12. checkmate result 正确
13. stalemate 后 status=completed
14. stalemate result=1/2-1/2
15. completed 后不允许继续落子
16. legal_moves 返回合法 UCI 列表
17. move_history 顺序正确，含 uci/san/player/fen_after
18. uid + char_id 隔离
19. yexuan/hongcha 不共用 chess session
20. close 后 state 不再返回 active session
21. 不写 short_term / history / user_hidden_state
22. 非法 session_id 不能路径逃逸
23. 自定义 FEN 非法时明确报错
24. 不接 Stockfish / 外部 API / LLM
"""
from __future__ import annotations

import chess as _chess_lib
import pytest

from core.activity import chess as chess_activity
from core.activity import store as activity_store
from core.activity.chess import STARTING_FEN, apply_move, legal_moves_uci, make_initial_state


# ═══════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════

def _make_chess_session(sandbox, uid: str = "user1", char_id: str = "yexuan",
                        fen: str | None = None):
    state = make_initial_state(fen)
    return activity_store.create_session(uid, char_id, "chess", state)


# ═══════════════════════════════════════════════════════════════════════════
# 1. start 创建 chess session 成功
# ═══════════════════════════════════════════════════════════════════════════

def test_start_creates_session(sandbox):
    session = _make_chess_session(sandbox)
    assert session.activity_type == "chess"
    assert session.status == "active"
    assert session.session_id
    assert session.state


# ═══════════════════════════════════════════════════════════════════════════
# 2. 默认初始 FEN 正确
# ═══════════════════════════════════════════════════════════════════════════

def test_default_initial_fen(sandbox):
    state = make_initial_state()
    board = _chess_lib.Board()
    # FEN produced by python-chess for standard starting position
    assert state["fen"] == board.fen()
    # Verify it matches STARTING_FEN prefix (ignoring half/full move counters)
    assert state["fen"].startswith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")


# ═══════════════════════════════════════════════════════════════════════════
# 3. 白方先手
# ═══════════════════════════════════════════════════════════════════════════

def test_white_goes_first(sandbox):
    state = make_initial_state()
    assert state["turn"] == "white"
    assert state["status"] == "active"
    assert state["result"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 4. 合法 UCI move e2e4 后 FEN 更新
# ═══════════════════════════════════════════════════════════════════════════

def test_legal_uci_move_updates_fen(sandbox):
    state = make_initial_state()
    original_fen = state["fen"]
    new_state = apply_move(state, "e2e4")
    assert new_state["fen"] != original_fen
    # Pawn should now be on e4
    board = _chess_lib.Board(new_state["fen"])
    assert board.piece_at(_chess_lib.E4) == _chess_lib.Piece(_chess_lib.PAWN, _chess_lib.WHITE)
    assert board.piece_at(_chess_lib.E2) is None


# ═══════════════════════════════════════════════════════════════════════════
# 5. 落子后 turn 切换到 black
# ═══════════════════════════════════════════════════════════════════════════

def test_turn_switches_after_move(sandbox):
    state = make_initial_state()
    assert state["turn"] == "white"
    new_state = apply_move(state, "e2e4")
    assert new_state["turn"] == "black"
    # black plays
    new_state2 = apply_move(new_state, "e7e5")
    assert new_state2["turn"] == "white"


# ═══════════════════════════════════════════════════════════════════════════
# 6. 非法 move 报错
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad_move", [
    "e2e5",       # pawn can't jump two squares from start to e5
    "a1a5",       # rook is blocked at start
    "zzz",        # garbage string
    "",           # empty string
    "e2e2",       # same square
])
def test_illegal_move_raises(sandbox, bad_move):
    state = make_initial_state()
    with pytest.raises(ValueError):
        apply_move(state, bad_move)


# ═══════════════════════════════════════════════════════════════════════════
# 7. 当前方不能走对方棋子
# ═══════════════════════════════════════════════════════════════════════════

def test_cannot_move_opponent_piece(sandbox):
    # White to move — trying to move black's e7 pawn
    state = make_initial_state()
    with pytest.raises(ValueError):
        apply_move(state, "e7e5")  # black's pawn, white's turn


# ═══════════════════════════════════════════════════════════════════════════
# 8. 支持升变 UCI 格式（e7e8q）
# ═══════════════════════════════════════════════════════════════════════════

def test_promotion_uci(sandbox):
    # White pawn on e7, white king on e1, black king on g1
    fen = "8/4P3/8/8/8/8/8/4K1k1 w - - 0 1"
    state = make_initial_state(fen)
    new_state = apply_move(state, "e7e8q")
    board = _chess_lib.Board(new_state["fen"])
    piece = board.piece_at(_chess_lib.E8)
    assert piece is not None
    assert piece.piece_type == _chess_lib.QUEEN
    assert piece.color == _chess_lib.WHITE
    # last_move records the correct UCI and SAN
    assert new_state["last_move"]["uci"] == "e7e8q"


# ═══════════════════════════════════════════════════════════════════════════
# 9. 支持王车易位规则
# ═══════════════════════════════════════════════════════════════════════════

def test_castling_kingside(sandbox):
    # Both sides have castling rights; white castles kingside (e1g1)
    fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    state = make_initial_state(fen)
    new_state = apply_move(state, "e1g1")
    board = _chess_lib.Board(new_state["fen"])
    # King should be on g1, rook on f1
    assert board.piece_at(_chess_lib.G1) == _chess_lib.Piece(_chess_lib.KING, _chess_lib.WHITE)
    assert board.piece_at(_chess_lib.F1) == _chess_lib.Piece(_chess_lib.ROOK, _chess_lib.WHITE)
    assert board.piece_at(_chess_lib.E1) is None
    assert board.piece_at(_chess_lib.H1) is None


def test_castling_queenside(sandbox):
    fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    state = make_initial_state(fen)
    new_state = apply_move(state, "e1c1")  # queenside castling
    board = _chess_lib.Board(new_state["fen"])
    assert board.piece_at(_chess_lib.C1) == _chess_lib.Piece(_chess_lib.KING, _chess_lib.WHITE)
    assert board.piece_at(_chess_lib.D1) == _chess_lib.Piece(_chess_lib.ROOK, _chess_lib.WHITE)


# ═══════════════════════════════════════════════════════════════════════════
# 10. 支持吃过路兵规则
# ═══════════════════════════════════════════════════════════════════════════

def test_en_passant(sandbox):
    # Position after 1.e4 f5 2.e5 d5 — en passant square is d6
    # White's e5 pawn can capture d5 pawn via exd6 en passant (e5d6)
    fen = "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3"
    state = make_initial_state(fen)
    new_state = apply_move(state, "e5d6")
    board = _chess_lib.Board(new_state["fen"])
    # White pawn should be on d6 now
    assert board.piece_at(_chess_lib.D6) == _chess_lib.Piece(_chess_lib.PAWN, _chess_lib.WHITE)
    # Captured black pawn on d5 should be gone
    assert board.piece_at(_chess_lib.D5) is None
    # e5 should be empty
    assert board.piece_at(_chess_lib.E5) is None


# ═══════════════════════════════════════════════════════════════════════════
# 11. checkmate 后 status=completed
# ═══════════════════════════════════════════════════════════════════════════

def test_checkmate_status_completed(sandbox):
    # Fool's mate: 1. f3 e5 2. g4 Qh4#  → white is mated
    fen = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 2"
    state = make_initial_state(fen)
    new_state = apply_move(state, "d8h4")  # Qh4# (UCI)
    assert new_state["status"] == "completed"


# ═══════════════════════════════════════════════════════════════════════════
# 12. checkmate result 正确
# ═══════════════════════════════════════════════════════════════════════════

def test_checkmate_result(sandbox):
    fen = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 2"
    state = make_initial_state(fen)
    new_state = apply_move(state, "d8h4")  # black queens to h4, white is mated
    # Black wins
    assert new_state["result"] == "0-1"
    assert new_state["termination"] == "checkmate"


# ═══════════════════════════════════════════════════════════════════════════
# 13. stalemate 后 status=completed
# ═══════════════════════════════════════════════════════════════════════════

def test_stalemate_status_completed(sandbox):
    # White queen moves from c7 to b6, leaving black king on a8 in stalemate
    # Black king a8, white queen c7, white king a1 → after Qb6 it's stalemate
    fen = "k7/2Q5/8/8/8/8/8/K7 w - - 0 1"
    state = make_initial_state(fen)
    new_state = apply_move(state, "c7b6")  # Qb6 → stalemate
    assert new_state["status"] == "completed"


# ═══════════════════════════════════════════════════════════════════════════
# 14. stalemate result=1/2-1/2
# ═══════════════════════════════════════════════════════════════════════════

def test_stalemate_result(sandbox):
    fen = "k7/2Q5/8/8/8/8/8/K7 w - - 0 1"
    state = make_initial_state(fen)
    new_state = apply_move(state, "c7b6")
    assert new_state["result"] == "1/2-1/2"
    assert new_state["termination"] == "stalemate"


# ═══════════════════════════════════════════════════════════════════════════
# 15. completed 后不允许继续落子
# ═══════════════════════════════════════════════════════════════════════════

def test_cannot_move_after_completed(sandbox):
    fen = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 2"
    state = make_initial_state(fen)
    completed_state = apply_move(state, "d8h4")
    assert completed_state["status"] == "completed"
    with pytest.raises(ValueError, match="棋局已结束"):
        apply_move(completed_state, "a2a3")


# ═══════════════════════════════════════════════════════════════════════════
# 16. legal_moves 返回合法 UCI 列表
# ═══════════════════════════════════════════════════════════════════════════

def test_legal_moves_returns_uci_list(sandbox):
    state = make_initial_state()
    moves = legal_moves_uci(state)
    assert isinstance(moves, list)
    assert len(moves) == 20  # standard opening has 20 legal moves
    assert "e2e4" in moves
    assert "d2d4" in moves
    assert "g1f3" in moves
    # All must be valid UCI strings (4-5 chars)
    for m in moves:
        assert 4 <= len(m) <= 5, f"unexpected UCI length: {m!r}"


# ═══════════════════════════════════════════════════════════════════════════
# 17. move_history 顺序正确，含 uci/san/player/fen_after
# ═══════════════════════════════════════════════════════════════════════════

def test_move_history_correct(sandbox):
    state = make_initial_state()
    s1 = apply_move(state, "e2e4")
    s2 = apply_move(s1, "e7e5")
    s3 = apply_move(s2, "g1f3")

    history = s3["move_history"]
    assert len(history) == 3

    m1, m2, m3 = history
    assert m1["uci"] == "e2e4"
    assert m1["san"] == "e4"
    assert m1["player"] == "white"
    assert m1["move_no"] == 1
    assert m1["fen_after"]

    assert m2["uci"] == "e7e5"
    assert m2["san"] == "e5"
    assert m2["player"] == "black"
    assert m2["move_no"] == 1

    assert m3["uci"] == "g1f3"
    assert m3["san"] == "Nf3"
    assert m3["player"] == "white"
    assert m3["move_no"] == 2

    # fen_after for each move must differ
    fens = [m["fen_after"] for m in history]
    assert len(set(fens)) == 3


# ═══════════════════════════════════════════════════════════════════════════
# 18. uid + char_id 隔离
# ═══════════════════════════════════════════════════════════════════════════

def test_uid_char_id_isolation(sandbox):
    s1 = _make_chess_session(sandbox, uid="user1", char_id="yexuan")
    s2 = _make_chess_session(sandbox, uid="user2", char_id="yexuan")

    d1 = sandbox.activity_session_dir(
        char_id="yexuan", uid="user1", activity_type="chess", session_id=s1.session_id
    )
    d2 = sandbox.activity_session_dir(
        char_id="yexuan", uid="user2", activity_type="chess", session_id=s2.session_id
    )
    assert d1 != d2

    # user1 active → visible
    active1 = activity_store.find_active_session("yexuan", "user1", "chess")
    active2 = activity_store.find_active_session("yexuan", "user2", "chess")
    assert active1.session_id == s1.session_id
    assert active2.session_id == s2.session_id


# ═══════════════════════════════════════════════════════════════════════════
# 19. yexuan/hongcha 不共用 chess session
# ═══════════════════════════════════════════════════════════════════════════

def test_different_chars_not_shared(sandbox):
    sy = _make_chess_session(sandbox, uid="user1", char_id="yexuan")
    sh = _make_chess_session(sandbox, uid="user1", char_id="hongcha")

    assert sy.session_id != sh.session_id

    ay = activity_store.find_active_session("yexuan", "user1", "chess")
    ah = activity_store.find_active_session("hongcha", "user1", "chess")
    assert ay is not None and ah is not None
    assert ay.char_id == "yexuan"
    assert ah.char_id == "hongcha"
    assert ay.session_id != ah.session_id

    # Paths must not overlap
    dy = sandbox.activity_session_dir(
        char_id="yexuan", uid="user1", activity_type="chess", session_id=sy.session_id
    )
    dh = sandbox.activity_session_dir(
        char_id="hongcha", uid="user1", activity_type="chess", session_id=sh.session_id
    )
    assert "yexuan" in str(dy)
    assert "hongcha" in str(dh)
    assert dy != dh


# ═══════════════════════════════════════════════════════════════════════════
# 20. close 后 state 不再返回 active session
# ═══════════════════════════════════════════════════════════════════════════

def test_close_hides_active_session(sandbox):
    session = _make_chess_session(sandbox)
    active_before = activity_store.find_active_session("yexuan", "user1", "chess")
    assert active_before is not None

    activity_store.close_session("yexuan", "user1", "chess", session.session_id)

    active_after = activity_store.find_active_session("yexuan", "user1", "chess")
    assert active_after is None

    loaded = activity_store.load_session("yexuan", "user1", "chess", session.session_id)
    assert loaded is not None
    assert loaded.status == "closed"


# ═══════════════════════════════════════════════════════════════════════════
# 21. 不写 short_term / history / user_hidden_state
# ═══════════════════════════════════════════════════════════════════════════

def test_no_side_effect_writes(sandbox):
    session = _make_chess_session(sandbox)
    state = session.state
    s1 = apply_move(state, "e2e4")
    activity_store.update_state("yexuan", "user1", "chess", session.session_id, s1)

    # No history directory write
    history_dir = sandbox._p("history")
    chars_history = sandbox._p("chars", "yexuan", "history")
    for p in (history_dir, chars_history):
        if p.exists():
            assert list(p.iterdir()) == [], f"unexpected write in {p}"

    # No user_hidden_state write
    hidden = sandbox._p("runtime", "memory", "yexuan", "user1", "user_hidden_state.json")
    assert not hidden.exists()

    # Session data is only in activity directory
    session_dir = sandbox.activity_session_dir(
        char_id="yexuan", uid="user1", activity_type="chess", session_id=session.session_id
    )
    assert (session_dir / "session.json").exists()


# ═══════════════════════════════════════════════════════════════════════════
# 22. 非法 session_id 不能路径逃逸
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("evil_id", [
    "../evil",
    "../../etc/passwd",
    "/abs/path",
    "a/b",
    "a\\b",
    ".",
    "..",
])
def test_session_id_no_path_traversal(sandbox, evil_id):
    with pytest.raises((ValueError, Exception)):
        sandbox.activity_session_dir(
            char_id="yexuan", uid="user1", activity_type="chess", session_id=evil_id
        )


# ═══════════════════════════════════════════════════════════════════════════
# 23. 自定义 FEN 非法时明确报错
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad_fen", [
    "not-a-fen",
    "8/8/8/8/8/8/8",         # incomplete
    "8/8/8/8/8/8/8/8 z - -",  # invalid turn character
    "",                        # empty string
])
def test_invalid_fen_raises(sandbox, bad_fen):
    with pytest.raises(ValueError, match="无效的 FEN"):
        make_initial_state(bad_fen)


# ═══════════════════════════════════════════════════════════════════════════
# 24. 不接 Stockfish / 外部 API / LLM
# ═══════════════════════════════════════════════════════════════════════════

def test_no_external_dependencies():
    """chess.py must not import subprocess, requests, openai, or http clients."""
    import importlib, sys

    mod = sys.modules.get("core.activity.chess")
    if mod is None:
        mod = importlib.import_module("core.activity.chess")

    source_file = mod.__file__
    assert source_file is not None
    source = open(source_file, encoding="utf-8").read()

    forbidden = ["subprocess", "requests", "openai", "httpx", "aiohttp",
                 "stockfish", "urllib.request"]
    for name in forbidden:
        assert name not in source, f"chess.py must not use {name!r}"


def test_apply_move_uses_only_python_chess():
    """Verify apply_move doesn't call any network or shell during normal use."""
    import socket
    original_connect = socket.socket.connect

    calls: list[str] = []

    def patched_connect(self, address):
        calls.append(str(address))
        return original_connect(self, address)

    socket.socket.connect = patched_connect
    try:
        state = make_initial_state()
        apply_move(state, "e2e4")
    finally:
        socket.socket.connect = original_connect

    assert not calls, f"Unexpected network call during apply_move: {calls}"


# ═══════════════════════════════════════════════════════════════════════════
# bonus: SAN 支持
# ═══════════════════════════════════════════════════════════════════════════

def test_san_move_accepted(sandbox):
    """SAN moves (e.g. 'e4', 'Nf3') must also be accepted."""
    state = make_initial_state()
    new_state = apply_move(state, "e4")
    board = _chess_lib.Board(new_state["fen"])
    assert board.piece_at(_chess_lib.E4) == _chess_lib.Piece(_chess_lib.PAWN, _chess_lib.WHITE)
    assert new_state["last_move"]["san"] == "e4"


def test_san_castling_accepted(sandbox):
    fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    state = make_initial_state(fen)
    new_state = apply_move(state, "O-O")
    board = _chess_lib.Board(new_state["fen"])
    assert board.piece_at(_chess_lib.G1) == _chess_lib.Piece(_chess_lib.KING, _chess_lib.WHITE)
