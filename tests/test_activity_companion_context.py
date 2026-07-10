"""
tests/test_activity_companion_context.py

core.activity.companion_context read-only helpers (Brief 43 §C).

T1. load_persona_brief returns a non-empty, <=300-char string for a real character.
T2. load_persona_brief fails open (returns "") for an unknown character id.
T3. load_main_chat_recall returns "" when there is no main-chat history.
T4. load_main_chat_recall formats main-chat history as 用户：/{char_name}：lines.
T5. load_main_chat_recall fails open (returns "") when short_term raises.
T6. chess/gomoku _build_messages include activity_persona / activity_main_chat_recall
    layers (with the read-only header) when the loaders return non-empty strings,
    and omit them when empty (backward compatible with existing callers).
"""
from __future__ import annotations

import chess
import pytest

from core.activity import chess_companion as CC
from core.activity import gomoku_companion as GC
from core.activity.companion_context import (
    MAIN_CHAT_RECALL_HEADER,
    load_main_chat_recall,
    load_persona_brief,
)


# ── load_persona_brief ──────────────────────────────────────────────────────────

def test_load_persona_brief_real_character(sandbox):
    brief = load_persona_brief("yexuan")
    assert isinstance(brief, str)
    assert brief
    assert len(brief) <= 300


def test_load_persona_brief_unknown_character_fails_open(sandbox):
    assert load_persona_brief("no_such_character_xyz") == ""


# ── load_main_chat_recall ────────────────────────────────────────────────────────

def test_load_main_chat_recall_empty_when_no_history(sandbox):
    assert load_main_chat_recall("recall_user_empty", "yexuan") == ""


def test_load_main_chat_recall_formats_lines(sandbox):
    from core.memory import short_term

    short_term.append("recall_user_1", "user", "你好呀", char_id="yexuan")
    short_term.append("recall_user_1", "assistant", "嗯，我在。", char_id="yexuan")

    recall = load_main_chat_recall("recall_user_1", "yexuan")
    assert "用户：你好呀" in recall
    assert "嗯，我在。" in recall


def test_load_main_chat_recall_fails_open_on_error(sandbox, monkeypatch):
    def _raise(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr("core.memory.short_term.get_history", _raise)
    assert load_main_chat_recall("recall_user_err", "yexuan") == ""


# ── _build_messages layer wiring ─────────────────────────────────────────────────

def _chess_state() -> dict:
    board = chess.Board()
    return {
        "fen": board.fen(),
        "turn": "white",
        "status": "active",
        "result": None,
        "termination": None,
        "move_history": [],
        "last_move": None,
    }


def _gomoku_state() -> dict:
    return {
        "board_size": 15,
        "board": [[None] * 15 for _ in range(15)],
        "current_turn": "black",
        "move_history": [],
        "status": "active",
        "winner": None,
        "last_move": None,
        "opponent": "human",
        "ai_player": "white",
        "ai_style": "balanced",
    }


def test_chess_build_messages_includes_persona_and_recall_layers():
    facts = CC.build_chess_grounding_facts(_chess_state())
    msgs = CC._build_messages(
        _chess_state(), [], "你好", facts,
        persona_brief="话少，喜欢观察棋局。",
        main_chat_recall="用户：早上好\n叶瑄：早。",
    )
    layers = {m["_layer"]: m for m in msgs}
    assert "activity_persona" in layers
    assert layers["activity_persona"]["content"] == "话少，喜欢观察棋局。"
    assert "activity_main_chat_recall" in layers
    assert MAIN_CHAT_RECALL_HEADER in layers["activity_main_chat_recall"]["content"]
    assert "早上好" in layers["activity_main_chat_recall"]["content"]


def test_chess_build_messages_omits_empty_layers():
    facts = CC.build_chess_grounding_facts(_chess_state())
    msgs = CC._build_messages(_chess_state(), [], "你好", facts)
    layers = {m["_layer"] for m in msgs}
    assert "activity_persona" not in layers
    assert "activity_main_chat_recall" not in layers


def test_gomoku_build_messages_includes_persona_and_recall_layers():
    msgs = GC._build_messages(
        _gomoku_state(), [], "你好",
        persona_brief="话少，喜欢观察棋局。",
        main_chat_recall="用户：早上好\n叶瑄：早。",
    )
    layers = {m["_layer"]: m for m in msgs}
    assert "activity_persona" in layers
    assert "activity_main_chat_recall" in layers
    assert MAIN_CHAT_RECALL_HEADER in layers["activity_main_chat_recall"]["content"]


def test_gomoku_build_messages_omits_empty_layers():
    msgs = GC._build_messages(_gomoku_state(), [], "你好")
    layers = {m["_layer"] for m in msgs}
    assert "activity_persona" not in layers
    assert "activity_main_chat_recall" not in layers


# ── End-to-end: generate_reply still doesn't write main memory ──────────────────

@pytest.mark.asyncio
async def test_chess_generate_reply_reads_but_does_not_write_short_term(sandbox, monkeypatch):
    async def _chat(messages, **kwargs):
        return "好的。"
    monkeypatch.setattr("core.llm_client.chat", _chat)

    from core.memory import short_term
    short_term.append("readonly_user", "user", "早上好", char_id="yexuan")

    await CC.generate_reply("yexuan", "readonly_user", "sessRO", _chess_state(), "你好")

    history_after = short_term.get_history("readonly_user", char_id="yexuan")
    assert len(history_after) == 1, "activity chat must not append to main short_term history"
