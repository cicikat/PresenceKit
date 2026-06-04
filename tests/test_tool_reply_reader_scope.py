"""
tests/test_tool_reply_reader_scope.py — P1-0A: _reply_with_tool_result reader char_id scope

Verifies that _reply_with_tool_result reads short_term and user_profile using the
active character's bucket (not the default "yexuan" fallback).

Covers:
  1. short_term.load_for_prompt receives active char_id
  2. user_profile.load receives active char_id
  3. Character switch → reader picks up new bucket
  4. Invalid active_character → fail-loud, readers never called
  5. Content isolation: hongcha active → yexuan content absent from LLM context
  6. Regression: output governance chain (process/strip/scrub/post_process) intact
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_pipeline(active_char_id: str, llm_reply: str = "回复", refresh_raises=None):
    fake = MagicMock()
    fake.character = MagicMock()
    fake.character.name = "TestChar"
    fake.author_note_extra = ""
    fake._active_character_id = active_char_id
    fake.build_prompt = MagicMock(return_value=([], {"pending_paths": []}))
    fake.run_llm = AsyncMock(return_value=llm_reply)
    fake.post_process = AsyncMock(
        return_value={"turn_id": "t1", "critical_written": True, "emotion": "neutral"}
    )
    if refresh_raises is not None:
        fake._refresh_character_if_needed = MagicMock(side_effect=refresh_raises)
    else:
        fake._refresh_character_if_needed = MagicMock()
    return fake


def _patch_memory_capture(monkeypatch):
    """Patch short_term/user_profile to capture kwargs; return captured-call lists."""
    import core.memory.short_term as _st
    import core.memory.user_profile as _up
    import core.memory.group_context as _gc
    import core.user_relation as _ur

    st_calls: list[dict] = []
    up_calls: list[dict] = []

    def _st_load(uid, **kw):
        st_calls.append(kw)
        return []

    def _up_load(uid, **kw):
        up_calls.append(kw)
        return {}

    monkeypatch.setattr(_st, "load_for_prompt", _st_load)
    monkeypatch.setattr(_up, "load", _up_load)
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_ur, "get_relation", lambda uid: {})
    return st_calls, up_calls


def _patch_text_output(monkeypatch):
    import core.output.text_output as _to
    mock_send = AsyncMock()
    monkeypatch.setattr(_to, "send", mock_send)
    return mock_send


def _patch_rp(monkeypatch):
    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])


# ═══════════════════════════════════════════════════════════════════════════════
# 1. short_term.load_for_prompt receives active char_id
# ═══════════════════════════════════════════════════════════════════════════════

async def test_short_term_receives_active_char_id(sandbox, monkeypatch):
    import main as _main

    fake = _make_pipeline("hongcha")
    monkeypatch.setattr(_main, "_pipeline", fake)
    st_calls, _ = _patch_memory_capture(monkeypatch)
    _patch_text_output(monkeypatch)
    _patch_rp(monkeypatch)

    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)

    assert st_calls, "short_term.load_for_prompt should have been called"
    assert st_calls[0].get("char_id") == "hongcha", (
        f"expected char_id='hongcha', got {st_calls[0]}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. user_profile.load receives active char_id
# ═══════════════════════════════════════════════════════════════════════════════

async def test_user_profile_receives_active_char_id(sandbox, monkeypatch):
    import main as _main

    fake = _make_pipeline("hongcha")
    monkeypatch.setattr(_main, "_pipeline", fake)
    _, up_calls = _patch_memory_capture(monkeypatch)
    _patch_text_output(monkeypatch)
    _patch_rp(monkeypatch)

    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)

    assert up_calls, "user_profile.load should have been called"
    assert up_calls[0].get("char_id") == "hongcha", (
        f"expected char_id='hongcha', got {up_calls[0]}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Character switch → reader follows new bucket
# ═══════════════════════════════════════════════════════════════════════════════

async def test_character_switch_reader_follows(sandbox, monkeypatch):
    import main as _main
    import core.memory.short_term as _st
    import core.memory.user_profile as _up
    import core.memory.group_context as _gc
    import core.user_relation as _ur

    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_ur, "get_relation", lambda uid: {})
    monkeypatch.setattr(_up, "load", lambda uid, **kw: {})
    _patch_text_output(monkeypatch)
    _patch_rp(monkeypatch)

    received_chars: list[str] = []

    def _st_load(uid, **kw):
        received_chars.append(kw.get("char_id", "__missing__"))
        return []

    monkeypatch.setattr(_st, "load_for_prompt", _st_load)

    # First call: yexuan
    monkeypatch.setattr(_main, "_pipeline", _make_pipeline("yexuan"))
    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)

    # Second call: hongcha
    monkeypatch.setattr(_main, "_pipeline", _make_pipeline("hongcha"))
    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)

    assert len(received_chars) == 2, f"expected 2 reader calls, got {received_chars}"
    assert received_chars[0] == "yexuan", f"first call expected yexuan, got {received_chars[0]}"
    assert received_chars[1] == "hongcha", (
        f"second call expected hongcha (not yexuan), got {received_chars[1]}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Invalid active_character → fail-loud, readers never called
# ═══════════════════════════════════════════════════════════════════════════════

async def test_invalid_active_char_no_reader_called(sandbox, monkeypatch):
    import main as _main
    import core.memory.short_term as _st
    import core.memory.user_profile as _up
    import core.memory.group_context as _gc
    import core.user_relation as _ur

    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_ur, "get_relation", lambda uid: {})

    def _fail_if_called(*a, **kw):
        pytest.fail("Reader must not be called when active_character is invalid")

    monkeypatch.setattr(_st, "load_for_prompt", _fail_if_called)
    monkeypatch.setattr(_up, "load", _fail_if_called)

    _patch_text_output(monkeypatch)
    _patch_rp(monkeypatch)

    fake = _make_pipeline(
        "missing_id",
        refresh_raises=ValueError("[pipeline] active_character 'missing_id' 无法加载"),
    )
    monkeypatch.setattr(_main, "_pipeline", fake)

    # Must not propagate — should log error and return
    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)
    # If we reach here, readers were not called (otherwise _fail_if_called raised)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Content isolation: hongcha active → yexuan sentinel absent from LLM context
# ═══════════════════════════════════════════════════════════════════════════════

async def test_content_isolation_yexuan_not_in_hongcha_context(sandbox, monkeypatch):
    import main as _main
    import core.memory.short_term as _st
    import core.memory.user_profile as _up
    import core.memory.group_context as _gc
    import core.user_relation as _ur

    SENTINEL = "草莓大福-tool"

    def _st_load(uid, **kw):
        return [{"role": "user", "content": SENTINEL}] if kw.get("char_id") == "yexuan" else []

    def _up_load(uid, **kw):
        return {"marker": SENTINEL} if kw.get("char_id") == "yexuan" else {}

    monkeypatch.setattr(_st, "load_for_prompt", _st_load)
    monkeypatch.setattr(_up, "load", _up_load)
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_ur, "get_relation", lambda uid: {})
    _patch_text_output(monkeypatch)
    _patch_rp(monkeypatch)

    captured_ctx: dict = {}

    def _fake_build_prompt(uid, content, ctx, **kw):
        captured_ctx.update(ctx)
        return ([], {"pending_paths": []})

    fake = _make_pipeline("hongcha")
    fake.build_prompt = MagicMock(side_effect=_fake_build_prompt)
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)

    assert fake.build_prompt.called, "build_prompt should have been called"
    history_str = str(captured_ctx.get("history", []))
    profile_str = str(captured_ctx.get("profile", {}))
    assert SENTINEL not in history_str, (
        f"yexuan history sentinel leaked into hongcha context: {history_str!r}"
    )
    assert SENTINEL not in profile_str, (
        f"yexuan profile sentinel leaked into hongcha context: {profile_str!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Regression: output governance chain intact after char_id plumbing
# ═══════════════════════════════════════════════════════════════════════════════

async def test_output_governance_chain_intact(sandbox, monkeypatch):
    """process → strip_render_tags → scrub → post_process must still fire in order."""
    import main as _main

    fake = _make_pipeline("hongcha", llm_reply="<say>测试内容。</say>")
    monkeypatch.setattr(_main, "_pipeline", fake)
    _patch_memory_capture(monkeypatch)
    sent = _patch_text_output(monkeypatch)
    _patch_rp(monkeypatch)

    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)
    await asyncio.sleep(0.05)

    # post_process was called (memory write path intact)
    fake.post_process.assert_called_once()

    # QQ output has render tags stripped
    assert sent.call_count >= 1
    all_sent_text = " ".join(str(arg) for call in sent.call_args_list for arg in call.args)
    assert "<say>" not in all_sent_text, "<say> tag must be stripped before QQ send"
