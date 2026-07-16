"""tests/test_activity_pseudo_stream.py — Brief 84 §2/§3: dream + coplay/活动接线.

dream_chat / chess_chat / chess_comment / gomoku_chat / gomoku_comment / reading_chat
are all pure-HTTP-response endpoints with zero pre-existing WS push. Brief 84 adds an
optional pseudo-stream typewriter replay (via core.activity.pseudo_stream for the three
activity routers, inline for dream) plus a nullable `msg_id` field on the HTTP response
so a future client can dedup the same way owner chat's real stream path already does.

Covers:
- core.activity.pseudo_stream.push_companion_reply: None on falsy text, forwards to
  ui_push.pseudo_stream_push, fail-open on exception.
- dream_chat / chess_chat / chess_comment / gomoku_chat / gomoku_comment / reading_chat:
  reply/comment text triggers the push with a msg_id that also lands in the HTTP
  response; empty reply/comment (e.g. comment=None on a non-key move) never pushes and
  msg_id stays None.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ── core.activity.pseudo_stream.push_companion_reply ────────────────────────

@pytest.mark.asyncio
async def test_push_companion_reply_returns_none_for_empty_text(monkeypatch):
    from core.activity.pseudo_stream import push_companion_reply

    called = []
    monkeypatch.setattr(
        "channels.ui_push.pseudo_stream_push",
        lambda *a, **kw: called.append((a, kw)),
    )

    assert await push_companion_reply(None, char_id="yexuan") is None
    assert await push_companion_reply("", char_id="yexuan") is None
    assert called == []


@pytest.mark.asyncio
async def test_push_companion_reply_forwards_to_ui_push(monkeypatch):
    from core.activity.pseudo_stream import push_companion_reply

    calls = []

    async def fake_pseudo_stream_push(text, *, msg_id, char_id="", **kw):
        calls.append((text, msg_id, char_id))

    monkeypatch.setattr("channels.ui_push.pseudo_stream_push", fake_pseudo_stream_push)

    msg_id = await push_companion_reply("这步棋走得不错。", char_id="yexuan")

    assert msg_id is not None
    assert calls == [("这步棋走得不错。", msg_id, "yexuan")]


@pytest.mark.asyncio
async def test_push_companion_reply_fail_open_on_exception(monkeypatch):
    from core.activity.pseudo_stream import push_companion_reply

    async def boom(*a, **kw):
        raise RuntimeError("simulated ui_push failure")

    monkeypatch.setattr("channels.ui_push.pseudo_stream_push", boom)

    # Must not raise, and a msg_id is still returned for the caller to use.
    msg_id = await push_companion_reply("文本", char_id="yexuan")
    assert msg_id is not None


# ── dream_chat ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dream_chat_wires_pseudo_stream_and_msg_id(sandbox, monkeypatch):
    from admin.routers import dream as dream_router

    async def fake_dream_turn(uid, message):
        return {
            "reply": "梦里的风很轻。",
            "exit_accepted": False,
            "force_exited": False,
            "segments": [],
            "segmented_content": "",
        }

    monkeypatch.setattr("core.dream.dream_pipeline.dream_turn", fake_dream_turn)

    class FakePipeline:
        _active_character_id = "yexuan"

    monkeypatch.setattr("core.pipeline_registry.get", lambda: FakePipeline())

    calls = []

    async def fake_pseudo_stream_push(text, *, msg_id, char_id="", round_id="", profile="default"):
        calls.append((text, msg_id, char_id, profile))

    monkeypatch.setattr("channels.ui_push.pseudo_stream_push", fake_pseudo_stream_push)

    with patch("admin.routers.dream._owner_uid", return_value="owner"):
        result = await dream_router.dream_chat({"message": "你好"}, _auth=None)

    assert result["reply"] == "梦里的风很轻。"
    assert result["msg_id"]
    assert calls == [("梦里的风很轻。", result["msg_id"], "yexuan", "dream")]


@pytest.mark.asyncio
async def test_dream_chat_no_msg_id_when_reply_empty(sandbox, monkeypatch):
    from admin.routers import dream as dream_router

    async def fake_dream_turn(uid, message):
        return {"reply": "", "exit_accepted": False, "force_exited": False}

    monkeypatch.setattr("core.dream.dream_pipeline.dream_turn", fake_dream_turn)

    calls = []
    monkeypatch.setattr(
        "channels.ui_push.pseudo_stream_push",
        lambda *a, **kw: calls.append((a, kw)),
    )

    with patch("admin.routers.dream._owner_uid", return_value="owner"):
        result = await dream_router.dream_chat({"message": "你好"}, _auth=None)

    assert "msg_id" not in result
    assert calls == []


# ── chess_chat / chess_comment ───────────────────────────────────────────────

def _fake_chess_session(session_id="s1", uid="user1", char_id="yexuan"):
    from core.activity.session import ActivitySession

    return ActivitySession(
        session_id=session_id, uid=uid, char_id=char_id, activity_type="chess",
        status="active", state={}, created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_chess_chat_wires_pseudo_stream_and_msg_id(sandbox, monkeypatch):
    from admin.routers import chess as chess_router

    session = _fake_chess_session()
    monkeypatch.setattr(chess_router, "_active_char_id", lambda: "yexuan")
    monkeypatch.setattr(
        chess_router.activity_store, "load_session", lambda *a, **kw: session
    )

    async def fake_generate_reply(**kwargs):
        return "这步棋走得不错。", {"c": 1}, {"g": 1}

    monkeypatch.setattr(chess_router.chess_companion, "generate_reply", fake_generate_reply)

    calls = []

    async def fake_pseudo_stream_push(text, *, msg_id, char_id="", **kw):
        calls.append((text, msg_id, char_id))

    monkeypatch.setattr("channels.ui_push.pseudo_stream_push", fake_pseudo_stream_push)

    body = chess_router.ChatRequest(session_id="s1", message="你好", uid="user1")
    result = await chess_router.chess_chat(body, auth=None)

    assert result["reply"] == "这步棋走得不错。"
    assert result["msg_id"]
    assert calls == [("这步棋走得不错。", result["msg_id"], "yexuan")]


@pytest.mark.asyncio
async def test_chess_comment_no_push_when_comment_is_none(sandbox, monkeypatch):
    """P0-2 non-key-moment 分支：comment=None 时不应该有任何伪流式推送。"""
    from admin.routers import chess as chess_router

    session = _fake_chess_session()
    monkeypatch.setattr(chess_router, "_active_char_id", lambda: "yexuan")
    monkeypatch.setattr(
        chess_router.activity_store, "load_session", lambda *a, **kw: session
    )

    async def fake_maybe_comment(**kwargs):
        return None, {"g": 1}

    monkeypatch.setattr(
        chess_router.chess_companion, "maybe_generate_move_comment", fake_maybe_comment
    )

    calls = []
    monkeypatch.setattr(
        "channels.ui_push.pseudo_stream_push",
        lambda *a, **kw: calls.append((a, kw)),
    )

    body = chess_router.CommentRequest(session_id="s1", uid="user1")
    result = await chess_router.chess_comment(body, auth=None)

    assert result["comment"] is None
    assert result["msg_id"] is None
    assert calls == []


# ── gomoku_chat / gomoku_comment ─────────────────────────────────────────────

def _fake_gomoku_session(session_id="s1", uid="user1", char_id="yexuan"):
    from core.activity.session import ActivitySession

    return ActivitySession(
        session_id=session_id, uid=uid, char_id=char_id, activity_type="gomoku",
        status="active", state={}, created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_gomoku_chat_wires_pseudo_stream_and_msg_id(sandbox, monkeypatch):
    from admin.routers import gomoku as gomoku_router

    session = _fake_gomoku_session()
    monkeypatch.setattr(gomoku_router, "_active_char_id", lambda: "yexuan")
    monkeypatch.setattr(
        gomoku_router.gomoku_store, "load_session", lambda *a, **kw: session
    )

    async def fake_generate_reply(**kwargs):
        return "这手棋很有意思。", {"c": 1}, {"g": 1}

    monkeypatch.setattr(gomoku_router.gomoku_companion, "generate_reply", fake_generate_reply)

    calls = []

    async def fake_pseudo_stream_push(text, *, msg_id, char_id="", **kw):
        calls.append((text, msg_id, char_id))

    monkeypatch.setattr("channels.ui_push.pseudo_stream_push", fake_pseudo_stream_push)

    body = gomoku_router.ChatRequest(session_id="s1", message="你好", uid="user1")
    result = await gomoku_router.gomoku_chat(body, auth=None)

    assert result["reply"] == "这手棋很有意思。"
    assert result["msg_id"]
    assert calls == [("这手棋很有意思。", result["msg_id"], "yexuan")]


@pytest.mark.asyncio
async def test_gomoku_comment_no_push_when_comment_is_none(sandbox, monkeypatch):
    from admin.routers import gomoku as gomoku_router

    session = _fake_gomoku_session()
    monkeypatch.setattr(gomoku_router, "_active_char_id", lambda: "yexuan")
    monkeypatch.setattr(
        gomoku_router.gomoku_store, "load_session", lambda *a, **kw: session
    )

    async def fake_maybe_comment(**kwargs):
        return None, {"g": 1}

    monkeypatch.setattr(
        gomoku_router.gomoku_companion, "maybe_generate_move_comment", fake_maybe_comment
    )

    calls = []
    monkeypatch.setattr(
        "channels.ui_push.pseudo_stream_push",
        lambda *a, **kw: calls.append((a, kw)),
    )

    body = gomoku_router.CommentRequest(session_id="s1", uid="user1")
    result = await gomoku_router.gomoku_comment(body, auth=None)

    assert result["comment"] is None
    assert result["msg_id"] is None
    assert calls == []


# ── reading_chat ─────────────────────────────────────────────────────────────

def _fake_reading_session(session_id="s1", uid="user1", char_id="yexuan"):
    from core.activity.reading_session import ReadingSession

    return ReadingSession(
        session_id=session_id, uid=uid, char_id=char_id, file_id="f1",
        filename="book.pdf", total_pages=10, current_page=1,
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
        status="active",
    )


@pytest.mark.asyncio
async def test_reading_chat_wires_pseudo_stream_and_msg_id(sandbox, monkeypatch):
    from admin.routers import reading as reading_router

    session = _fake_reading_session()
    monkeypatch.setattr(reading_router, "_active_char_id", lambda: "yexuan")
    monkeypatch.setattr(reading_router, "_require_session", lambda char_id, sid: session)
    monkeypatch.setattr(
        reading_router.activity_store, "load_page", lambda *a, **kw: None
    )

    async def fake_generate_reply(**kwargs):
        return "这本书写得真好。", {"c": 1}, {"g": 1}

    monkeypatch.setattr(reading_router.reading_companion, "generate_reply", fake_generate_reply)

    calls = []

    async def fake_pseudo_stream_push(text, *, msg_id, char_id="", **kw):
        calls.append((text, msg_id, char_id))

    monkeypatch.setattr("channels.ui_push.pseudo_stream_push", fake_pseudo_stream_push)

    body = reading_router.ChatRequest(session_id="s1", message="你好")
    result = await reading_router.reading_chat(body, auth=None)

    assert result["reply"] == "这本书写得真好。"
    assert result["msg_id"]
    assert calls == [("这本书写得真好。", result["msg_id"], "yexuan")]
