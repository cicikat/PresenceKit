"""
tests/test_tool_probe_reader_scope.py — P1-0B: handle_message probe reader char_id scope

Verifies that the probe path in handle_message reads user_profile using the
active character's bucket (not the default "yexuan" fallback).

Covers:
  1. probe calls user_profile.load with char_id="hongcha"
  2. active switches yexuan → hongcha; second probe reads hongcha bucket
  3. active invalid → fail-loud, user_profile.load never called
  4. Content isolation: hongcha active → yexuan profile sentinel absent from probe context
  5. Regression: original probe path unbroken when active character is valid
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_pipeline(active_char_id: str, refresh_raises=None):
    from core.memory.scope import MemoryScope

    fake = MagicMock()
    fake.character = MagicMock()
    fake.character.name = "TestChar"
    fake.author_note_extra = ""
    fake._active_character_id = active_char_id
    fake.fetch_context = AsyncMock(return_value={})
    fake.build_prompt = MagicMock(return_value=([], {"pending_paths": []}))
    fake.run_llm = AsyncMock(return_value="回复")
    fake.post_process = AsyncMock(
        return_value={"turn_id": "t1", "critical_written": True, "emotion": "neutral"}
    )
    if refresh_raises is not None:
        # _current_reality_scope calls _refresh_character_if_needed; mock it to raise
        fake._current_reality_scope = MagicMock(side_effect=refresh_raises)
        fake._refresh_character_if_needed = MagicMock(side_effect=refresh_raises)
    else:
        fake._refresh_character_if_needed = MagicMock()
        # Return a proper MemoryScope so _char_id = _frozen_scope.character_id resolves
        fake._current_reality_scope = MagicMock(
            side_effect=lambda uid: MemoryScope.reality_scope(str(uid), active_char_id)
        )
    return fake


_MSG = {"user_id": "probe_test_uid", "content": "现在几点", "sender_name": "tester"}


def _patch_env(monkeypatch):
    """Patch all handle_message dependencies except user_profile.load."""
    import core.config_loader as _cl
    import core.scheduler.loop as _sl
    import core.scheduler.state_machine as _sm
    import core.presence as _pr
    import core.memory.group_context as _gc
    import core.tool_dispatcher as _td
    import core.llm_client as _llm
    import core.response_processor as _rp
    import core.output.text_output as _to

    monkeypatch.setattr(_cl, "get_config", lambda: {
        "scheduler": {"owner_id": "99999"},
        "llm": {"tool_call_mode": "function_calling"},
    })
    monkeypatch.setattr(_sl, "mark_user_active", lambda: None)
    monkeypatch.setattr(_sm, "notify_owner_turn", lambda uid: None)
    monkeypatch.setattr(_pr, "update_last_message", lambda uid: None)
    monkeypatch.setattr(_gc, "append", lambda *a, **kw: None)
    _td._TOOL_REGISTRY = {}
    monkeypatch.setattr(_td, "get_probe_prompt", lambda loc: "")
    monkeypatch.setattr(_td, "get_tools_schema", lambda categories=None: [])
    monkeypatch.setattr(_llm, "chat", AsyncMock(return_value=""))
    monkeypatch.setattr(_llm, "parse_tool_call_response", lambda r: [])
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])
    monkeypatch.setattr(_to, "send", AsyncMock())


# ═══════════════════════════════════════════════════════════════════════════════
# 1. probe calls user_profile.load with char_id="hongcha"
# ═══════════════════════════════════════════════════════════════════════════════

async def test_probe_passes_active_char_id(sandbox, monkeypatch):
    import main as _main
    import core.memory.user_profile as _up

    _patch_env(monkeypatch)
    monkeypatch.setattr(_main, "_pipeline", _make_pipeline("hongcha"))

    up_calls: list[dict] = []

    def _capture_load(uid, **kw):
        up_calls.append(kw)
        return {}

    monkeypatch.setattr(_up, "load", _capture_load)

    await _main.handle_message(_MSG)

    probe_calls = [c for c in up_calls if c]  # filter empty-kw calls if any
    assert probe_calls, "user_profile.load should have been called in the probe path"
    assert probe_calls[0].get("char_id") == "hongcha", (
        f"expected char_id='hongcha', got {probe_calls[0]}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. active switches yexuan → hongcha; second probe reads hongcha bucket
# ═══════════════════════════════════════════════════════════════════════════════

async def test_char_switch_probe_follows_new_bucket(sandbox, monkeypatch):
    import main as _main
    import core.memory.user_profile as _up

    _patch_env(monkeypatch)

    received: list[str] = []

    def _capture_load(uid, **kw):
        received.append(kw.get("char_id", "__missing__"))
        return {}

    monkeypatch.setattr(_up, "load", _capture_load)

    monkeypatch.setattr(_main, "_pipeline", _make_pipeline("yexuan"))
    await _main.handle_message(_MSG)

    monkeypatch.setattr(_main, "_pipeline", _make_pipeline("hongcha"))
    await _main.handle_message(_MSG)

    assert len(received) >= 2, f"expected at least 2 load calls, got {received}"
    assert received[0] == "yexuan", f"first probe expected yexuan, got {received[0]}"
    assert received[1] == "hongcha", (
        f"second probe expected hongcha (not yexuan), got {received[1]}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. active invalid → fail-loud, user_profile.load never called
# ═══════════════════════════════════════════════════════════════════════════════

async def test_invalid_active_char_probe_aborts(sandbox, monkeypatch):
    import main as _main
    import core.memory.user_profile as _up

    _patch_env(monkeypatch)

    def _must_not_call(*a, **kw):
        pytest.fail("user_profile.load must not be called when active_character is invalid")

    monkeypatch.setattr(_up, "load", _must_not_call)

    fake = _make_pipeline(
        "missing_char",
        refresh_raises=ValueError("[pipeline] active_character 'missing_char' 无法加载"),
    )
    monkeypatch.setattr(_main, "_pipeline", fake)

    # Must not raise, must not call user_profile.load
    await _main.handle_message(_MSG)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Content isolation: hongcha active → yexuan profile sentinel absent from probe
# ═══════════════════════════════════════════════════════════════════════════════

async def test_content_isolation_yexuan_sentinel_not_in_probe(sandbox, monkeypatch):
    import main as _main
    import core.memory.user_profile as _up
    import core.tool_dispatcher as _td

    _patch_env(monkeypatch)

    YEXUAN_SENTINEL = "杭州-yexuan-unique-location"

    def _fake_load(uid, **kw):
        if kw.get("char_id") == "yexuan":
            return {"location": YEXUAN_SENTINEL}
        return {"location": "绍兴"}

    monkeypatch.setattr(_up, "load", _fake_load)

    probe_locations: list[str] = []

    def _capture_probe_prompt(loc: str) -> str:
        probe_locations.append(loc)
        return ""

    monkeypatch.setattr(_td, "get_probe_prompt", _capture_probe_prompt)
    monkeypatch.setattr(_main, "_pipeline", _make_pipeline("hongcha"))

    await _main.handle_message(_MSG)

    assert probe_locations, "get_probe_prompt should have been called"
    loc = probe_locations[0]
    assert YEXUAN_SENTINEL not in loc, (
        f"yexuan profile sentinel leaked into hongcha probe context: {loc!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Regression: probe path unbroken when active is valid
# ═══════════════════════════════════════════════════════════════════════════════

async def test_probe_path_intact_for_valid_active(sandbox, monkeypatch):
    """Smoke test: handle_message reaches run_llm when active character is valid."""
    import main as _main
    import core.memory.user_profile as _up

    _patch_env(monkeypatch)
    monkeypatch.setattr(_up, "load", lambda uid, **kw: {"location": "杭州"})

    fake = _make_pipeline("yexuan")
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main.handle_message(_MSG)

    fake.run_llm.assert_called_once()
