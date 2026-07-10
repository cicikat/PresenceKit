"""
Chess Activity 棋局逻辑 (P0 + P1-AI)

规则、合法性、胜负全部由 python-chess 负责。
不接 Stockfish / 外部 API / LLM / Dream / trigger / scheduler / perceive_event。
不写 short_term / user_hidden_state。

P1 新增：
- make_initial_state 接受 opponent / ai_style 参数
- apply_move 用户落子后若 opponent=character_ai 且轮到 AI，设 pending_ai_turn=True
- apply_ai_move() 执行待处理 AI 落子
"""
from __future__ import annotations

from typing import Optional

import chess

STARTING_FEN: str = chess.STARTING_FEN

_VALID_OPPONENTS = frozenset({"human", "character_ai"})
_VALID_STYLES = frozenset({"balanced", "gentle", "serious", "teaching"})

# Brief 25 §3 P2: "yexuan_ai" -> "character_ai" rename, back-compat normalization.
_LEGACY_OPPONENT_ALIASES: dict[str, str] = {"yexuan_ai": "character_ai"}


def _normalize_opponent(value: str) -> str:
    """Map legacy opponent values to their current canonical name; unknown values pass through
    unchanged so _VALID_OPPONENTS validation can reject them with a clear error."""
    return _LEGACY_OPPONENT_ALIASES.get(value, value)


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


def make_initial_state(
    fen: Optional[str] = None,
    opponent: str = "human",
    ai_style: str = "balanced",
) -> dict:
    """Return a fresh chess state dict.

    Uses the standard starting position when fen is None.
    Raises ValueError for an invalid FEN string or unknown opponent/style.
    """
    opponent = _normalize_opponent(opponent)
    if opponent not in _VALID_OPPONENTS:
        raise ValueError(f"opponent 必须是 {sorted(_VALID_OPPONENTS)}，收到 {opponent!r}")
    if ai_style not in _VALID_STYLES:
        raise ValueError(f"ai_style 必须是 {sorted(_VALID_STYLES)}，收到 {ai_style!r}")

    if fen is None:
        board = chess.Board()
    else:
        try:
            board = chess.Board(fen)
        except Exception as e:
            raise ValueError(f"无效的 FEN: {e}") from e

    # User always plays white (first move); AI plays black when enabled.
    ai_player = "black" if opponent == "character_ai" else None

    return {
        "fen": board.fen(),
        "turn": _turn_str(board),
        "status": "active",
        "result": None,
        "termination": None,
        "move_history": [],
        "last_move": None,
        "opponent": opponent,
        "ai_player": ai_player,
        "ai_style": ai_style,
        "pending_ai_turn": False,
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

    opponent = _normalize_opponent(state.get("opponent", "human"))
    ai_player = state.get("ai_player")
    ai_style = state.get("ai_style", "balanced")

    # If game still active and AI opponent, check if it's now AI's turn.
    pending_ai_turn = (
        game_status == "active"
        and opponent == "character_ai"
        and ai_player is not None
        and _turn_str(board) == ai_player
    )

    return {
        "fen": new_fen,
        "turn": _turn_str(board),
        "status": game_status,
        "result": result,
        "termination": termination,
        "move_history": history,
        "last_move": entry,
        "opponent": opponent,
        "ai_player": ai_player,
        "ai_style": ai_style,
        "pending_ai_turn": pending_ai_turn,
    }


def apply_ai_move(state: dict, style_tilt: Optional[str] = None) -> dict:
    """Execute the pending AI move and return the updated state.

    style_tilt (Brief 43 §E, mirrors gomoku): optional style read from companion
    chat control (via chess_companion.get_recent_ai_style_tilt), applied for this
    single move only. Invalid/None tilt falls back to the session's base ai_style;
    the session's ai_style itself is never overwritten.

    Raises ValueError if there is no pending AI turn or the game is over.
    """
    opponent = _normalize_opponent(state.get("opponent", "character_ai"))
    if state.get("status") != "active":
        raise ValueError("棋局已结束，无法 AI 落子")
    if not state.get("pending_ai_turn"):
        raise ValueError("当前没有待处理的 AI 落子（pending_ai_turn=False）")

    ai_player_str = state.get("ai_player")
    if ai_player_str is None:
        raise ValueError("非 AI 对手模式")

    ai_color = chess.BLACK if ai_player_str == "black" else chess.WHITE
    base_style = state.get("ai_style", "balanced")

    effective_style = base_style
    style_source = "base_style"
    if style_tilt and style_tilt in _VALID_STYLES:
        effective_style = style_tilt
        style_source = "activity_chat_control"

    board = chess.Board(state["fen"])
    if board.turn != ai_color:
        raise ValueError("当前不是 AI 的回合")

    from core.activity.chess_ai import choose_chess_ai_move
    move = choose_chess_ai_move(board, ai_color, effective_style)
    if move is None:
        raise ValueError("AI 无合法走法")

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
        "style": effective_style,
        "base_style": base_style,
        "style_source": style_source,
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
        "opponent": opponent,
        "ai_player": ai_player_str,
        "ai_style": base_style,
        "pending_ai_turn": False,
    }


def legal_moves_uci(state: dict) -> list[str]:
    """Return all legal moves in UCI notation for the current position."""
    board = chess.Board(state["fen"])
    return sorted(m.uci() for m in board.legal_moves)
