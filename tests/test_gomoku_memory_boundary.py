"""
tests/test_gomoku_memory_boundary.py

Gomoku Activity 记忆边界 P0 验收测试（17 用例）

覆盖：
T1.  move_count=12（阈值）→ 不生成摘要，返回 (session, None)
T2.  move_count=6（远低于阈值）→ 不生成摘要
T3.  move_count=0（空局）→ 不生成摘要
T4.  move_count=13（刚过阈值）→ 生成摘要，返回 (session, str)
T5.  move_count>12 → summary.json 写入磁盘
T6.  summary 文本不含完整棋谱（不含坐标序列）
T7.  summary 文本不含 "[" 方括号（棋谱列表标志）
T8.  human 模式文案不含"叶瑄"
T9.  yexuan_ai 模式文案含"叶瑄执白"
T10. summary 文本含正确 move_count 数值
T11. winner=black → 黑棋获胜
T12. winner=white → 白棋获胜
T13. winner=None → 未分胜负
T14. move_count>12 close 后不写 short_term history 目录
T15. move_count>12 close 后不写 user_hidden_state 文件
T16. 返回值在 move_count>12 时包含 summary_text 字符串
T17. 返回值在 move_count<=12 时 summary 为 None
"""
from __future__ import annotations

import pytest

from core.activity import gomoku as G
from core.activity import store as activity_store


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _start(uid="user1", char_id="yexuan", opponent="human"):
    return G.start_game(uid, char_id, opponent=opponent)


def _inject_state(
    uid: str,
    char_id: str,
    session_id: str,
    move_count: int,
    opponent: str = "human",
    winner: str | None = None,
) -> None:
    """直接向 session.state 注入指定数量的 move_history，跳过真实落子逻辑。

    坐标散布在棋盘各角，不会形成五连。
    """
    session = activity_store.load_session(char_id, uid, "gomoku", session_id)
    assert session is not None
    state = session.state

    # 生成不会连续五连的坐标序列：沿棋盘对角线间隔排列
    moves = []
    for i in range(move_count):
        col = (i * 3) % 15
        row = (i * 7 + 1) % 15
        player = "black" if i % 2 == 0 else "white"
        state["board"][row][col] = player
        moves.append({"x": col, "y": row, "player": player, "move_no": i + 1})

    state["move_history"] = moves
    state["last_move"] = moves[-1] if moves else None
    state["opponent"] = opponent
    if winner is not None:
        state["status"] = "completed"
        state["winner"] = winner

    activity_store.update_state(char_id, uid, "gomoku", session_id, state)


# ═══════════════════════════════════════════════════════════════════════════════
# T1 — move_count = 12（阈值边界）→ 不生成摘要
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_summary_at_threshold(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=12)
    closed, summary = G.close_game("user1", "yexuan", session.session_id)
    assert closed is not None
    assert summary is None
    loaded = activity_store.load_summary("yexuan", "user1", "gomoku", session.session_id)
    assert loaded is None


# ═══════════════════════════════════════════════════════════════════════════════
# T2 — move_count = 6 → 不生成摘要
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_summary_below_threshold(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=6)
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is None


# ═══════════════════════════════════════════════════════════════════════════════
# T3 — move_count = 0（空局）→ 不生成摘要
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_summary_zero_moves(sandbox):
    session = _start()
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is None
    loaded = activity_store.load_summary("yexuan", "user1", "gomoku", session.session_id)
    assert loaded is None


# ═══════════════════════════════════════════════════════════════════════════════
# T4 — move_count = 13（刚过阈值）→ 生成摘要
# ═══════════════════════════════════════════════════════════════════════════════

def test_summary_generated_above_threshold(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=13)
    closed, summary = G.close_game("user1", "yexuan", session.session_id)
    assert closed is not None
    assert summary is not None
    assert isinstance(summary, str)
    assert len(summary) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# T5 — move_count > 12 → summary.json 写入磁盘
# ═══════════════════════════════════════════════════════════════════════════════

def test_summary_json_persisted(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=20)
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is not None

    loaded = activity_store.load_summary("yexuan", "user1", "gomoku", session.session_id)
    assert loaded is not None
    assert loaded["text"] == summary
    assert loaded["move_count"] == 20


# ═══════════════════════════════════════════════════════════════════════════════
# T6 — summary 文本不含完整棋谱（坐标序列字符串）
# ═══════════════════════════════════════════════════════════════════════════════

def test_summary_no_full_move_record(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=15)
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is not None
    # 棋谱坐标形如 "x": 7  或  "(7, 7)"
    assert '"x"' not in summary
    assert '"y"' not in summary
    assert "(7, 7)" not in summary


# ═══════════════════════════════════════════════════════════════════════════════
# T7 — summary 文本不含方括号（列表结构标志）
# ═══════════════════════════════════════════════════════════════════════════════

def test_summary_no_bracket_list(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=14)
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is not None
    assert "[" not in summary
    assert "]" not in summary


# ═══════════════════════════════════════════════════════════════════════════════
# T8 — human 模式文案不含"叶瑄"
# ═══════════════════════════════════════════════════════════════════════════════

def test_human_mode_no_yexuan_reference(sandbox):
    session = _start(opponent="human")
    _inject_state("user1", "yexuan", session.session_id, move_count=14, opponent="human")
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is not None
    assert "叶瑄" not in summary


# ═══════════════════════════════════════════════════════════════════════════════
# T9 — yexuan_ai 模式文案含"叶瑄执白"
# ═══════════════════════════════════════════════════════════════════════════════

def test_yexuan_ai_mode_mentions_yexuan_white(sandbox):
    session = _start(opponent="yexuan_ai")
    _inject_state("user1", "yexuan", session.session_id, move_count=14, opponent="yexuan_ai")
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is not None
    assert "叶瑄执白" in summary


# ═══════════════════════════════════════════════════════════════════════════════
# T10 — summary 文本含正确 move_count 数值
# ═══════════════════════════════════════════════════════════════════════════════

def test_summary_contains_move_count(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=18)
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is not None
    assert "18" in summary


# ═══════════════════════════════════════════════════════════════════════════════
# T11 — winner=black → "黑棋获胜"
# ═══════════════════════════════════════════════════════════════════════════════

def test_summary_winner_black(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=14, winner="black")
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is not None
    assert "黑棋获胜" in summary


# ═══════════════════════════════════════════════════════════════════════════════
# T12 — winner=white → "白棋获胜"
# ═══════════════════════════════════════════════════════════════════════════════

def test_summary_winner_white(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=14, winner="white")
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is not None
    assert "白棋获胜" in summary


# ═══════════════════════════════════════════════════════════════════════════════
# T13 — winner=None → "未分胜负"
# ═══════════════════════════════════════════════════════════════════════════════

def test_summary_no_winner(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=14, winner=None)
    _, summary = G.close_game("user1", "yexuan", session.session_id)
    assert summary is not None
    assert "未分胜负" in summary


# ═══════════════════════════════════════════════════════════════════════════════
# T14 — move_count > 12 close 后不写 short_term history 目录
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_short_term_written_after_close_with_summary(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=14)
    G.close_game("user1", "yexuan", session.session_id)

    history_dir = sandbox._p("history")
    chars_history = sandbox._p("chars", "yexuan", "history")
    for p in (history_dir, chars_history):
        if p.exists():
            assert list(p.iterdir()) == [], f"unexpected write in {p}"


# ═══════════════════════════════════════════════════════════════════════════════
# T15 — move_count > 12 close 后不写 user_hidden_state 文件
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_hidden_state_written_after_close_with_summary(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=14)
    G.close_game("user1", "yexuan", session.session_id)

    hidden = sandbox._p("runtime", "memory", "yexuan", "user1", "user_hidden_state.json")
    assert not hidden.exists()


# ═══════════════════════════════════════════════════════════════════════════════
# T16 — 返回值在 move_count > 12 时包含 summary_text 字符串
# ═══════════════════════════════════════════════════════════════════════════════

def test_return_value_summary_text_above_threshold(sandbox):
    session = _start(opponent="yexuan_ai")
    _inject_state("user1", "yexuan", session.session_id, move_count=15, opponent="yexuan_ai")
    closed, summary = G.close_game("user1", "yexuan", session.session_id)
    assert closed is not None
    assert closed.status == "closed"
    assert isinstance(summary, str)
    assert "五子棋" in summary


# ═══════════════════════════════════════════════════════════════════════════════
# T17 — 返回值在 move_count <= 12 时 summary 为 None
# ═══════════════════════════════════════════════════════════════════════════════

def test_return_value_none_below_threshold(sandbox):
    session = _start()
    _inject_state("user1", "yexuan", session.session_id, move_count=10)
    closed, summary = G.close_game("user1", "yexuan", session.session_id)
    assert closed is not None
    assert summary is None
