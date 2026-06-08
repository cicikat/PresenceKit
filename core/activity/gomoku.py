"""
Gomoku Activity — 五子棋规则引擎 (P0)

职责：
- 纯代码判棋：落子合法性、胜负判断。
- 不接 LLM / trigger / Dream / scheduler / short_term / user_hidden_state。
- 规则：15x15，黑棋先手，横/竖/左斜/右斜五连胜，P0 不做禁手。
"""
from __future__ import annotations

from typing import Optional

from core.activity.store import (
    close_session,
    create_session,
    find_active_session,
    load_session,
    update_state,
)
from core.activity.session import ActivitySession

BOARD_SIZE = 15

# 四个方向向量：横 / 竖 / 右斜 / 左斜
_DIRS = [(1, 0), (0, 1), (1, 1), (1, -1)]


# ── 棋盘工具 ──────────────────────────────────────────────────────────────────

def _make_board(size: int) -> list[list[Optional[str]]]:
    return [[None] * size for _ in range(size)]


def _count_line(
    board: list[list],
    x: int,
    y: int,
    player: str,
    dx: int,
    dy: int,
    size: int,
) -> list[tuple[int, int]]:
    """从 (x,y) 出发沿 (dx,dy) 和反方向收集连续同色棋子坐标。"""
    cells: list[tuple[int, int]] = [(x, y)]
    for sign in (1, -1):
        nx, ny = x + sign * dx, y + sign * dy
        while 0 <= nx < size and 0 <= ny < size and board[ny][nx] == player:
            cells.append((nx, ny))
            nx += sign * dx
            ny += sign * dy
    return cells


def check_win(
    board: list[list],
    x: int,
    y: int,
    player: str,
    size: int,
) -> Optional[list[tuple[int, int]]]:
    """检查落子 (x,y) 后是否五连；胜则返回连线坐标列表，否则返回 None。"""
    for dx, dy in _DIRS:
        cells = _count_line(board, x, y, player, dx, dy, size)
        if len(cells) >= 5:
            return sorted(cells)
    return None


def _initial_state(board_size: int) -> dict:
    return {
        "board_size": board_size,
        "board": _make_board(board_size),
        "current_turn": "black",
        "move_history": [],
        "status": "active",
        "winner": None,
        "last_move": None,
    }


# ── 公开接口 ──────────────────────────────────────────────────────────────────

def start_game(uid: str, char_id: str, board_size: int = BOARD_SIZE) -> ActivitySession:
    """开局，创建 gomoku session（同类型旧 session 自动关闭）。"""
    if board_size != 15:
        raise ValueError(f"P0 只支持 board_size=15，收到 {board_size}")
    state = _initial_state(board_size)
    return create_session(uid, char_id, "gomoku", state)


def get_active_session(uid: str, char_id: str) -> Optional[ActivitySession]:
    """返回当前 active gomoku session，无则返回 None。"""
    return find_active_session(char_id, uid, "gomoku")


def make_move(
    uid: str,
    char_id: str,
    session_id: str,
    x: int,
    y: int,
) -> dict:
    """
    落子，返回更新后的游戏状态字典。

    出错时抛 ValueError：
    - session 不存在或已关闭
    - 棋局已结束（winner 已产生）
    - 坐标越界
    - 格子已有棋子
    """
    session = load_session(char_id, uid, "gomoku", session_id)
    if session is None:
        raise ValueError(f"session {session_id!r} 不存在")
    if session.status != "active":
        raise ValueError(f"session {session_id!r} 已关闭，不能继续落子")

    state = session.state
    if state.get("status") != "active":
        raise ValueError(f"棋局已结束（{state.get('status')}），不能继续落子")

    size = state["board_size"]
    if not (0 <= x < size and 0 <= y < size):
        raise ValueError(f"坐标 ({x}, {y}) 超出棋盘范围 [0, {size - 1}]")

    board = state["board"]
    if board[y][x] is not None:
        raise ValueError(f"({x}, {y}) 已有棋子（{board[y][x]}），不可重复落子")

    player = state["current_turn"]
    board[y][x] = player

    move_no = len(state["move_history"]) + 1
    move = {"x": x, "y": y, "player": player, "move_no": move_no}
    state["move_history"].append(move)
    state["last_move"] = move

    win_line = check_win(board, x, y, player, size)
    if win_line is not None:
        state["status"] = "completed"
        state["winner"] = player
        # 胜后不切换，保持落子方记录
    else:
        state["current_turn"] = "white" if player == "black" else "black"

    update_state(char_id, uid, "gomoku", session_id, state)

    result: dict = {
        "board": board,
        "last_move": move,
        "current_turn": state["current_turn"],
        "status": state["status"],
        "winner": state["winner"],
    }
    if win_line is not None:
        result["win_line"] = [{"x": c[0], "y": c[1]} for c in win_line]
    return result


def close_game(uid: str, char_id: str, session_id: str) -> Optional[ActivitySession]:
    """关闭棋局（不写长期记忆）。"""
    return close_session(char_id, uid, "gomoku", session_id)
