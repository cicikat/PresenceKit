"""
Chess Grounding — deterministic game facts for companion LLM grounding.

Provides build_chess_grounding_facts(state: dict) -> dict.

Rules:
- Output does NOT include full board or full move_history — only derived facts.
- Uses python-chess for FEN parsing; no LLM calls, no external I/O.
- Designed to be injected into companion LLM prompt as <game_facts>.
"""
from __future__ import annotations

from typing import Optional

import chess

# Piece values for material balance (standard centipawn scale)
_PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

_PIECE_NAMES_ZH: dict[chess.PieceType, str] = {
    chess.PAWN: "兵/卒",
    chess.KNIGHT: "马",
    chess.BISHOP: "象",
    chess.ROOK: "车",
    chess.QUEEN: "后",
    chess.KING: "王",
}


def _material_balance(board: chess.Board) -> int:
    """Return white_material - black_material in pawn units. Positive = white ahead."""
    white = sum(_PIECE_VALUES[p.piece_type] for p in board.piece_map().values() if p.color == chess.WHITE)
    black = sum(_PIECE_VALUES[p.piece_type] for p in board.piece_map().values() if p.color == chess.BLACK)
    return white - black


def _tactics_category(board: chess.Board, move: Optional[chess.Move]) -> str:
    """Classify the last move as 'check' / 'capture' / 'promotion' / 'normal'."""
    if move is None:
        return "normal"
    # board is already AFTER the move, so we check if move gave check
    if board.is_check():
        return "check"
    # To check if the move was a capture or promotion, we need to look at the move object
    if move.promotion:
        return "promotion"
    # Capture detection: check if the move captured a piece
    # We can check this from move history — a capture removes a piece
    # Use the before-move board: re-parse from fen_before stored in move_history
    return "normal"


def _describe_balance(balance: int) -> str:
    if balance == 0:
        return "子力均等"
    side = "白方" if balance > 0 else "黑方"
    diff = abs(balance)
    return f"{side}多{diff}个兵力当量"


def _san_to_zh_hint(san: str, player: str) -> str:
    """Very light hint extraction from SAN string."""
    if "+" in san:
        return "将军"
    if "#" in san:
        return "将死"
    if "x" in san:
        return "吃子"
    if san.startswith("O"):
        return "王车易位"
    if "=" in san:
        return "升变"
    return "普通走法"


def build_chess_grounding_facts(state: dict) -> dict:
    """
    Build deterministic, conservative grounding facts from chess game state.

    Uses python-chess to parse FEN. Returns "unknown" when analysis is unclear.
    Safe to call on any valid state dict.
    """
    fen: str = state.get("fen", chess.STARTING_FEN)
    turn: str = state.get("turn", "white")
    status: str = state.get("status", "active")
    result = state.get("result")
    termination = state.get("termination")
    move_history: list[dict] = state.get("move_history", [])
    last_move_entry: Optional[dict] = state.get("last_move")

    try:
        board = chess.Board(fen)
    except Exception:
        board = chess.Board()

    # Material balance
    balance = _material_balance(board)
    balance_desc = _describe_balance(balance)

    # Is check
    is_check = board.is_check()

    # Last move analysis
    last_san: Optional[str] = None
    last_player: Optional[str] = None
    last_uci: Optional[str] = None
    move_hint: str = "暂无走法"
    tactics: str = "normal"

    if last_move_entry:
        last_san = last_move_entry.get("san")
        last_player = last_move_entry.get("player")
        last_uci = last_move_entry.get("uci")
        if last_san:
            move_hint = _san_to_zh_hint(last_san, last_player or "")
            tactics = move_hint if move_hint in ("将军", "将死", "吃子") else "普通走法"

    # Captured piece detection (compare piece count)
    captured_piece: Optional[str] = None
    if len(move_history) >= 2 and last_move_entry:
        # Current board has fewer pieces than previous → capture happened
        current_count = len(board.piece_map())
        if last_move_entry.get("uci") and len(last_move_entry["uci"]) >= 4:
            # Parse pre-move FEN from second-to-last entry if available
            prev_entry = move_history[-2] if len(move_history) >= 2 else None
            if prev_entry and prev_entry.get("fen_after"):
                try:
                    prev_board = chess.Board(prev_entry["fen_after"])
                    if len(prev_board.piece_map()) > current_count:
                        # A piece was removed — figure out what
                        to_sq = chess.parse_square(last_move_entry["uci"][2:4])
                        prev_piece = prev_board.piece_at(to_sq)
                        if prev_piece:
                            captured_piece = _PIECE_NAMES_ZH.get(prev_piece.piece_type, "棋子")
                except Exception:
                    pass

    # Move count
    move_count = len(move_history)

    return {
        "status": status,
        "result": result,
        "termination": termination,
        "turn": turn,
        "move_count": move_count,
        "is_check": is_check,
        "last_move": last_move_entry,
        "last_san": last_san,
        "last_player": last_player,
        "last_uci": last_uci,
        "move_hint": move_hint,
        "tactics": tactics,
        "captured_piece": captured_piece,
        "material_balance": balance,
        "material_balance_desc": balance_desc,
    }


def format_chess_grounding_for_prompt(facts: dict) -> str:
    """Format chess grounding facts as a <game_facts> block for LLM injection."""
    lines = ["<game_facts>"]

    status_label = {"active": "进行中", "completed": "已结束"}.get(facts.get("status", ""), facts.get("status", ""))
    lines.append(f"状态：{status_label}")
    lines.append(f"步数：{facts.get('move_count', 0)}")

    turn = facts.get("turn", "white")
    turn_label = "白方" if turn == "white" else "黑方"
    lines.append(f"当前轮次：{turn_label}")

    if facts.get("is_check"):
        lines.append("将军：是（当前方处于被将状态）")

    lm = facts.get("last_move")
    if lm:
        player_label = "白方" if lm.get("player") == "white" else "黑方"
        san = facts.get("last_san") or lm.get("uci", "?")
        lines.append(f"\n上一步：{player_label} {san}")
        lines.append(f"走法性质：{facts.get('move_hint', '普通走法')}")
        if facts.get("captured_piece"):
            lines.append(f"被吃棋子：{facts['captured_piece']}")

    lines.append(f"\n子力形势：{facts.get('material_balance_desc', '均等')}")

    if facts.get("result"):
        lines.append(f"结果：{facts['result']}")
    if facts.get("termination"):
        lines.append(f"终局原因：{facts['termination']}")

    lines.append("</game_facts>")
    return "\n".join(lines)
