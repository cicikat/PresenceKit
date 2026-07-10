"""
tests/test_activity_prompt_capture.py

Activity companion chat prompt-layer observation wiring (Brief 43 §B).

T1-T3. generate_reply for chess/gomoku/reading writes a snapshot into
       prompt_capture with origin.origin=="activity", origin.activity_type
       correct, and llm_output filled in after the call.
T4.    messages passed to capture() carry _layer fields (not "unknown").
"""
from __future__ import annotations

import chess
import pytest

from core.activity import chess_companion as CC
from core.activity import gomoku_companion as GC
from core.activity import reading_companion as RC
from core.observe import prompt_capture


def _fake_llm(reply_text: str):
    async def _chat(messages, **kwargs):
        return reply_text
    return _chat


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


@pytest.mark.asyncio
async def test_chess_chat_captures_snapshot(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("好的，轮到你了。"))
    await CC.generate_reply("yexuan", "capture_user_chess", "sess1", _chess_state(), "你好")
    snaps = prompt_capture.get_snapshots("capture_user_chess")
    assert snaps
    snap = snaps[-1]
    assert snap["origin"]["origin"] == "activity"
    assert snap["origin"]["activity_type"] == "chess"
    assert snap["origin"]["kind"] == "chat"
    assert snap["llm_output"] == "好的，轮到你了。"


@pytest.mark.asyncio
async def test_gomoku_chat_captures_snapshot(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("我看看局面。"))
    await GC.generate_reply("yexuan", "capture_user_gomoku", "sess1", _gomoku_state(), "你好")
    snaps = prompt_capture.get_snapshots("capture_user_gomoku")
    assert snaps
    snap = snaps[-1]
    assert snap["origin"]["origin"] == "activity"
    assert snap["origin"]["activity_type"] == "gomoku"
    assert snap["llm_output"] == "我看看局面。"


@pytest.mark.asyncio
async def test_reading_chat_captures_snapshot(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("这段挺有意思。"))
    await RC.generate_reply(
        "yexuan", "capture_user_reading", "sess1", 1, 10, "book.pdf", "示例正文", "你好"
    )
    snaps = prompt_capture.get_snapshots("capture_user_reading")
    assert snaps
    snap = snaps[-1]
    assert snap["origin"]["origin"] == "activity"
    assert snap["origin"]["activity_type"] == "reading"
    assert snap["llm_output"] == "这段挺有意思。"


@pytest.mark.asyncio
async def test_captured_layers_are_named(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("好的。"))
    await CC.generate_reply("yexuan", "capture_user_layers", "sess1", _chess_state(), "你好")
    snap = prompt_capture.get_snapshots("capture_user_layers")[-1]
    layers = {l["layer"] for l in snap["layers"]}
    assert "unknown" not in layers
    assert "activity_system" in layers
    assert "activity_context" in layers
