"""
tests/test_chess_style_tilt.py

Chess AI style tilt (Brief 43 §E) — mirrors gomoku's tilt tests
(tests/test_gomoku_pending.py T12-T14 + get_recent_ai_style_tilt tests).

T1. valid ai_style_tilt in transcript control → apply_ai_move uses tilt style
T2. no control in transcript → apply_ai_move uses base_style
T3. invalid ai_style_tilt is ignored, falls back to base_style
T4. move entry carries style / base_style / style_source
T5. get_recent_ai_style_tilt returns the most recent valid tilt
T6. get_recent_ai_style_tilt returns None when transcript is empty
T7. get_recent_ai_style_tilt ignores invalid tilt values
T8. session's base ai_style is never overwritten by a tilt
"""
from __future__ import annotations

import chess
import pytest

from core.activity import chess as CH
from core.activity import chess_companion as CC
from core.activity import transcript as TR


def _pending_state(ai_style: str = "balanced") -> dict:
    state = CH.make_initial_state(opponent="character_ai", ai_style=ai_style)
    # Force it to be black's (AI's) turn with a pending flag, as if user just moved.
    state = CH.apply_move(state, "e2e4")
    assert state["pending_ai_turn"] is True
    return state


# ── T1: valid tilt applied for this move only ──────────────────────────────────

def test_valid_tilt_applied_to_ai_move():
    state = _pending_state(ai_style="balanced")
    new_state = CH.apply_ai_move(state, style_tilt="serious")
    ai_move = new_state["last_move"]
    assert ai_move["style"] == "serious"
    assert ai_move["base_style"] == "balanced"
    assert ai_move["style_source"] == "activity_chat_control"


# ── T2: no tilt → base_style used ──────────────────────────────────────────────

def test_no_tilt_uses_base_style():
    state = _pending_state(ai_style="serious")
    new_state = CH.apply_ai_move(state, style_tilt=None)
    ai_move = new_state["last_move"]
    assert ai_move["style"] == "serious"
    assert ai_move["style_source"] == "base_style"


# ── T3: invalid tilt ignored ────────────────────────────────────────────────────

def test_invalid_tilt_ignored():
    state = _pending_state(ai_style="balanced")
    new_state = CH.apply_ai_move(state, style_tilt="aggressive")
    ai_move = new_state["last_move"]
    assert ai_move["style"] == "balanced"
    assert ai_move["style_source"] == "base_style"


# ── T4: move_history entry carries the fields too ──────────────────────────────

def test_move_history_entry_has_style_fields():
    state = _pending_state(ai_style="gentle")
    new_state = CH.apply_ai_move(state, style_tilt="teaching")
    entry = new_state["move_history"][-1]
    assert entry["style"] == "teaching"
    assert entry["base_style"] == "gentle"
    assert entry["style_source"] == "activity_chat_control"


# ── T8: session base ai_style is not permanently overwritten ──────────────────

def test_base_ai_style_not_overwritten():
    state = _pending_state(ai_style="balanced")
    new_state = CH.apply_ai_move(state, style_tilt="serious")
    assert new_state["ai_style"] == "balanced"


# ── get_recent_ai_style_tilt ────────────────────────────────────────────────────

def test_get_recent_style_tilt_returns_latest(sandbox):
    char_id, uid, sid = "yexuan", "user1", "chess_tilt_session"
    TR.append_entry(char_id, uid, "chess", sid, {
        "type": "assistant_chat",
        "text": "...",
        "ts": "2026-07-11T00:00:00+00:00",
        "control": {"ai_style_tilt": "serious"},
    })
    TR.append_entry(char_id, uid, "chess", sid, {
        "type": "assistant_chat",
        "text": "...",
        "ts": "2026-07-11T00:01:00+00:00",
        "control": {"ai_style_tilt": "gentle"},
    })
    tilt = CC.get_recent_ai_style_tilt(char_id, uid, sid)
    assert tilt == "gentle"


def test_get_recent_style_tilt_none_when_empty(sandbox):
    tilt = CC.get_recent_ai_style_tilt("yexuan", "user1", "empty_chess_tilt_session")
    assert tilt is None


def test_get_recent_style_tilt_ignores_invalid(sandbox):
    char_id, uid, sid = "yexuan", "user1", "invalid_chess_tilt_session"
    TR.append_entry(char_id, uid, "chess", sid, {
        "type": "assistant_chat",
        "text": "...",
        "ts": "2026-07-11T00:00:00+00:00",
        "control": {"ai_style_tilt": "aggressive"},
    })
    tilt = CC.get_recent_ai_style_tilt(char_id, uid, sid)
    assert tilt is None


# ── control block parsing accepts ai_style_tilt ────────────────────────────────

@pytest.mark.asyncio
async def test_chat_control_parses_ai_style_tilt(sandbox, monkeypatch):
    reply_with_control = (
        '好的。\n\n<activity_control>\n{"ai_style_tilt":"teaching"}\n</activity_control>'
    )

    async def _chat(messages, **kwargs):
        return reply_with_control
    monkeypatch.setattr("core.llm_client.chat", _chat)

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
    reply, control, grounding = await CC.generate_reply("yexuan", "user1", "sessTilt", state, "test")
    assert control.get("ai_style_tilt") == "teaching"
