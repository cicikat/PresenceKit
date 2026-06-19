"""
tests/test_chess_companion.py

Chess Companion Chat 验收测试

T1.  generate_reply 返回 3-tuple (reply, control, grounding)
T2.  chat 后 transcript.jsonl 写入磁盘
T3.  transcript 包含 user_chat 和 assistant_chat
T4.  chat 不创建 short_term history 目录
T5.  chat 不创建 user_hidden_state 文件
T6.  LLM 异常时有 fallback reply 且 transcript 仍写入
T7.  grounding 包含 turn / move_hint / material_balance_desc 字段
T8.  合法 control 值保存到 transcript
T9.  非法 control 值被丢弃
T10. _build_messages 包含 <game_facts> 块
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import chess

from core.activity import chess_companion as CC
from core.activity import transcript as TR


def _initial_state() -> dict:
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


def _transcript_path(sandbox, char_id, uid, session_id) -> Path:
    return sandbox.activity_session_dir(
        char_id=char_id, uid=uid, activity_type="chess", session_id=session_id
    ) / "transcript.jsonl"


def _fake_llm(reply_text: str):
    async def _chat(messages, **kwargs):
        return reply_text
    return _chat


def _raising_llm(exc=RuntimeError("api error")):
    async def _chat(messages, **kwargs):
        raise exc
    return _chat


# T1
@pytest.mark.asyncio
async def test_generate_reply_returns_3_tuple(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("好的，轮到你了。"))
    result = await CC.generate_reply("yexuan", "user1", "sess1", _initial_state(), "你好")
    assert isinstance(result, tuple)
    assert len(result) == 3
    reply, control, grounding = result
    assert isinstance(reply, str) and reply
    assert isinstance(control, dict)
    assert isinstance(grounding, dict)


# T2
@pytest.mark.asyncio
async def test_transcript_written_to_disk(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("嗯，等你。"))
    await CC.generate_reply("yexuan", "user1", "sessA", _initial_state(), "你好")
    p = _transcript_path(sandbox, "yexuan", "user1", "sessA")
    assert p.exists()


# T3
@pytest.mark.asyncio
async def test_transcript_has_both_types(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("我看着局面。"))
    await CC.generate_reply("yexuan", "user1", "sessB", _initial_state(), "帮我看看")
    p = _transcript_path(sandbox, "yexuan", "user1", "sessB")
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    types = {e["type"] for e in lines}
    assert "user_chat" in types
    assert "assistant_chat" in types


# T4
@pytest.mark.asyncio
async def test_chat_does_not_create_short_term(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("好的。"))
    await CC.generate_reply("yexuan", "user1", "sessC", _initial_state(), "test")
    short_term_dir = sandbox._base / "history"
    assert not short_term_dir.exists()


# T5
@pytest.mark.asyncio
async def test_chat_does_not_create_user_hidden_state(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("好的。"))
    await CC.generate_reply("yexuan", "user1", "sessD", _initial_state(), "test")
    hidden_state_dir = sandbox._base / "user_hidden_state"
    assert not hidden_state_dir.exists()


# T6
@pytest.mark.asyncio
async def test_fallback_on_llm_error(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _raising_llm())
    reply, control, grounding = await CC.generate_reply("yexuan", "user1", "sessE", _initial_state(), "test")
    assert reply == CC._FALLBACK_REPLY
    p = _transcript_path(sandbox, "yexuan", "user1", "sessE")
    assert p.exists()


# T7
@pytest.mark.asyncio
async def test_grounding_has_expected_fields(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("好局面。"))
    _, _, grounding = await CC.generate_reply("yexuan", "user1", "sessF", _initial_state(), "评价一下")
    assert "turn" in grounding
    assert "move_hint" in grounding
    assert "material_balance_desc" in grounding


# T8
@pytest.mark.asyncio
async def test_valid_control_saved_to_transcript(sandbox, monkeypatch):
    reply_with_control = '好的。\n\n<activity_control>\n{"commentary_tone":"calm"}\n</activity_control>'
    monkeypatch.setattr("core.llm_client.chat", _fake_llm(reply_with_control))
    await CC.generate_reply("yexuan", "user1", "sessG", _initial_state(), "test")
    p = _transcript_path(sandbox, "yexuan", "user1", "sessG")
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assistant = next(e for e in lines if e["type"] == "assistant_chat")
    assert "control" in assistant
    assert assistant["control"]["commentary_tone"] == "calm"


# T9
@pytest.mark.asyncio
async def test_invalid_control_discarded(sandbox, monkeypatch):
    reply_bad = '好的。\n\n<activity_control>\n{"commentary_tone":"invalid_value"}\n</activity_control>'
    monkeypatch.setattr("core.llm_client.chat", _fake_llm(reply_bad))
    await CC.generate_reply("yexuan", "user1", "sessH", _initial_state(), "test")
    p = _transcript_path(sandbox, "yexuan", "user1", "sessH")
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assistant = next(e for e in lines if e["type"] == "assistant_chat")
    assert "control" not in assistant or "commentary_tone" not in assistant.get("control", {})


# T10
def test_build_messages_contains_game_facts():
    from core.activity.chess_companion import _build_messages
    from core.activity.chess_grounding import build_chess_grounding_facts
    state = _initial_state()
    facts = build_chess_grounding_facts(state)
    msgs = _build_messages(state, [], "你好", facts)
    assert any("<game_facts>" in m["content"] for m in msgs)
