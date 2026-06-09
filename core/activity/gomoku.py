"""
Gomoku Activity — 五子棋规则引擎 (P1 + P2-memory-boundary)

P0：纯代码判棋，双人裁判。
P1 新增：
- 本地 AI 对手（choose_gomoku_ai_move）
- opponent / ai_player / ai_style session state 字段
- start_game 接受 opponent / ai_style 参数
- make_move 用户落子后，若 opponent=yexuan_ai 且当前轮到 AI，自动落一手

P2 新增（记忆边界）：
- close_game 按步数阈值决定是否生成/写入对局摘要（见 SUMMARY_THRESHOLD）
- move_count > SUMMARY_THRESHOLD：生成轻量摘要写入 session/summary.json
- move_count <= SUMMARY_THRESHOLD：视为噪声/误触，跳过，仅记日志

禁止：不接 LLM / trigger / Dream / scheduler / short_term / user_hidden_state。
"""
from __future__ import annotations

import logging
from typing import Optional

from core.activity.gomoku_ai import choose_gomoku_ai_move
from core.activity.store import (
    close_session,
    create_session,
    find_active_session,
    load_session,
    save_summary,
    update_state,
)
from core.activity.session import ActivitySession

logger = logging.getLogger(__name__)

# 对局总步数须 > 此阈值才生成摘要（≤ 视为误触/试棋噪声）
SUMMARY_THRESHOLD = 12

BOARD_SIZE = 15

# 四个方向向量：横 / 竖 / 右斜 / 左斜
_DIRS = [(1, 0), (0, 1), (1, 1), (1, -1)]

_VALID_OPPONENTS = frozenset({"human", "yexuan_ai"})
_VALID_STYLES = frozenset({"balanced", "gentle", "serious", "teaching"})


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


def _initial_state(
    board_size: int,
    opponent: str = "human",
    ai_style: str = "balanced",
) -> dict:
    return {
        "board_size": board_size,
        "board": _make_board(board_size),
        "current_turn": "black",
        "move_history": [],
        "status": "active",
        "winner": None,
        "last_move": None,
        "opponent": opponent,
        "ai_player": "white",
        "ai_style": ai_style,
    }


# ── 公开接口 ──────────────────────────────────────────────────────────────────

def start_game(
    uid: str,
    char_id: str,
    board_size: int = BOARD_SIZE,
    opponent: str = "human",
    ai_style: str = "balanced",
) -> ActivitySession:
    """开局，创建 gomoku session（同类型旧 session 自动关闭）。"""
    if board_size != 15:
        raise ValueError(f"P0 只支持 board_size=15，收到 {board_size}")
    if opponent not in _VALID_OPPONENTS:
        raise ValueError(f"opponent 必须是 {sorted(_VALID_OPPONENTS)}，收到 {opponent!r}")
    if ai_style not in _VALID_STYLES:
        raise ValueError(f"ai_style 必须是 {sorted(_VALID_STYLES)}，收到 {ai_style!r}")
    state = _initial_state(board_size, opponent, ai_style)
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
    AI 模式下，用户落子后自动追加 AI 落子（除非用户已赢）。

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

    # ── 用户落子 ──────────────────────────────────────────────────────────────
    player = state["current_turn"]
    board[y][x] = player
    move_no = len(state["move_history"]) + 1
    move = {"x": x, "y": y, "player": player, "move_no": move_no}
    state["move_history"].append(move)
    state["last_move"] = move

    win_line = check_win(board, x, y, player, size)
    ai_win_line = None

    if win_line is not None:
        state["status"] = "completed"
        state["winner"] = player
        # 胜后不切换轮次
    else:
        state["current_turn"] = "white" if player == "black" else "black"

        # ── AI 自动落子（用户未赢时） ──────────────────────────────────────────
        if (
            state.get("opponent") == "yexuan_ai"
            and state["current_turn"] == state.get("ai_player", "white")
        ):
            ai_color: str = state["ai_player"]
            ai_style: str = state.get("ai_style", "balanced")

            ax, ay = choose_gomoku_ai_move(board, ai_color, ai_style, size)
            board[ay][ax] = ai_color

            ai_move_no = len(state["move_history"]) + 1
            ai_move = {
                "x": ax,
                "y": ay,
                "player": ai_color,
                "move_no": ai_move_no,
                "source": "ai",
                "style": ai_style,
            }
            state["move_history"].append(ai_move)
            state["last_move"] = ai_move

            ai_win_line = check_win(board, ax, ay, ai_color, size)
            if ai_win_line is not None:
                state["status"] = "completed"
                state["winner"] = ai_color
            else:
                state["current_turn"] = "white" if ai_color == "black" else "black"

    update_state(char_id, uid, "gomoku", session_id, state)

    result: dict = {
        "board": board,
        "last_move": state["last_move"],
        "move_history": state["move_history"],
        "current_turn": state["current_turn"],
        "status": state["status"],
        "winner": state["winner"],
    }
    final_win_line = ai_win_line if ai_win_line is not None else win_line
    if final_win_line is not None:
        result["win_line"] = [{"x": c[0], "y": c[1]} for c in final_win_line]
    return result


def build_game_summary(state: dict) -> str:
    """
    生成轻量对局摘要文本。

    只使用 move_count / winner / opponent，不含棋谱坐标列表。
    opponent=yexuan_ai 时写"叶瑄执白"；human 时写"本地双人对局"。
    """
    move_count = len(state.get("move_history", []))
    winner = state.get("winner")
    opponent = state.get("opponent", "human")

    if winner == "black":
        result = "黑棋获胜"
    elif winner == "white":
        result = "白棋获胜"
    else:
        result = "未分胜负"

    if opponent == "yexuan_ai":
        return (
            f"用户和叶瑄进行了一局五子棋。"
            f"用户执黑，叶瑄执白，对局共 {move_count} 手，结果：{result}。"
        )
    return f"用户进行了一局本地双人五子棋，对局共 {move_count} 手，结果：{result}。"


def close_game(
    uid: str,
    char_id: str,
    session_id: str,
) -> tuple[Optional[ActivitySession], Optional[str]]:
    """
    关闭棋局，按步数阈值决定是否生成对局摘要。

    返回 (session, summary_text)：
    - summary_text=None  →  move_count <= SUMMARY_THRESHOLD，视为噪声不写摘要
    - summary_text=str   →  move_count > SUMMARY_THRESHOLD，已写 session/summary.json

    主记忆接入待后续实现：当前摘要只落到 activity session 目录的 summary.json，
    不写 short_term / event_log / user_hidden_state。
    """
    session = close_session(char_id, uid, "gomoku", session_id)
    if session is None:
        return None, None

    state = session.state
    move_count = len(state.get("move_history", []))

    if move_count <= SUMMARY_THRESHOLD:
        logger.info(
            "[gomoku] skip memory summary: move_count=%d <= %d",
            move_count,
            SUMMARY_THRESHOLD,
        )
        return session, None

    summary_text = build_game_summary(state)
    save_summary(
        char_id=char_id,
        uid=uid,
        activity_type="gomoku",
        session_id=session_id,
        summary={
            "text": summary_text,
            "move_count": move_count,
            "winner": state.get("winner"),
            "opponent": state.get("opponent", "human"),
            "generated_at": session.updated_at,
        },
    )
    logger.info(
        "[gomoku] generated memory summary: move_count=%d session=%s",
        move_count,
        session_id,
    )
    return session, summary_text
