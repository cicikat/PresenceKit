"""
tests/test_activity_comment.py

Proactive move comment (Brief 43 §D) — maybe_generate_move_comment for
chess/gomoku companion.

T1.  chess: key moment (is_check) always comments, regardless of random()/cooldown
T2.  chess: key moment (captured_piece) always comments
T3.  chess: key moment (status completed) always comments
T4.  chess: non-key moment, random() fails probability -> no comment, no transcript write
T5.  chess: non-key moment, probability passes but cooldown not satisfied -> no comment
T6.  chess: non-key moment, probability + cooldown both satisfied -> comments
T7.  comment=null -> no transcript write, no LLM call
T8.  written entry has proactive=True and at_move set
T9.  gomoku: key moment (winner set) always comments
T10. gomoku: key moment (created_chain>=3) always comments
T11. gomoku: non-key moment respects probability/cooldown same as chess
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import chess
import pytest

from core.activity import chess_companion as CC
from core.activity import gomoku_companion as GC
from core.activity import transcript as TR


def _fake_llm(reply_text: str):
    calls = {"n": 0}

    async def _chat(messages, **kwargs):
        calls["n"] += 1
        return reply_text
    _chat.calls = calls
    return _chat


def _chess_state(**overrides) -> dict:
    board = chess.Board()
    state = {
        "fen": board.fen(),
        "turn": "white",
        "status": "active",
        "result": None,
        "termination": None,
        "move_history": [],
        "last_move": None,
    }
    state.update(overrides)
    return state


def _gomoku_state(**overrides) -> dict:
    state = {
        "board_size": 15,
        "board": [[None] * 15 for _ in range(15)],
        "current_turn": "black",
        "move_history": [],
        "status": "active",
        "winner": None,
        "last_move": None,
        "opponent": "character_ai",
        "ai_player": "white",
        "ai_style": "balanced",
    }
    state.update(overrides)
    return state


# ── Chess key moments ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chess_key_moment_is_check_always_comments(sandbox, monkeypatch):
    llm = _fake_llm("将军了，小心。")
    monkeypatch.setattr("core.llm_client.chat", llm)
    monkeypatch.setattr("random.random", lambda: 0.99)  # would fail probability if checked

    # Fool's-mate-ish position where black is in check.
    board = chess.Board()
    board.push_san("f3")
    board.push_san("e5")
    board.push_san("g4")
    # Qh4# gives check (checkmate actually, but is_check is what we assert on)
    board.push_san("Qh4#")
    state = _chess_state(fen=board.fen(), status="active", last_move={
        "move_no": 2, "uci": "d8h4", "san": "Qh4#", "player": "black", "fen_after": board.fen(),
    })

    comment, grounding = await CC.maybe_generate_move_comment(TEST_CHAR_ID, "user1", "sessKM1", state)
    assert comment is not None
    assert llm.calls["n"] == 1


@pytest.mark.asyncio
async def test_chess_key_moment_captured_piece_always_comments(sandbox, monkeypatch):
    llm = _fake_llm("吃了一个子。")
    monkeypatch.setattr("core.llm_client.chat", llm)
    monkeypatch.setattr("random.random", lambda: 0.99)

    board = chess.Board()
    board.push_san("e4")
    board.push_san("d5")
    board.push_san("exd5")  # capture
    history = [
        {"move_no": 1, "uci": "e2e4", "san": "e4", "player": "white", "fen_after": "..."},
        {"move_no": 1, "uci": "d7d5", "san": "d5", "player": "black", "fen_after":
            chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2").fen()},
        {"move_no": 2, "uci": "e4d5", "san": "exd5", "player": "white", "fen_after": board.fen()},
    ]
    state = _chess_state(fen=board.fen(), move_history=history, last_move=history[-1])

    comment, grounding = await CC.maybe_generate_move_comment(TEST_CHAR_ID, "user1", "sessKM2", state)
    assert comment is not None
    assert grounding["captured_piece"]
    assert llm.calls["n"] == 1


@pytest.mark.asyncio
async def test_chess_key_moment_completed_always_comments(sandbox, monkeypatch):
    llm = _fake_llm("下完了。")
    monkeypatch.setattr("core.llm_client.chat", llm)
    monkeypatch.setattr("random.random", lambda: 0.99)

    state = _chess_state(status="completed", result="1-0", termination="checkmate")
    comment, _ = await CC.maybe_generate_move_comment(TEST_CHAR_ID, "user1", "sessKM3", state)
    assert comment is not None
    assert llm.calls["n"] == 1


# ── Chess non-key moment: probability + cooldown ─────────────────────────────────

@pytest.mark.asyncio
async def test_chess_non_key_probability_fails_no_comment(sandbox, monkeypatch):
    llm = _fake_llm("普通的一手。")
    monkeypatch.setattr("core.llm_client.chat", llm)
    monkeypatch.setattr("random.random", lambda: 0.9)  # >= 0.2 -> fails probability

    state = _chess_state(move_history=[
        {"move_no": 1, "uci": "e2e4", "san": "e4", "player": "white", "fen_after": "x"},
    ])
    comment, grounding = await CC.maybe_generate_move_comment(TEST_CHAR_ID, "user1", "sessNK1", state)
    assert comment is None
    assert llm.calls["n"] == 0
    p = TR.path_for_session(TEST_CHAR_ID, "user1", "chess", "sessNK1")
    assert not p.exists()


@pytest.mark.asyncio
async def test_chess_non_key_probability_passes_cooldown_blocks(sandbox, monkeypatch):
    llm = _fake_llm("普通的一手。")
    monkeypatch.setattr("core.llm_client.chat", llm)
    monkeypatch.setattr("random.random", lambda: 0.05)  # < 0.2 -> passes probability

    # Simulate a prior proactive comment 1 move ago (cooldown needs >=2).
    TR.append_entry(TEST_CHAR_ID, "user1", "chess", "sessNK2", {
        "type": "assistant_chat", "text": "刚评论过。", "ts": "2026-07-11T00:00:00+00:00",
        "proactive": True, "at_move": 0,
    })
    state = _chess_state(move_history=[
        {"move_no": 1, "uci": "e2e4", "san": "e4", "player": "white", "fen_after": "x"},
    ])
    comment, _ = await CC.maybe_generate_move_comment(TEST_CHAR_ID, "user1", "sessNK2", state)
    assert comment is None
    assert llm.calls["n"] == 0


@pytest.mark.asyncio
async def test_chess_non_key_probability_and_cooldown_satisfied_comments(sandbox, monkeypatch):
    llm = _fake_llm("普通的一手，走得挺稳。")
    monkeypatch.setattr("core.llm_client.chat", llm)
    monkeypatch.setattr("random.random", lambda: 0.05)

    TR.append_entry(TEST_CHAR_ID, "user1", "chess", "sessNK3", {
        "type": "assistant_chat", "text": "很久之前评论过。", "ts": "2026-07-11T00:00:00+00:00",
        "proactive": True, "at_move": 0,
    })
    state = _chess_state(move_history=[
        {"move_no": 1, "uci": "e2e4", "san": "e4", "player": "white", "fen_after": "x"},
        {"move_no": 1, "uci": "e7e5", "san": "e5", "player": "black", "fen_after": "y"},
    ])  # move_count=2, gap from at_move=0 is 2 -> cooldown satisfied
    comment, _ = await CC.maybe_generate_move_comment(TEST_CHAR_ID, "user1", "sessNK3", state)
    assert comment is not None
    assert llm.calls["n"] == 1


# ── Written transcript entry shape ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chess_comment_writes_proactive_entry_only(sandbox, monkeypatch):
    llm = _fake_llm("将军，注意。")
    monkeypatch.setattr("core.llm_client.chat", llm)

    state = _chess_state(move_history=[
        {"move_no": 1, "uci": "e2e4", "san": "e4", "player": "white", "fen_after": "x"},
    ])
    # Force a check so it's a guaranteed key moment (patch grounding facts).
    monkeypatch.setattr(
        "core.activity.chess_companion.build_chess_grounding_facts",
        lambda s: {
            "status": "active", "result": None, "termination": None, "turn": "black",
            "move_count": 1, "is_check": True, "last_move": s.get("last_move"),
            "last_san": "e4", "last_player": "white", "last_uci": "e2e4",
            "move_hint": "普通走法", "tactics": "normal", "captured_piece": None,
            "material_balance": 0, "material_balance_desc": "子力均等",
        },
    )

    comment, _ = await CC.maybe_generate_move_comment(TEST_CHAR_ID, "user1", "sessProactive", state)
    assert comment is not None

    import json
    p = TR.path_for_session(TEST_CHAR_ID, "user1", "chess", "sessProactive")
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1, "only assistant_chat should be written, no user_chat"
    entry = lines[0]
    assert entry["type"] == "assistant_chat"
    assert entry["proactive"] is True
    assert entry["at_move"] == 1


# ── Gomoku key moments ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gomoku_key_moment_winner_always_comments(sandbox, monkeypatch):
    llm = _fake_llm("赢了这局。")
    monkeypatch.setattr("core.llm_client.chat", llm)
    monkeypatch.setattr("random.random", lambda: 0.99)

    state = _gomoku_state(status="completed", winner="black", move_history=[
        {"x": 7, "y": 7, "player": "black", "move_no": 1},
    ])
    comment, _ = await GC.maybe_generate_move_comment(TEST_CHAR_ID, "user1", "sessGKM1", state)
    assert comment is not None
    assert llm.calls["n"] == 1


@pytest.mark.asyncio
async def test_gomoku_key_moment_created_chain_always_comments(sandbox, monkeypatch):
    llm = _fake_llm("形成了三连。")
    monkeypatch.setattr("core.llm_client.chat", llm)
    monkeypatch.setattr("random.random", lambda: 0.99)

    board = [[None] * 15 for _ in range(15)]
    board[7][7] = "black"
    board[7][8] = "black"
    board[7][9] = "black"
    state = _gomoku_state(board=board, move_history=[
        {"x": 7, "y": 7, "player": "black", "move_no": 1},
        {"x": 8, "y": 7, "player": "black", "move_no": 2},
        {"x": 9, "y": 7, "player": "black", "move_no": 3},
    ], last_move={"x": 9, "y": 7, "player": "black", "move_no": 3})

    comment, grounding = await GC.maybe_generate_move_comment(TEST_CHAR_ID, "user1", "sessGKM2", state)
    assert comment is not None
    assert llm.calls["n"] == 1


@pytest.mark.asyncio
async def test_gomoku_non_key_moment_respects_probability(sandbox, monkeypatch):
    llm = _fake_llm("普通落子。")
    monkeypatch.setattr("core.llm_client.chat", llm)
    monkeypatch.setattr("random.random", lambda: 0.9)

    board = [[None] * 15 for _ in range(15)]
    board[0][0] = "black"
    state = _gomoku_state(board=board, move_history=[
        {"x": 0, "y": 0, "player": "black", "move_no": 1},
    ], last_move={"x": 0, "y": 0, "player": "black", "move_no": 1})

    comment, _ = await GC.maybe_generate_move_comment(TEST_CHAR_ID, "user1", "sessGNK1", state)
    assert comment is None
    assert llm.calls["n"] == 0
