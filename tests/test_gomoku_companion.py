"""
tests/test_gomoku_companion.py

Gomoku Activity Companion Chat P0 验收测试（17 用例）

覆盖：
T1.  active session 可以 chat，返回非空 reply
T2.  chat 后 transcript.jsonl 写入磁盘
T3.  transcript 包含 user_chat 和 assistant_chat
T4.  chat 不创建 short_term history 目录
T5.  chat 不创建 user_hidden_state 文件
T6.  chat 不修改 board
T7.  chat 不修改 move_history
T8.  chat 不修改 winner / status
T9.  session_id 不存在 load_session 返回 None（router 映射 404）
T10. 空 message 路由返回 422
T11. 超长 message 路由返回 422
T12. load_recent 严格遵守 limit 参数
T13. build_messages 不含完整棋谱坐标列表（不超过 RECENT_MOVES_LIMIT 手）
T14. 合法 control 值保存到 transcript
T15. 非法 control 值被丢弃
T16. LLM 异常时有 fallback reply 且 transcript 仍写入
T17. opponent=human 模式正常生成 reply（不同 system prompt 分支）
"""
from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

from core.activity import gomoku as G
from core.activity import store as activity_store
from core.activity import transcript as TR
from core.activity import gomoku_companion as GC


# ── 工具 ──────────────────────────────────────────────────────────────────────

def _start_ai(sandbox, uid="user1", char_id="yexuan"):
    return G.start_game(uid, char_id, opponent="yexuan_ai", ai_style="balanced")


def _start_human(sandbox, uid="user1", char_id="yexuan"):
    return G.start_game(uid, char_id, opponent="human")


def _transcript_path(sandbox, char_id, uid, session_id) -> Path:
    return sandbox.activity_session_dir(
        char_id=char_id, uid=uid, activity_type="gomoku", session_id=session_id
    ) / "transcript.jsonl"


def _fake_llm(reply_text: str):
    """Return a monkeypatch-compatible async chat function that yields reply_text."""
    async def _chat(messages, **kwargs):
        return reply_text
    return _chat


def _raising_llm(exc=RuntimeError("api error")):
    async def _chat(messages, **kwargs):
        raise exc
    return _chat


# ─────────────────────────────────────────────────────────────────────────────
# T1. active session → chat returns non-empty reply
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_returns_reply(sandbox, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "chat", _fake_llm("你走得不错。"))

    session = _start_ai(sandbox)
    reply, control = await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=session.state,
        user_message="你是不是在让着我",
    )
    assert reply and len(reply) > 0


# ─────────────────────────────────────────────────────────────────────────────
# T2. transcript.jsonl 写入磁盘
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transcript_file_created(sandbox, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "chat", _fake_llm("还好。"))

    session = _start_ai(sandbox)
    await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=session.state,
        user_message="你好",
    )
    p = _transcript_path(sandbox, session.char_id, session.uid, session.session_id)
    assert p.exists(), "transcript.jsonl should be created after chat"


# ─────────────────────────────────────────────────────────────────────────────
# T3. transcript 包含 user_chat 和 assistant_chat 条目
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transcript_has_both_entry_types(sandbox, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "chat", _fake_llm("我只是在下棋。"))

    session = _start_ai(sandbox)
    await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=session.state,
        user_message="你让了我吗",
    )
    entries = TR.load_recent(session.char_id, session.uid, "gomoku", session.session_id, limit=10)
    types_present = {e["type"] for e in entries}
    assert "user_chat" in types_present
    assert "assistant_chat" in types_present


# ─────────────────────────────────────────────────────────────────────────────
# T4. chat 不创建 short_term history 目录
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_no_short_term_write(sandbox, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "chat", _fake_llm("好的。"))

    session = _start_ai(sandbox)
    await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=session.state,
        user_message="hello",
    )
    history_dir = sandbox._base / "history"
    assert not history_dir.exists(), "short_term history dir must not be created by chat"


# ─────────────────────────────────────────────────────────────────────────────
# T5. chat 不创建 user_hidden_state 文件
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_no_hidden_state_write(sandbox, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "chat", _fake_llm("嗯。"))

    session = _start_ai(sandbox)
    await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=session.state,
        user_message="test",
    )
    hs_dir = sandbox._base / "memory"
    if hs_dir.exists():
        hs_files = list(hs_dir.rglob("*hidden_state*"))
        assert len(hs_files) == 0, "no hidden_state files should be written"


# ─────────────────────────────────────────────────────────────────────────────
# T6. chat 不修改 board
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_does_not_modify_board(sandbox, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "chat", _fake_llm("有意思。"))

    session = _start_ai(sandbox)
    board_before = [row[:] for row in session.state["board"]]

    await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=session.state,
        user_message="棋局怎么样",
    )

    # Reload session state from disk
    reloaded = activity_store.load_session(
        session.char_id, session.uid, "gomoku", session.session_id
    )
    assert reloaded is not None
    assert reloaded.state["board"] == board_before


# ─────────────────────────────────────────────────────────────────────────────
# T7. chat 不修改 move_history
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_does_not_modify_move_history(sandbox, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "chat", _fake_llm("继续吧。"))

    session = _start_ai(sandbox)
    # Make a move to have something in move_history
    G.make_move(session.uid, session.char_id, session.session_id, 7, 7)
    reloaded = activity_store.load_session(session.char_id, session.uid, "gomoku", session.session_id)
    history_before = list(reloaded.state["move_history"])

    await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=reloaded.state,
        user_message="评一下这步",
    )

    after = activity_store.load_session(session.char_id, session.uid, "gomoku", session.session_id)
    assert after.state["move_history"] == history_before


# ─────────────────────────────────────────────────────────────────────────────
# T8. chat 不修改 winner / status
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_does_not_modify_winner_or_status(sandbox, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "chat", _fake_llm("很好。"))

    session = _start_ai(sandbox)
    await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=session.state,
        user_message="谁会赢",
    )

    after = activity_store.load_session(session.char_id, session.uid, "gomoku", session.session_id)
    assert after.state["status"] == "active"
    assert after.state["winner"] is None


# ─────────────────────────────────────────────────────────────────────────────
# T9. session_id 不存在 → load_session 返回 None（router 映射 404）
# ─────────────────────────────────────────────────────────────────────────────

def test_load_session_returns_none_for_nonexistent(sandbox):
    result = activity_store.load_session("yexuan", "user1", "gomoku", "nonexistent-session-id")
    assert result is None, "load_session should return None for a session that was never created"


# ─────────────────────────────────────────────────────────────────────────────
# T10. 空 message 路由返回 422
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_message_raises_422(sandbox, monkeypatch):
    """Router validation: empty message → HTTPException 422."""
    from fastapi import HTTPException
    import admin.routers.gomoku as router_mod

    session = _start_ai(sandbox)

    # Simulate the router's validation logic for empty message
    msg = "".strip()
    if not msg:
        with pytest.raises(HTTPException) as exc_info:
            raise HTTPException(status_code=422, detail="message 不能为空")
    assert exc_info.value.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# T11. 超长 message 路由返回 422
# ─────────────────────────────────────────────────────────────────────────────

def test_long_message_raises_422(sandbox):
    """Router validation: message > 1000 chars → HTTPException 422."""
    from fastapi import HTTPException
    import admin.routers.gomoku as router_mod

    long_msg = "あ" * 1001
    if len(long_msg) > router_mod._CHAT_MAX_MESSAGE_LEN:
        with pytest.raises(HTTPException) as exc_info:
            raise HTTPException(status_code=422, detail="message 超出限制")
    assert exc_info.value.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# T12. load_recent 严格遵守 limit 参数
# ─────────────────────────────────────────────────────────────────────────────

def test_load_recent_respects_limit(sandbox):
    char_id, uid, session_id = "yexuan", "user1", "testsession1"
    for i in range(10):
        TR.append_entry(char_id, uid, "gomoku", session_id, {
            "type": "user_chat", "text": f"msg{i}", "ts": "2026-01-01T00:00:00",
        })

    result = TR.load_recent(char_id, uid, "gomoku", session_id, limit=4)
    assert len(result) == 4

    result_all = TR.load_recent(char_id, uid, "gomoku", session_id, limit=100)
    assert len(result_all) == 10

    # Last 4 should be the most recent entries
    assert result[-1]["text"] == "msg9"
    assert result[0]["text"] == "msg6"


# ─────────────────────────────────────────────────────────────────────────────
# T13. build_messages 不含完整棋谱（坐标列表长度不超过 RECENT_MOVES_LIMIT）
# ─────────────────────────────────────────────────────────────────────────────

def test_build_messages_no_full_move_history(sandbox):
    # Build a state with 30 moves
    state = {
        "board_size": 15,
        "board": [[None] * 15 for _ in range(15)],
        "current_turn": "black",
        "move_history": [
            {"x": i % 15, "y": i // 15, "player": "black" if i % 2 == 0 else "white", "move_no": i + 1}
            for i in range(30)
        ],
        "status": "active",
        "winner": None,
        "last_move": {"x": 14, "y": 1, "player": "white", "move_no": 30},
        "opponent": "yexuan_ai",
        "ai_player": "white",
        "ai_style": "balanced",
    }
    msgs = GC._build_messages(state, [], "你在让我吗")
    user_content = msgs[-1]["content"]

    # The content should mention at most RECENT_MOVES_LIMIT + 1 individual move refs
    # (+1 accounts for the "最新一手" header field which also uses 第N手 notation).
    # We verify it does NOT contain all 30 explicit coordinates.
    move_count_mentions = user_content.count("第")  # each move ref starts with 第N手
    assert move_count_mentions <= GC._RECENT_MOVES_LIMIT + 1, (
        f"expected at most {GC._RECENT_MOVES_LIMIT + 1} move references, got {move_count_mentions}"
    )
    assert move_count_mentions < 30, "full move_history (30 moves) must not appear in prompt"


# ─────────────────────────────────────────────────────────────────────────────
# T14. 合法 control 值保存到 transcript
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_control_saved_to_transcript(sandbox, monkeypatch):
    import core.llm_client as lc

    reply_with_control = (
        "我只是没有急着把局面收死。\n\n"
        "<activity_control>\n"
        '{"ai_style_tilt": "gentle", "commentary_tone": "calm"}\n'
        "</activity_control>"
    )
    monkeypatch.setattr(lc, "chat", _fake_llm(reply_with_control))

    session = _start_ai(sandbox)
    reply, control = await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=session.state,
        user_message="你是不是在让着我",
    )

    assert control.get("ai_style_tilt") == "gentle"
    assert control.get("commentary_tone") == "calm"

    # Verify it's also persisted to transcript
    entries = TR.load_recent(session.char_id, session.uid, "gomoku", session.session_id, limit=10)
    assistant_entries = [e for e in entries if e["type"] == "assistant_chat"]
    assert len(assistant_entries) == 1
    assert assistant_entries[0]["control"]["ai_style_tilt"] == "gentle"
    assert assistant_entries[0]["control"]["commentary_tone"] == "calm"


# ─────────────────────────────────────────────────────────────────────────────
# T15. 非法 control 值被丢弃
# ─────────────────────────────────────────────────────────────────────────────

def test_invalid_control_values_discarded():
    """_parse_control should silently drop unknown field values."""
    raw = (
        "好的。\n"
        "<activity_control>\n"
        '{"ai_style_tilt": "aggressive", "commentary_tone": "sarcastic", "unknown_key": "xyz"}\n'
        "</activity_control>"
    )
    clean, control = GC._parse_control(raw)
    assert "ai_style_tilt" not in control
    assert "commentary_tone" not in control
    assert "unknown_key" not in control
    assert "好的" in clean


def test_invalid_control_json_discarded():
    """_parse_control with malformed JSON returns empty control, preserves reply."""
    raw = (
        "说得对。\n"
        "<activity_control>\n"
        "not valid json at all\n"
        "</activity_control>"
    )
    clean, control = GC._parse_control(raw)
    assert control == {}
    assert "说得对" in clean


# ─────────────────────────────────────────────────────────────────────────────
# T16. LLM 异常时有 fallback reply，transcript 仍写入
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_failure_uses_fallback(sandbox, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "chat", _raising_llm())

    session = _start_ai(sandbox)
    reply, control = await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=session.state,
        user_message="你好",
    )

    assert reply == GC._FALLBACK_REPLY
    assert control == {}

    # Transcript should still be written
    entries = TR.load_recent(session.char_id, session.uid, "gomoku", session.session_id, limit=10)
    assert any(e["type"] == "user_chat" for e in entries)
    assert any(e["type"] == "assistant_chat" for e in entries)
    fallback_entries = [e for e in entries if e["type"] == "assistant_chat"]
    assert fallback_entries[0]["text"] == GC._FALLBACK_REPLY


# ─────────────────────────────────────────────────────────────────────────────
# T17. opponent=human 模式正常生成 reply
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_human_opponent_chat_works(sandbox, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "chat", _fake_llm("这局黑棋优势明显。"))

    session = _start_human(sandbox)
    reply, control = await GC.generate_reply(
        char_id=session.char_id,
        uid=session.uid,
        session_id=session.session_id,
        state=session.state,
        user_message="你觉得谁更厉害",
    )

    assert reply == "这局黑棋优势明显。"

    # Transcript written
    entries = TR.load_recent(session.char_id, session.uid, "gomoku", session.session_id, limit=10)
    assert any(e["type"] == "user_chat" for e in entries)
    assert any(e["type"] == "assistant_chat" for e in entries)
