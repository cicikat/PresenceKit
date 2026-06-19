"""
Chess AI — 本地极小化对手 (P1)

不接 Stockfish / 外部 API / LLM / Dream / trigger / scheduler。
纯 python-chess minimax + alpha-beta，depth 2-3。

ai_style 影响搜索深度和随机扰动：
  serious   → depth 3，总选最优
  balanced  → depth 2，top-3 加权随机
  gentle    → depth 2，避免必杀手（除非必须防守）
  teaching  → depth 2，倾向兑子/活跃棋形
"""
from __future__ import annotations

import random
from typing import Optional

import chess

# 子力价值（厘升格）
_PIECE_VALUE: dict[chess.PieceType, int] = {
    chess.PAWN:   100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK:   500,
    chess.QUEEN:  900,
    chess.KING:   20_000,
}

_WIN_SCORE = 100_000


def _material(board: chess.Board, color: chess.Color) -> int:
    total = 0
    for pt, val in _PIECE_VALUE.items():
        total += len(board.pieces(pt, color)) * val
    return total


def _evaluate(board: chess.Board, ai_color: chess.Color) -> int:
    """Static evaluation: material balance from AI's perspective."""
    if board.is_checkmate():
        # the side to move is checkmated — bad for whoever is to move
        return -_WIN_SCORE if board.turn == ai_color else _WIN_SCORE
    if board.is_game_over(claim_draw=False):
        return 0
    return _material(board, ai_color) - _material(board, not ai_color)


def _minimax(
    board: chess.Board,
    depth: int,
    alpha: int,
    beta: int,
    maximizing: bool,
    ai_color: chess.Color,
) -> int:
    if depth == 0 or board.is_game_over(claim_draw=False):
        return _evaluate(board, ai_color)

    if maximizing:
        best = -_WIN_SCORE - 1
        for move in board.legal_moves:
            board.push(move)
            val = _minimax(board, depth - 1, alpha, beta, False, ai_color)
            board.pop()
            best = max(best, val)
            alpha = max(alpha, best)
            if alpha >= beta:
                break
        return best
    else:
        best = _WIN_SCORE + 1
        for move in board.legal_moves:
            board.push(move)
            val = _minimax(board, depth - 1, alpha, beta, True, ai_color)
            board.pop()
            best = min(best, val)
            beta = min(beta, best)
            if alpha >= beta:
                break
        return best


def choose_chess_ai_move(
    board: chess.Board,
    ai_color: chess.Color,
    ai_style: str = "balanced",
) -> Optional[chess.Move]:
    """
    选择 AI 落子。返回 chess.Move；棋局结束或无合法走法时返回 None。
    不修改 board。
    """
    if board.is_game_over(claim_draw=False):
        return None
    if board.turn != ai_color:
        return None

    legal = list(board.legal_moves)
    if not legal:
        return None

    depth = 3 if ai_style == "serious" else 2

    scored: list[tuple[int, chess.Move]] = []
    for move in legal:
        board.push(move)
        val = _minimax(board, depth - 1, -_WIN_SCORE - 1, _WIN_SCORE + 1, False, ai_color)
        board.pop()
        scored.append((val, move))

    scored.sort(key=lambda t: t[0], reverse=True)
    return _apply_style(scored, board, ai_color, ai_style)


def _apply_style(
    scored: list[tuple[int, chess.Move]],
    board: chess.Board,
    ai_color: chess.Color,
    style: str,
) -> chess.Move:
    if style == "serious":
        return scored[0][1]

    if style == "balanced":
        top = scored[:3]
        weights = [3, 2, 1][: len(top)]
        return random.choices([t[1] for t in top], weights=weights, k=1)[0]

    if style == "gentle":
        best_val = scored[0][0]
        # 若有即杀，只在必须防守时才走；否则主动避开
        if best_val >= _WIN_SCORE:
            # 检查对手有没有即杀——若有，必须防守，选最优
            opponent = not ai_color
            board_copy = board.copy()
            opponent_has_win = False
            for mv in board_copy.legal_moves:
                board_copy.push(mv)
                if board_copy.is_checkmate():
                    opponent_has_win = True
                board_copy.pop()
                if opponent_has_win:
                    break
            if opponent_has_win:
                return scored[0][1]
            # 过滤掉即杀手
            non_win = [(v, m) for v, m in scored if v < _WIN_SCORE]
            pool = non_win[:5] if non_win else scored[:1]
        else:
            pool = scored[:5]
        return random.choice([t[1] for t in pool])

    if style == "teaching":
        # 偏好兑子（material exchange）和将军，增加 +200 bonus
        boosted: list[tuple[int, chess.Move]] = []
        for val, move in scored:
            bonus = 0
            board.push(move)
            if board.is_check():
                bonus += 200
            if board.is_capture(move):
                bonus += 150
            board.pop()
            boosted.append((val + bonus, move))
        boosted.sort(key=lambda t: t[0], reverse=True)
        return boosted[0][1]

    return scored[0][1]
