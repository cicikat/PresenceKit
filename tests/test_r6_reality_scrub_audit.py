"""
tests/test_r6_reality_scrub_audit.py — R6-A: Reality output scrub boundary audit

Static + behavioral guards that pin the current scrub boundary so that:
 1. Every REALITY_VISIBLE exit applies strip_render_tags (no markup tags sent to users).
 2. Every REALITY_MEMORY write applies scrub_reality_output_text (no action/narration in history).
 3. DREAM_VISIBLE exits are isolated — reality scrub functions are never applied to dream output.
 4. Outlet allowlist documents every text_output.send in main.py.

Exit classification used in this test suite:
  REALITY_VISIBLE  — user-facing reality reply       → strip_render_tags only
  REALITY_MEMORY   — short_term / event_log write    → scrub_reality_output_text
  DREAM_VISIBLE    — dream turn reply                → neither reality function applied
  SYSTEM_MSG       — hardcoded bot text (not LLM)   → no scrub needed

Scrub ownership map (as of R6-A):
  main.py handle_message         → pre-scrubs memory_reply before post_process  (S1a)
  main.py _reply_with_tool_result → pre-scrubs memory_reply before post_process  (S1b)
  core/turn_sink record_assistant_turn → pre-scrubs memory_text before post_process (S1c)
  core/memory/fixation_pipeline capture_turn → defense-in-depth scrub at write point (S1d)

Double-scrub note: paths through turn_sink or main.py pre-scrub text, then capture_turn
scrubs again.  This is intentional defense-in-depth and is idempotent (verified in B4).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = Path(__file__).parent.parent


# ── static helpers ─────────────────────────────────────────────────────────────

def _source(relpath: str) -> str:
    return (_ROOT / relpath).read_text(encoding="utf-8")


def _grep_lines(relpath: str, symbol: str) -> list[int]:
    """Return 1-based line numbers where symbol appears."""
    lines = (_ROOT / relpath).read_text(encoding="utf-8").splitlines()
    return [i + 1 for i, ln in enumerate(lines) if symbol in ln]


# ── behavioral helpers ─────────────────────────────────────────────────────────

def _patch_noise(monkeypatch, owner_id: str = "77777"):
    import core.config_loader as _cl
    monkeypatch.setattr(_cl, "get_config", lambda: {
        "scheduler": {"owner_id": owner_id},
        "llm": {"tool_call_mode": "function_calling"},
    })
    for mod_path, attr, stub in [
        ("core.scheduler.loop",          "mark_user_active",     lambda: None),
        ("core.presence",                "update_last_message",  lambda uid: None),
        ("core.scheduler.state_machine", "notify_owner_turn",    lambda uid: None),
    ]:
        try:
            import importlib
            m = importlib.import_module(mod_path)
            monkeypatch.setattr(m, attr, stub)
        except Exception:
            pass


def _patch_memory(monkeypatch):
    import core.memory.short_term as _st
    import core.memory.user_profile as _up
    import core.memory.group_context as _gc
    import core.user_relation as _ur
    monkeypatch.setattr(_st, "load_for_prompt", lambda uid, **kw: [])
    monkeypatch.setattr(_up, "load", lambda uid, **kw: {"location": "杭州"})
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_gc, "append", lambda *a, **kw: None)
    monkeypatch.setattr(_ur, "get_relation", lambda uid: {})


def _patch_output(monkeypatch) -> list:
    sent: list[list[str]] = []
    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", AsyncMock(
        side_effect=lambda tgt, segs, grp: sent.append(list(segs))
    ))
    return sent


def _patch_probe(monkeypatch):
    import core.tool_dispatcher as _td
    _td._TOOL_REGISTRY = {}
    monkeypatch.setattr(_td, "get_probe_prompt", lambda loc: "")
    monkeypatch.setattr(_td, "get_tools_schema", lambda categories=None: [])
    import core.llm_client as _llm
    monkeypatch.setattr(_llm, "chat", AsyncMock(return_value=""))
    monkeypatch.setattr(_llm, "parse_tool_call_response", lambda r: [])


def _make_pipeline(llm_reply: str = "回复", char_id: str = "yexuan"):
    from core.memory.scope import MemoryScope
    fake = MagicMock()
    fake.character = MagicMock()
    fake.character.name = "TestChar"
    fake.author_note_extra = ""
    fake._active_character_id = char_id
    fake._current_reality_scope = MagicMock(
        return_value=MemoryScope.reality_scope("77777", char_id)
    )
    fake.fetch_context = AsyncMock(return_value={})
    fake.build_prompt = MagicMock(return_value=([], {"pending_paths": []}))
    fake.run_llm = AsyncMock(return_value=llm_reply)
    fake.post_process = AsyncMock(
        return_value={"turn_id": "t1", "critical_written": True, "emotion": "neutral"}
    )
    return fake


_OWNER_ID = "77777"


# ═══════════════════════════════════════════════════════════════════════════════
# S1. Static inventory — scrub_reality_output_text call sites
# ═══════════════════════════════════════════════════════════════════════════════

def test_s1a_scrub_call_site_main_handle_message():
    """
    S1a (R1-D updated): main.py no longer calls scrub_reality_output_text directly.
    After R1-D the scrub lives inside record_assistant_turn → pipeline.post_process →
    capture_turn.  The adapter must call record_assistant_turn (turn_sink chain) and
    must NOT call scrub or post_process directly.
    """
    src = _source("main.py")
    # R1-D: scrub authority moved into turn_sink; no direct post_process call in adapter
    assert "await _pipeline.post_process(" not in src, (
        "main.py: direct await _pipeline.post_process found — "
        "R1-D turn_sink migration regression; adapter must route through record_assistant_turn"
    )
    # R1-D: adapter must call record_assistant_turn (via import alias _record_turn)
    assert "_record_turn(" in src or "record_assistant_turn(" in src, (
        "main.py: record_assistant_turn/_record_turn call missing — "
        "R1-D turn_sink chain not wired"
    )


def test_s1b_scrub_call_site_main_tool_reply():
    """
    S1b (R1-C updated): main.py _reply_with_tool_result routes to
    _qq_reality_reply_adapter, which calls scrub_reality_output_text.

    After R1-C the scrub lives in the adapter rather than directly in
    _reply_with_tool_result; the R6 contract is still satisfied.
    """
    src = _source("main.py")
    assert "_reply_with_tool_result" in src
    # _reply_with_tool_result must call the adapter
    lines = src.splitlines()
    in_func = False
    found_adapter = False
    for ln in lines:
        if "def _reply_with_tool_result(" in ln:
            in_func = True
        if in_func and "_qq_reality_reply_adapter(" in ln:
            found_adapter = True
            break
        if in_func and ln.startswith("async def ") and "_reply_with_tool_result" not in ln:
            break
    assert found_adapter, (
        "main.py _reply_with_tool_result: _qq_reality_reply_adapter not called — "
        "tool-reply memory path may leak action lines (R6-A/B regression)"
    )
    # The adapter itself must call scrub
    in_adapter = False
    found_scrub = False
    for ln in lines:
        if "def _qq_reality_reply_adapter(" in ln:
            in_adapter = True
        if in_adapter and "scrub_reality_output_text" in ln:
            found_scrub = True
            break
        if in_adapter and ln.startswith("async def ") and "_qq_reality_reply_adapter" not in ln:
            break
    assert found_scrub, (
        "main.py _qq_reality_reply_adapter: scrub_reality_output_text not called — "
        "tool-reply memory path may leak action lines (R6-A/B regression)"
    )


def test_s1c_scrub_call_site_turn_sink():
    """S1c: turn_sink.record_assistant_turn must call scrub for memory_text."""
    hits = _grep_lines("core/turn_sink.py", "scrub_reality_output_text")
    assert hits, (
        "core/turn_sink.py: scrub_reality_output_text not found — "
        "desktop/scheduler/sensor/wake memory paths unprotected"
    )
    # scrub must be applied to produce memory_text (the post_process arg)
    src = _source("core/turn_sink.py")
    assert "memory_text" in src
    assert "_scrub" in src


def test_s1d_scrub_call_site_capture_turn_defense_in_depth():
    """S1d: capture_turn must call scrub as defense-in-depth at the actual write point."""
    src = _source("core/memory/fixation_pipeline.py")
    lines = src.splitlines()
    in_capture = False
    found = False
    for ln in lines:
        if "def capture_turn(" in ln:
            in_capture = True
        if in_capture and "scrub_reality_output_text" in ln:
            found = True
            break
        if in_capture and ln.startswith("def ") and "capture_turn" not in ln:
            break
    assert found, (
        "capture_turn does not call scrub_reality_output_text — "
        "the defense-in-depth write guard is missing"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# S2. Static inventory — strip_render_tags call sites
# ═══════════════════════════════════════════════════════════════════════════════

def test_s2a_strip_tags_in_main_qq_visible():
    """S2a: main.py must call strip_render_tags before visible QQ send."""
    src = _source("main.py")
    assert "strip_render_tags" in src, (
        "main.py: strip_render_tags not found — render tags may leak to QQ"
    )
    # Segments (visible output path) must be processed
    assert "segments" in src


def test_s2b_strip_tags_in_turn_sink_fanout():
    """S2b: turn_sink._fanout must strip render tags for all REALITY_VISIBLE channel sends."""
    hits = _grep_lines("core/turn_sink.py", "strip_render_tags")
    assert hits, (
        "core/turn_sink.py: strip_render_tags not found in _fanout — "
        "render tags may leak to desktop/mobile channels"
    )
    src = _source("core/turn_sink.py")
    # Visible text variable must exist
    assert "_visible_text" in src


def test_s2c_strip_tags_in_desktop_chat_visible():
    """S2c: admin/routers/chat.py desktop_chat must strip render tags for visible reply."""
    hits = _grep_lines("admin/routers/chat.py", "strip_render_tags")
    assert hits, (
        "admin/routers/chat.py: strip_render_tags not found — "
        "desktop chat visible reply may leak render tags"
    )


def test_s2d_strip_tags_in_desktop_wake_visible():
    """S2d: admin/routers/chat.py desktop_wake Path B must strip render tags for visible reply."""
    src = _source("admin/routers/chat.py")
    assert "live_wake" in src, "desktop_wake live path not found in chat.py"
    assert "strip_render_tags" in src, (
        "admin/routers/chat.py: strip_render_tags not applied to wake visible reply"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# S3. Dream isolation — static checks
# ═══════════════════════════════════════════════════════════════════════════════

def test_s3a_dream_pipeline_no_reality_scrubber():
    """S3a: dream_pipeline.py must never import scrub_reality_output_text."""
    src = _source("core/dream/dream_pipeline.py")
    assert "scrub_reality_output_text" not in src, (
        "dream_pipeline.py imports scrub_reality_output_text — "
        "dream output must NOT be processed by the reality scrubber"
    )
    assert "reality_output_scrubber" not in src, (
        "dream_pipeline.py imports reality_output_scrubber module"
    )


def test_s3b_dream_pipeline_no_strip_render_tags():
    """S3b: dream_pipeline.py must not apply strip_render_tags to dream output."""
    src = _source("core/dream/dream_pipeline.py")
    assert "strip_render_tags" not in src, (
        "dream_pipeline.py imports strip_render_tags — dream has its own output path"
    )


def test_s3c_dream_router_no_reality_scrub():
    """S3c: admin/routers/dream.py must not apply reality scrub functions."""
    src = _source("admin/routers/dream.py")
    assert "scrub_reality_output_text" not in src, (
        "admin/routers/dream.py calls scrub_reality_output_text — "
        "dream reply should bypass reality scrub"
    )
    assert "strip_render_tags" not in src, (
        "admin/routers/dream.py calls strip_render_tags"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# S4. Outlet allowlist — every text_output.send in main.py is accounted for
# ═══════════════════════════════════════════════════════════════════════════════

def test_s4_text_output_send_allowlist():
    """
    S4: Every text_output.send call in main.py is either:
      (a) A hardcoded SYSTEM_MSG (not LLM output — no scrub needed), or
      (b) A LLM-generated reply whose segments already had strip_render_tags applied.
    Fails if a new send site is added that doesn't match a known pattern.
    """
    lines = (_ROOT / "main.py").read_text(encoding="utf-8").splitlines()
    # Match only actual call sites (with open paren) — comments/docstrings say "text_output.send"
    # without parens and must not be treated as call sites.
    send_linenos = [i + 1 for i, ln in enumerate(lines) if "text_output.send(" in ln]
    assert send_linenos, "No text_output.send( found in main.py — file may have changed"

    # Patterns that are either SYSTEM_MSG (hardcoded strings) or LLM segments already
    # processed through strip_render_tags.
    covered_patterns = [
        # SYSTEM_MSG — hardcoded, not LLM output
        '["好的，已取消～"]',
        "[ask_text]",
        # LLM segments — strip_render_tags applied to 'segments' earlier in the function
        # (R1-B baseline: called with `segments` variable)
        "target_id, segments, is_group",
        # R1-C: adapter uses `clean` (result of strip_render_tags applied to segments)
        "target_id, clean, is_group",
    ]

    uncovered = []
    for lineno in send_linenos:
        line = lines[lineno - 1]
        if not any(pat in line for pat in covered_patterns):
            uncovered.append(f"main.py:{lineno}: {line.strip()}")

    assert not uncovered, (
        "New text_output.send call(s) not in R6 allowlist — "
        "verify strip_render_tags is applied before send:\n"
        + "\n".join(uncovered)
    )


def test_s4_no_registry_broadcast_from_reality_core():
    """
    S4b: Core reality pipeline files must not call channels.registry.broadcast directly,
    which would bypass turn_sink._fanout's strip_render_tags guard.
    """
    reality_files = [
        "core/pipeline.py",
        "main.py",
        "admin/routers/chat.py",
        "core/scheduler/loop.py",
    ]
    violations = []
    for relpath in reality_files:
        src = _source(relpath)
        if "registry.broadcast" in src or "registry.fanout" in src:
            violations.append(relpath)

    assert not violations, (
        "Direct registry.broadcast/fanout found in reality pipeline files — "
        "visible output must go through turn_sink._fanout (which applies strip_render_tags):\n"
        + "\n".join(violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# B1. Behavioral: QQ main path — memory reply has action lines scrubbed
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_b1_qq_main_memory_reply_scrubbed(sandbox, monkeypatch):
    """B1: handle_message must pass action-scrubbed text to post_process (not raw LLM output)."""
    from core.dream.dream_state import DreamStatus, write_state
    write_state(_OWNER_ID, {"status": DreamStatus.REALITY_CHAT.value, "user_id": _OWNER_ID})

    _patch_noise(monkeypatch)
    _patch_memory(monkeypatch)
    _patch_output(monkeypatch)
    _patch_probe(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    import main as _main
    raw = "（轻轻抬起头）\n你好，我在。"
    fake = _make_pipeline(llm_reply=raw)
    captured: list[str] = []

    async def spy_post_process(uid, content, reply, *args, **kwargs):
        captured.append(reply)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake.post_process = spy_post_process
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main.handle_message({
        "user_id": _OWNER_ID,
        "content": "你好",
        "sender_name": _OWNER_ID,
    })

    assert captured, "post_process was not called"
    mem = captured[0]
    assert "（轻轻抬起头）" not in mem, f"CJK bracket action leaked into memory: {mem!r}"
    assert "你好，我在" in mem, f"Dialogue text missing from memory: {mem!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# B2. Behavioral: QQ main path — visible output has render tags stripped
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_b2_qq_main_visible_strips_render_tags(sandbox, monkeypatch):
    """B2: handle_message must strip <say> tags from QQ visible output."""
    from core.dream.dream_state import DreamStatus, write_state
    write_state(_OWNER_ID, {"status": DreamStatus.REALITY_CHAT.value, "user_id": _OWNER_ID})

    _patch_noise(monkeypatch)
    _patch_memory(monkeypatch)
    sent = _patch_output(monkeypatch)
    _patch_probe(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    import main as _main
    fake = _make_pipeline(llm_reply="<say>你好世界</say>")
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main.handle_message({
        "user_id": _OWNER_ID,
        "content": "你好",
        "sender_name": _OWNER_ID,
    })

    assert sent, "QQ visible output was not sent"
    all_text = " ".join(seg for segs in sent for seg in segs)
    assert "<say>" not in all_text, f"Render tag leaked into QQ visible output: {all_text!r}"
    assert "你好世界" in all_text, f"Content missing from visible output: {all_text!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# B3. Behavioral: _reply_with_tool_result — memory scrub
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_b3_tool_reply_memory_scrubbed(sandbox, monkeypatch):
    """B3: _reply_with_tool_result must pass action-scrubbed text to post_process."""
    import main as _main

    raw = "*她低头看了你一眼*\n已经帮你搞定了。"
    fake = _make_pipeline(llm_reply=raw)
    _patch_memory(monkeypatch)
    _patch_output(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    captured: list[str] = []

    async def spy_post_process(uid, content, reply, *args, **kwargs):
        captured.append(reply)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake.post_process = spy_post_process
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)

    assert captured, "post_process was not called"
    mem = captured[0]
    assert "*她低头看了你一眼*" not in mem, f"Action line leaked into tool-reply memory: {mem!r}"
    assert "已经帮你搞定了" in mem, f"Dialogue missing from tool-reply memory: {mem!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# B4. Behavioral: turn_sink.record_assistant_turn — memory_text is scrubbed
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_b4_turn_sink_memory_text_scrubbed(sandbox, monkeypatch):
    """B4: record_assistant_turn must deliver action-scrubbed memory_text to post_process."""
    import core.turn_sink as _ts
    from core.turn_sink import TurnSource
    import core.pipeline_registry as _pr

    captured: list[str] = []

    fake_pipeline = MagicMock()

    async def spy_post_process(uid, user_msg, reply, **kw):
        captured.append(reply)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake_pipeline.post_process = spy_post_process
    monkeypatch.setattr(_pr, "get", lambda: fake_pipeline)

    # Stub fanout and desktop_ws to avoid side effects
    monkeypatch.setattr(_ts, "_fanout", AsyncMock(return_value=([], {})))
    import channels.desktop_ws as _dws
    monkeypatch.setattr(_dws, "is_connected", lambda: False)

    from core.write_envelope import stamp_user_chat
    raw_text = "好的。\n（他微微颔首，眼神轻柔）\n明白了。"
    await _ts.record_assistant_turn(
        assistant_text=raw_text,
        uid="test_uid",
        source=TurnSource.USER_CHAT,
        user_text="请回答",
        fanout=[],
        bypass_gate=True,
        pipeline=fake_pipeline,
        envelope=stamp_user_chat(),
    )

    assert captured, "post_process was not called"
    mem = captured[0]
    assert "（他微微颔首" not in mem, f"CJK bracket action leaked into turn_sink memory: {mem!r}"
    assert "好的" in mem or "明白了" in mem, f"Dialogue missing from memory: {mem!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# B5. Behavioral: turn_sink._fanout — visible text uses strip_render_tags
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_b5_fanout_visible_strips_render_tags(monkeypatch):
    """B5: _fanout must strip <say> tags from visible channel output but preserve action text."""
    import core.turn_sink as _ts
    import channels.registry as _reg

    sent_texts: list[str] = []

    class FakeChannel:
        name = "desktop"
        is_active = True

        async def send(self, text, uid, **kw):
            sent_texts.append(text)

    monkeypatch.setattr(_reg, "get_active", lambda: [FakeChannel()])

    raw = "<say>你好。</say>（他轻轻点头）"
    await _ts._fanout(
        assistant_text=raw,
        uid="u1",
        fanout="all",
        behavior=None,
    )

    assert sent_texts, "channel.send was not called"
    txt = sent_texts[0]
    assert "<say>" not in txt, f"Render tag leaked into channel visible output: {txt!r}"
    # Action descriptions survive in REALITY_VISIBLE (scrub is only for memory path)
    assert "（他轻轻点头）" in txt, f"Action description was wrongly removed from visible: {txt!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# B6. Behavioral: capture_turn defense-in-depth scrub
# ═══════════════════════════════════════════════════════════════════════════════

def test_b6_capture_turn_scrubs_independently(sandbox):
    """B6: capture_turn must scrub action lines even if the caller did not pre-scrub."""
    from core.memory.fixation_pipeline import capture_turn
    from core.memory import short_term as _st, event_log as _el
    from core.write_envelope import WriteEnvelope, SourceType

    _env = WriteEnvelope(source=SourceType.QQ, can_write_memory=True)

    # Raw reply with bracket action — caller deliberately did NOT pre-scrub
    raw_reply = "哦？\n（她抬起头，眼睛微微睁大）\n好的，知道了。"
    capture_turn(
        "test_uid_b6",
        "用户说了什么",
        raw_reply,
        "neutral",
        turn_id="r6_b6_t1",
        envelope=_env,
        char_id="yexuan",
    )

    history = _st.load("test_uid_b6", char_id="yexuan")
    assistant_msgs = [m for m in history if m.get("role") == "assistant"]
    assert assistant_msgs, "capture_turn must write assistant message to short_term"
    written = assistant_msgs[-1].get("content", "")
    assert "（她抬起头" not in written, f"Action line leaked via capture_turn: {written!r}"
    assert "好的，知道了" in written, f"Dialogue missing after capture_turn scrub: {written!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# B7. Behavioral: double-scrub is idempotent
# ═══════════════════════════════════════════════════════════════════════════════

def test_b7_double_scrub_idempotent():
    """
    B7: Applying scrub_reality_output_text twice must produce the same result.
    Verifies that the double-scrub pattern (main.py pre-scrub + capture_turn scrub) is safe.
    """
    from core.reality_output_scrubber import scrub_reality_output_text as _scrub

    samples = [
        "你好，我在。\n（她轻轻颔首）\n明白了。",
        "普通对话文本，没有动作。",
        "*抬起头*\n很好。",
        "好好好，不欺负了。\n（在你发顶落下一个很轻的吻）\n睡吧。",
        "她低头看着你。\n我懂了。",
        "只有这一行对话。",
    ]
    for text in samples:
        once = _scrub(text)
        twice = _scrub(once) if once is not None else None
        assert once == twice, (
            f"scrub_reality_output_text is NOT idempotent:\n"
            f"  input:       {text!r}\n"
            f"  first pass:  {once!r}\n"
            f"  second pass: {twice!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# B8. Behavioral: REALITY_VISIBLE must not apply scrub (action text survives for users)
# ═══════════════════════════════════════════════════════════════════════════════

def test_b8_visible_output_preserves_action_descriptions():
    """
    B8: REALITY_VISIBLE path (strip_render_tags only) must preserve action descriptions
    so that chat texture survives to the user.  Only REALITY_MEMORY strips action lines.
    """
    from core.response_processor import strip_render_tags

    raw = "<say>好好好，不欺负了。</say>\n（在你发顶落下一个很轻的吻）\n睡吧。"
    visible = strip_render_tags(raw)

    # Render tags stripped
    assert "<say>" not in visible, "strip_render_tags left <say> tag in visible output"
    assert "</say>" not in visible, "strip_render_tags left </say> tag in visible output"
    # Action description preserved for visible output
    assert "（在你发顶落下一个很轻的吻）" in visible, (
        "strip_render_tags wrongly removed action description from visible output"
    )
    # Dialogue preserved
    assert "好好好，不欺负了" in visible


# ═══════════════════════════════════════════════════════════════════════════════
# S5. Ownership contract — record_assistant_turn is the canonical desktop/scheduler path
# ═══════════════════════════════════════════════════════════════════════════════

def test_s5a_desktop_chat_uses_record_assistant_turn():
    """S5a: admin/routers/chat.py desktop_chat must go through record_assistant_turn."""
    src = _source("admin/routers/chat.py")
    assert "record_assistant_turn" in src, (
        "admin/routers/chat.py: record_assistant_turn not called — "
        "desktop memory write may bypass turn_sink scrub"
    )


def test_s5b_scheduler_uses_record_assistant_turn():
    """S5b: core/scheduler/loop.py _pipeline_send must use record_assistant_turn."""
    src = _source("core/scheduler/loop.py")
    assert "record_assistant_turn" in src, (
        "core/scheduler/loop.py: record_assistant_turn not called — "
        "scheduler trigger memory write may bypass turn_sink scrub"
    )


def test_s5c_sensor_aware_uses_record_assistant_turn():
    """S5c: sensor_aware must use record_assistant_turn for memory + fanout."""
    src = _source("core/scheduler/triggers/sensor_aware.py")
    assert "record_assistant_turn" in src, (
        "sensor_aware.py: record_assistant_turn not called — "
        "sensor reply memory write may bypass turn_sink scrub"
    )


def test_s5d_desktop_wake_uses_record_assistant_turn():
    """S5d: desktop_wake Path B must use record_assistant_turn."""
    src = _source("admin/routers/chat.py")
    lines = src.splitlines()
    # Find the desktop_wake function region and check record_assistant_turn is called
    in_wake = False
    found = False
    for ln in lines:
        if "async def desktop_wake(" in ln or "def desktop_wake(" in ln:
            in_wake = True
        if in_wake and "record_assistant_turn" in ln:
            found = True
            break
        if in_wake and ln.startswith("@router") and "wake" not in ln:
            break
    assert found, (
        "desktop_wake Path B does not call record_assistant_turn — "
        "wake reply memory write bypasses turn_sink scrub"
    )
