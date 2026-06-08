"""
Chess Activity 棋局逻辑 (P0)

规则、合法性、胜负全部由 python-chess 负责。
不接 Stockfish / 外部 API / LLM / Dream / trigger / scheduler / perceive_event。
不写 short_term / user_hidden_state。
"""
from __future__ import annotations

from typing import Optional

import chess

STARTING_FEN: str = chess.STARTING_FEN


def _turn_str(board: chess.Board) -> str:
    return "white" if board.turn == chess.WHITE else "black"


def _check_game_over(board: chess.Board) -> tuple[Optional[str], Optional[str]]:
    """Return (result, termination) if the game is over, else (None, None).

    Uses claim_draw=False so only automatic draws (75-move rule, fivefold
    repetition, insufficient material) are detected; 50-move / threefold
    repetition claims are NOT automatic and are ignored here.
    """
    if not board.is_game_over(claim_draw=False):
        return None, None
    outcome = board.outcome(claim_draw=False)
    if outcome is None:
        return None, None
    return outcome.result(), outcome.termination.name.lower()


def make_initial_state(fen: Optional[str] = None) -> dict:
    """Return a fresh chess state dict.

    Uses the standard starting position when fen is None.
    Raises ValueError for an invalid FEN string.
    """
    if fen is None:
        board = chess.Board()
    else:
        try:
            board = chess.Board(fen)
        except Exception as e:
            raise ValueError(f"无效的 FEN: {e}") from e

    return {
        "fen": board.fen(),
        "turn": _turn_str(board),
        "status": "active",
        "result": None,
        "termination": None,
        "move_history": [],
        "last_move": None,
    }


def apply_move(state: dict, move_str: str) -> dict:
    """Apply move_str (UCI or SAN) and return the updated state.

    Tries UCI first, then SAN.  Raises ValueError for illegal or
    unparseable moves, or if the game has already ended.
    """
    if state.get("status") != "active":
        raise ValueError(f"棋局已结束，无法落子: status={state.get('status')!r}")

    board = chess.Board(state["fen"])

    move: Optional[chess.Move] = None
    uci_candidate: Optional[chess.Move] = None

    # ── Try UCI ───────────────────────────────────────────────────────────────
    try:
        uci_candidate = chess.Move.from_uci(move_str)
        if uci_candidate in board.legal_moves:
            move = uci_candidate
    except chess.InvalidMoveError:
        pass

    # ── Fallback to SAN ───────────────────────────────────────────────────────
    if move is None:
        try:
            move = board.parse_san(move_str)
        except chess.IllegalMoveError:
            raise ValueError(f"非法走法: {move_str!r}")
        except chess.InvalidMoveError:
            if uci_candidate is not None:
                raise ValueError(f"非法走法: {move_str!r}")
            raise ValueError(f"无法解析走法: {move_str!r}")

    player = _turn_str(board)
    move_no = board.fullmove_number
    san = board.san(move)
    uci = move.uci()

    board.push(move)

    new_fen = board.fen()
    entry = {
        "move_no": move_no,
        "uci": uci,
        "san": san,
        "player": player,
        "fen_after": new_fen,
    }

    history = list(state.get("move_history") or [])
    history.append(entry)

    result, termination = _check_game_over(board)
    game_status = "completed" if result is not None else "active"

    return {
        "fen": new_fen,
        "turn": _turn_str(board),
        "status": game_status,
        "result": result,
        "termination": termination,
        "move_history": history,
        "last_move": entry,
    }


def legal_moves_uci(state: dict) -> list[str]:
    """Return all legal moves in UCI notation for the current position."""
    board = chess.Board(state["fen"])
    return sorted(m.uci() for m in board.legal_moves)
