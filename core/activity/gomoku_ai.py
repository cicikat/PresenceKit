"""
Gomoku AI — 本地启发式五子棋对手 (P1)

不接 LLM / 外部 API / Dream / trigger / scheduler。
纯代码 move generator，胜负仍由 gomoku.check_win 负责。

评分 = 进攻分 + 防守分 + 中心偏置 + 相邻偏置
风格 (serious/balanced/gentle/teaching) 只影响候选选点策略，不影响规则。
"""
from __future__ import annotations

import random

_DIRS = [(1, 0), (0, 1), (1, 1), (1, -1)]
_CENTER = 7  # 15×15 棋盘中心坐标

# (count, open_ends) → 启发式分值；count >= 5 时直接返回 _WIN_SCORE
_LINE_SCORES: dict[tuple[int, int], int] = {
    (4, 2): 10000,  # 活四
    (4, 1): 1000,   # 冲四
    (4, 0): 10,
    (3, 2): 1000,   # 活三
    (3, 1): 100,    # 眠三
    (3, 0): 10,
    (2, 2): 100,    # 活二
    (2, 1): 10,
    (2, 0): 1,
    (1, 2): 10,
    (1, 1): 1,
    (1, 0): 1,
}

_WIN_SCORE = 100_000


def _count_line_info(
    board: list[list],
    x: int,
    y: int,
    player: str,
    dx: int,
    dy: int,
    size: int,
) -> tuple[int, int]:
    """
    沿 (dx,dy) 两方向统计以 (x,y) 为起点的连续同色棋子数和开放端数。
    调用前 board[y][x] 必须已设为 player。
    返回 (count, open_ends)，open_ends ∈ {0, 1, 2}。
    """
    count = 1
    open_ends = 0
    for sign in (1, -1):
        nx, ny = x + sign * dx, y + sign * dy
        while 0 <= nx < size and 0 <= ny < size and board[ny][nx] == player:
            count += 1
            nx += sign * dx
            ny += sign * dy
        if 0 <= nx < size and 0 <= ny < size and board[ny][nx] is None:
            open_ends += 1
    return count, open_ends


def _score_position(board: list[list], x: int, y: int, player: str, size: int) -> int:
    """
    评估在 (x,y) 放置 player 棋子后的进攻价值（调用前须临时置棋）。
    五连立即返回 _WIN_SCORE。
    """
    total = 0
    for dx, dy in _DIRS:
        count, open_ends = _count_line_info(board, x, y, player, dx, dy, size)
        if count >= 5:
            return _WIN_SCORE
        total += _LINE_SCORES.get((count, open_ends), 0)
    return total


def _center_bias(x: int, y: int) -> int:
    return max(0, _CENTER - max(abs(x - _CENTER), abs(y - _CENTER)))


def _adjacency_bias(board: list[list], x: int, y: int, size: int) -> int:
    """8连通邻居中已有棋子的数量。"""
    count = 0
    for ddx in (-1, 0, 1):
        for ddy in (-1, 0, 1):
            if ddx == 0 and ddy == 0:
                continue
            nx, ny = x + ddx, y + ddy
            if 0 <= nx < size and 0 <= ny < size and board[ny][nx] is not None:
                count += 1
    return count


def choose_gomoku_ai_move(
    board: list[list],
    ai_player: str,
    style: str = "balanced",
    size: int = 15,
) -> tuple[int, int]:
    """
    为 ai_player 在当前 board 上选择落点。
    保证合法（在棋盘内、格子为空）。
    不修改 board。
    """
    # 空棋盘直接走中心
    if not any(board[r][c] is not None for r in range(size) for c in range(size)):
        return _CENTER, _CENTER

    opponent = "white" if ai_player == "black" else "black"
    empty = [(x, y) for y in range(size) for x in range(size) if board[y][x] is None]
    if not empty:
        raise ValueError("棋盘已满，无合法落点")

    # 对每个空格计算：进攻分 + 防守分 + 中心偏置 + 相邻偏置
    # (total, attack, defense, x, y)
    candidates: list[tuple[int, int, int, int, int]] = []
    for x, y in empty:
        board[y][x] = ai_player
        attack = _score_position(board, x, y, ai_player, size)
        board[y][x] = None

        board[y][x] = opponent
        defense = _score_position(board, x, y, opponent, size)
        board[y][x] = None

        center = _center_bias(x, y)
        adj = _adjacency_bias(board, x, y, size)
        total = attack + defense + center * 2 + adj * 10
        candidates.append((total, attack, defense, x, y))

    candidates.sort(reverse=True)
    return _apply_style(candidates, style, board, ai_player, opponent, size)


def _apply_style(
    candidates: list[tuple[int, int, int, int, int]],
    style: str,
    board: list[list],
    ai_player: str,
    opponent: str,
    size: int,
) -> tuple[int, int]:
    if style == "serious":
        # 选最高分
        return candidates[0][3], candidates[0][4]

    if style == "balanced":
        # 在 top-3 中加权随机
        top = candidates[:3]
        weights = [3, 2, 1][: len(top)]
        chosen = random.choices(top, weights=weights, k=1)[0]
        return chosen[3], chosen[4]

    if style == "gentle":
        # 必须防守：对手下一步能赢，立即堵
        must_block = [(t, a, d, x, y) for t, a, d, x, y in candidates if d >= _WIN_SCORE]
        if must_block:
            return must_block[0][3], must_block[0][4]
        # 过滤 AI 能立即赢的点，从 top-5 非赢点中随机选（温和）
        non_win = [(t, a, d, x, y) for t, a, d, x, y in candidates if a < _WIN_SCORE]
        pool = non_win[:5] if non_win else candidates[:1]
        chosen = random.choice(pool)
        return chosen[3], chosen[4]

    if style == "teaching":
        # 额外提升能形成/堵截活三的棋形得分
        boosted: list[tuple[int, int, int, int, int]] = []
        for total, attack, defense, x, y in candidates:
            boost = 0
            board[y][x] = ai_player
            for dx, dy in _DIRS:
                count, open_ends = _count_line_info(board, x, y, ai_player, dx, dy, size)
                if count == 3 and open_ends >= 1:
                    boost += 500
            board[y][x] = None
            board[y][x] = opponent
            for dx, dy in _DIRS:
                count, open_ends = _count_line_info(board, x, y, opponent, dx, dy, size)
                if count == 3 and open_ends >= 1:
                    boost += 300
            board[y][x] = None
            boosted.append((total + boost, attack, defense, x, y))
        boosted.sort(reverse=True)
        return boosted[0][3], boosted[0][4]

    # 未知 style → fallback serious
    return candidates[0][3], candidates[0][4]
