"""
tests/test_activity_companion_text.py

core.activity.companion_text.strip_action_descriptions 验收测试 (Brief 43 §A).

T1.  行内括号动作描写被删除
T2.  整行括号动作描写被删除
T3.  整行星号动作行被删除
T4.  整行 _feel_ / > env 行被删除
T5.  全动作描写清洗后为空 → 保底返回原文前 80 字，不返回空串
T6.  正常对话文本不受影响
T7-T9. 三个 companion 的 generate_reply 落盘 transcript 文本已清洗（mock 脏文本）
"""
from __future__ import annotations

import json
from pathlib import Path

import chess
import pytest

from core.activity import chess_companion as CC
from core.activity import gomoku_companion as GC
from core.activity import reading_companion as RC
from core.activity.companion_text import strip_action_descriptions


# ── Pure function tests ────────────────────────────────────────────────────────

def test_inline_bracket_removed():
    assert strip_action_descriptions("你好（笑了一下）今天下棋吗") == "你好今天下棋吗"
    assert strip_action_descriptions("hello (smiles) let's play") == "hello  let's play"


def test_whole_line_bracket_removed():
    text = "我们开始吧\n（他靠近棋盘）\n轮到你了"
    result = strip_action_descriptions(text)
    assert "靠近棋盘" not in result
    assert "我们开始吧" in result
    assert "轮到你了" in result


def test_whole_line_asterisk_removed():
    text = "好的。\n*微微一笑*\n你来吧。"
    result = strip_action_descriptions(text)
    assert "微微一笑" not in result
    assert "好的。" in result
    assert "你来吧。" in result


def test_whole_line_feel_and_env_removed():
    text = "开始下棋\n_有点紧张_\n> 窗外下着雨\n继续"
    result = strip_action_descriptions(text)
    assert "有点紧张" not in result
    assert "窗外下着雨" not in result
    assert "开始下棋" in result
    assert "继续" in result


def test_all_action_fallback_not_empty():
    text = "（他沉默地看着棋盘，久久没有说话）"
    result = strip_action_descriptions(text)
    assert result != ""
    assert result == text[:80]


def test_normal_text_unaffected():
    text = "这一步很稳，你后面小心右翼。"
    assert strip_action_descriptions(text) == text


# ── Companion wiring tests ──────────────────────────────────────────────────────

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


def _assistant_text(sandbox, char_id, uid, activity_type, session_id) -> str:
    p = sandbox.activity_session_dir(
        char_id=char_id, uid=uid, activity_type=activity_type, session_id=session_id
    ) / "transcript.jsonl"
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assistant = next(e for e in lines if e["type"] == "assistant_chat")
    return assistant["text"]


@pytest.mark.asyncio
async def test_chess_companion_transcript_is_cleaned(sandbox, monkeypatch):
    dirty = "这步稳（他敲了敲桌子）继续吧"
    monkeypatch.setattr("core.llm_client.chat", _fake_llm(dirty))
    await CC.generate_reply("yexuan", "user1", "sess_clean_chess", _chess_state(), "你好")
    text = _assistant_text(sandbox, "yexuan", "user1", "chess", "sess_clean_chess")
    assert "（" not in text and "）" not in text


@pytest.mark.asyncio
async def test_gomoku_companion_transcript_is_cleaned(sandbox, monkeypatch):
    dirty = "这手不错（他向前倾身看棋盘）该你了"
    monkeypatch.setattr("core.llm_client.chat", _fake_llm(dirty))
    await GC.generate_reply("yexuan", "user1", "sess_clean_gomoku", _gomoku_state(), "你好")
    text = _assistant_text(sandbox, "yexuan", "user1", "gomoku", "sess_clean_gomoku")
    assert "（" not in text and "）" not in text


@pytest.mark.asyncio
async def test_reading_companion_transcript_is_cleaned(sandbox, monkeypatch):
    dirty = "这段挺有意思（他翻了翻书页）你觉得呢"
    monkeypatch.setattr("core.llm_client.chat", _fake_llm(dirty))
    await RC.generate_reply(
        "yexuan", "user1", "sess_clean_reading", 1, 10, "book.pdf", "示例正文", "你好"
    )
    text = _assistant_text(sandbox, "yexuan", "user1", "reading", "sess_clean_reading")
    assert "（" not in text and "）" not in text
