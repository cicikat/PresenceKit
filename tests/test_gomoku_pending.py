"""
tests/test_gomoku_pending.py

Gomoku pending_ai_turn P0 验收测试 (19 用例)

覆盖（对应任务规格中的测试要求）：
T1.  pending mode 下，用户落子后 AI 不立刻落子（move_history 只含用户一步）
T2.  pending mode 下，move response pending_ai_turn=True
T3.  pending mode 下，board 只新增用户黑棋
T4.  apply_ai_move 后 AI 落白（board 含白棋）
T5.  apply_ai_move 后 pending_ai_turn=False
T6.  apply_ai_move 后 current_turn 回到 "black"
T7.  apply_ai_move 后 move_history 新增 AI move（source="ai"）
T8.  用户落子胜利后 pending_ai_turn=False，AI 不落子
T9.  session completed 后 apply_ai_move 抛 ValueError（映射 409）
T10. human opponent 下 apply_ai_move 抛 ValueError（映射 409）
T11. pending_ai_turn=False 时 apply_ai_move 抛 ValueError（映射 409）
T12. transcript 中有 ai_style_tilt="gentle" 时，AI move style="gentle"
T13. transcript 无有效 control 时，AI move 使用 base_style
T14. 非法 ai_style_tilt 值被忽略，回退到 base_style
T15. apply_ai_move 后不写 short_term
T16. apply_ai_move 后不写 hidden_state
T17. auto mode 旧行为不变（用户落子后 AI 自动落子，pending_ai_turn=False）
T18. registry 包含 activity_gomoku_ai_move tauri command
T19. did_hold_back 在 gentle style_tilt 时通过 grounding 反映为 True
"""
from __future__ import annotations

import pytest

from core.activity import gomoku as G
from core.activity import store as activity_store
from core.activity import transcript as TR
from core.activity import gomoku_companion as GC
from core.activity.gomoku_grounding import build_gomoku_grounding_facts


# ── Helpers ────────────────────────────────────────────────────────────────────

def _start_pending(sandbox, uid="user1", char_id="yexuan", ai_style="balanced"):
    return G.start_game(uid, char_id, opponent="yexuan_ai", ai_style=ai_style,
                        ai_response_mode="pending")


def _start_auto(sandbox, uid="user1", char_id="yexuan", ai_style="balanced"):
    return G.start_game(uid, char_id, opponent="yexuan_ai", ai_style=ai_style,
                        ai_response_mode="auto")


def _start_human(sandbox, uid="user1", char_id="yexuan"):
    return G.start_game(uid, char_id, opponent="human")


def _white_count(board) -> int:
    return sum(1 for row in board for cell in row if cell == "white")


def _black_count(board) -> int:
    return sum(1 for row in board for cell in row if cell == "black")


# ── T1: pending mode — AI does not move immediately ────────────────────────────

def test_pending_mode_user_move_no_ai_move(sandbox):
    session = _start_pending(sandbox)
    result = G.make_move(session.uid, session.char_id, session.session_id, 7, 7)
    # move_history should only have 1 entry (user's black stone)
    assert len(result["move_history"]) == 1
    assert result["move_history"][0]["player"] == "black"


# ── T2: pending mode — move response has pending_ai_turn=True ─────────────────

def test_pending_mode_move_response_pending_true(sandbox):
    session = _start_pending(sandbox)
    result = G.make_move(session.uid, session.char_id, session.session_id, 7, 7)
    assert result["pending_ai_turn"] is True


# ── T3: pending mode — board only gains user's black stone ────────────────────

def test_pending_mode_board_only_has_user_stone(sandbox):
    session = _start_pending(sandbox)
    result = G.make_move(session.uid, session.char_id, session.session_id, 7, 7)
    assert result["board"][7][7] == "black"
    assert _white_count(result["board"]) == 0, "no white stones should be placed yet"
    assert _black_count(result["board"]) == 1


# ── T4: apply_ai_move places a white stone ────────────────────────────────────

def test_apply_ai_move_places_white_stone(sandbox):
    session = _start_pending(sandbox)
    G.make_move(session.uid, session.char_id, session.session_id, 7, 7)
    result = G.apply_ai_move(session.uid, session.char_id, session.session_id)
    assert _white_count(result["board"]) == 1, "apply_ai_move must place exactly one white stone"


# ── T5: apply_ai_move clears pending_ai_turn ──────────────────────────────────

def test_apply_ai_move_clears_pending(sandbox):
    session = _start_pending(sandbox)
    G.make_move(session.uid, session.char_id, session.session_id, 7, 7)
    result = G.apply_ai_move(session.uid, session.char_id, session.session_id)
    assert result["pending_ai_turn"] is False


# ── T6: apply_ai_move returns current_turn="black" ────────────────────────────

def test_apply_ai_move_turn_returns_to_black(sandbox):
    session = _start_pending(sandbox)
    G.make_move(session.uid, session.char_id, session.session_id, 7, 7)
    result = G.apply_ai_move(session.uid, session.char_id, session.session_id)
    assert result["current_turn"] == "black"


# ── T7: apply_ai_move appends AI move to move_history ─────────────────────────

def test_apply_ai_move_adds_ai_move_to_history(sandbox):
    session = _start_pending(sandbox)
    G.make_move(session.uid, session.char_id, session.session_id, 7, 7)
    result = G.apply_ai_move(session.uid, session.char_id, session.session_id)
    ai_moves = [m for m in result["move_history"] if m.get("source") == "ai"]
    assert len(ai_moves) == 1
    assert ai_moves[0]["player"] == "white"
    assert "x" in ai_moves[0] and "y" in ai_moves[0]


# ── T8: user wins → pending_ai_turn stays False ───────────────────────────────

def test_user_wins_no_pending_ai_turn(sandbox, monkeypatch):
    # Mock AI to always play at a safe corner so it can't block the winning line.
    # Must patch the name in the gomoku module (already imported at module level there).
    monkeypatch.setattr(G, "choose_gomoku_ai_move", lambda *a, **kw: (14, 14))

    session = _start_pending(sandbox)
    uid, char_id, sid = session.uid, session.char_id, session.session_id
    # Layout: black row at y=5: (0,5),(1,5),(2,5),(3,5) then win at (4,5)
    # AI always plays (14,14) so can't block.
    for x in range(4):
        G.make_move(uid, char_id, sid, x, 5)
        G.apply_ai_move(uid, char_id, sid)
    result = G.make_move(uid, char_id, sid, 4, 5)
    assert result["status"] == "completed"
    assert result["winner"] == "black"
    assert result["pending_ai_turn"] is False, "winning move must not set pending_ai_turn"


# ── T9: completed session → apply_ai_move raises ValueError ───────────────────

def test_apply_ai_move_on_completed_raises(sandbox):
    session = _start_pending(sandbox)
    uid, char_id, sid = session.uid, session.char_id, session.session_id
    # Manually force completion via state manipulation
    state = session.state
    state["status"] = "completed"
    state["winner"] = "black"
    state["pending_ai_turn"] = True
    activity_store.update_state(char_id, uid, "gomoku", sid, state)
    with pytest.raises(ValueError, match="已关闭|已结束"):
        G.apply_ai_move(uid, char_id, sid)


# ── T10: human opponent → apply_ai_move raises ValueError ────────────────────

def test_apply_ai_move_human_opponent_raises(sandbox):
    session = _start_human(sandbox)
    uid, char_id, sid = session.uid, session.char_id, session.session_id
    # Force pending_ai_turn=True on a human game (shouldn't happen in practice)
    state = session.state
    state["pending_ai_turn"] = True
    activity_store.update_state(char_id, uid, "gomoku", sid, state)
    with pytest.raises(ValueError, match="非 AI 对手"):
        G.apply_ai_move(uid, char_id, sid)


# ── T11: pending_ai_turn=False → apply_ai_move raises ValueError ─────────────

def test_apply_ai_move_not_pending_raises(sandbox):
    session = _start_pending(sandbox)
    uid, char_id, sid = session.uid, session.char_id, session.session_id
    # Don't make any move — pending_ai_turn is still False
    with pytest.raises(ValueError, match="pending_ai_turn"):
        G.apply_ai_move(uid, char_id, sid)


# ── T12: recent control ai_style_tilt="gentle" → AI move style="gentle" ──────

def test_style_tilt_gentle_applied_to_ai_move(sandbox):
    session = _start_pending(sandbox)
    uid, char_id, sid = session.uid, session.char_id, session.session_id

    # Write a transcript entry with ai_style_tilt="gentle"
    TR.append_entry(char_id, uid, "gomoku", sid, {
        "type": "assistant_chat",
        "text": "这局我温和一点。",
        "ts": "2026-06-10T00:00:00+00:00",
        "control": {"ai_style_tilt": "gentle"},
    })

    G.make_move(uid, char_id, sid, 7, 7)
    # Simulate what the router does: read tilt then apply
    tilt = GC.get_recent_ai_style_tilt(char_id, uid, sid)
    assert tilt == "gentle"

    result = G.apply_ai_move(uid, char_id, sid, style_tilt=tilt)
    ai_move = next(m for m in result["move_history"] if m.get("source") == "ai")
    assert ai_move["style"] == "gentle"
    assert ai_move["base_style"] == "balanced"
    assert ai_move["style_source"] == "activity_chat_control"


# ── T13: no control in transcript → AI move uses base_style ──────────────────

def test_no_control_uses_base_style(sandbox):
    session = _start_pending(sandbox, ai_style="serious")
    uid, char_id, sid = session.uid, session.char_id, session.session_id

    tilt = GC.get_recent_ai_style_tilt(char_id, uid, sid)
    assert tilt is None

    G.make_move(uid, char_id, sid, 7, 7)
    result = G.apply_ai_move(uid, char_id, sid, style_tilt=None)
    ai_move = next(m for m in result["move_history"] if m.get("source") == "ai")
    assert ai_move["style"] == "serious"
    assert ai_move["style_source"] == "base_style"


# ── T14: invalid ai_style_tilt is ignored, falls back to base_style ───────────

def test_invalid_style_tilt_ignored(sandbox):
    session = _start_pending(sandbox, ai_style="balanced")
    uid, char_id, sid = session.uid, session.char_id, session.session_id

    G.make_move(uid, char_id, sid, 7, 7)
    # Pass an invalid tilt value
    result = G.apply_ai_move(uid, char_id, sid, style_tilt="aggressive")
    ai_move = next(m for m in result["move_history"] if m.get("source") == "ai")
    assert ai_move["style"] == "balanced"
    assert ai_move["style_source"] == "base_style"


# ── T15: apply_ai_move does not write short_term ──────────────────────────────

def test_apply_ai_move_no_short_term_write(sandbox):
    session = _start_pending(sandbox)
    uid, char_id, sid = session.uid, session.char_id, session.session_id
    G.make_move(uid, char_id, sid, 7, 7)
    G.apply_ai_move(uid, char_id, sid)
    history_dir = sandbox._base / "history"
    assert not history_dir.exists(), "apply_ai_move must not create short_term history dir"


# ── T16: apply_ai_move does not write hidden_state ───────────────────────────

def test_apply_ai_move_no_hidden_state_write(sandbox):
    session = _start_pending(sandbox)
    uid, char_id, sid = session.uid, session.char_id, session.session_id
    G.make_move(uid, char_id, sid, 7, 7)
    G.apply_ai_move(uid, char_id, sid)
    memory_dir = sandbox._base / "memory"
    if memory_dir.exists():
        hs_files = list(memory_dir.rglob("*hidden_state*"))
        assert len(hs_files) == 0, "apply_ai_move must not write hidden_state files"


# ── T17: auto mode still auto-places AI stone immediately ────────────────────

def test_auto_mode_ai_moves_immediately(sandbox):
    session = _start_auto(sandbox)
    result = G.make_move(session.uid, session.char_id, session.session_id, 7, 7)
    assert result["pending_ai_turn"] is False
    ai_moves = [m for m in result["move_history"] if m.get("source") == "ai"]
    assert len(ai_moves) == 1, "auto mode must place AI stone immediately after user move"
    assert _white_count(result["board"]) == 1


# ── T18: registry contains activity_gomoku_ai_move ───────────────────────────

def test_registry_gomoku_has_ai_move_command():
    from core.activity.registry import get_activity_meta
    meta = get_activity_meta("gomoku")
    assert "activity_gomoku_ai_move" in meta.tauri_commands, (
        "activity_gomoku_ai_move must be declared in gomoku tauri_commands"
    )


# ── T19: grounding did_hold_back=True when last AI move style="gentle" ────────

def test_grounding_did_hold_back_gentle_tilt(sandbox):
    session = _start_pending(sandbox, ai_style="balanced")
    uid, char_id, sid = session.uid, session.char_id, session.session_id
    G.make_move(uid, char_id, sid, 7, 7)
    # Apply AI move with gentle tilt
    G.apply_ai_move(uid, char_id, sid, style_tilt="gentle")

    reloaded = activity_store.load_session(char_id, uid, "gomoku", sid)
    facts = build_gomoku_grounding_facts(reloaded.state)
    assert facts["did_hold_back"] is True, (
        "grounding should report did_hold_back=True when last AI move used gentle style"
    )


def test_grounding_did_hold_back_false_balanced_tilt(sandbox):
    session = _start_pending(sandbox, ai_style="balanced")
    uid, char_id, sid = session.uid, session.char_id, session.session_id
    G.make_move(uid, char_id, sid, 7, 7)
    G.apply_ai_move(uid, char_id, sid, style_tilt=None)  # no tilt, base balanced

    reloaded = activity_store.load_session(char_id, uid, "gomoku", sid)
    facts = build_gomoku_grounding_facts(reloaded.state)
    assert facts["did_hold_back"] is False, (
        "grounding should report did_hold_back=False when last AI move used balanced style"
    )


# ── get_recent_ai_style_tilt: returns most recent valid tilt ──────────────────

def test_get_recent_style_tilt_returns_latest(sandbox):
    char_id, uid, sid = "yexuan", "user1", "tilt_test_session"
    # Write two assistant_chat entries
    TR.append_entry(char_id, uid, "gomoku", sid, {
        "type": "assistant_chat",
        "text": "...",
        "ts": "2026-06-10T00:00:00+00:00",
        "control": {"ai_style_tilt": "serious"},
    })
    TR.append_entry(char_id, uid, "gomoku", sid, {
        "type": "assistant_chat",
        "text": "...",
        "ts": "2026-06-10T00:01:00+00:00",
        "control": {"ai_style_tilt": "gentle"},
    })
    tilt = GC.get_recent_ai_style_tilt(char_id, uid, sid)
    assert tilt == "gentle", "should return the most recent valid ai_style_tilt"


def test_get_recent_style_tilt_returns_none_when_empty(sandbox):
    char_id, uid, sid = "yexuan", "user1", "empty_tilt_session"
    tilt = GC.get_recent_ai_style_tilt(char_id, uid, sid)
    assert tilt is None


def test_get_recent_style_tilt_ignores_invalid(sandbox):
    char_id, uid, sid = "yexuan", "user1", "invalid_tilt_session"
    TR.append_entry(char_id, uid, "gomoku", sid, {
        "type": "assistant_chat",
        "text": "...",
        "ts": "2026-06-10T00:00:00+00:00",
        "control": {"ai_style_tilt": "aggressive"},
    })
    tilt = GC.get_recent_ai_style_tilt(char_id, uid, sid)
    assert tilt is None, "invalid ai_style_tilt values must be ignored"
