"""
tests/test_r1d_qq_reality_reply_adapter.py — R1-D: QQ turn_sink 统一链路

Contracts for the R1-D migration that routes QQ LLM_ASSISTANT_REPLY memory writes
through turn_sink.record_assistant_turn instead of directly calling post_process.

R1-D 路径（Brief 34 §4 顺序反转后）:
  handle_message / _reply_with_tool_result
    └─ _qq_reality_reply_adapter
         ├─ record_assistant_turn(turn_sink)             (先写记忆)
         │     ├─ scrub_reality_output_text + strip_render_tags (defense-in-depth)
         │     └─ await pipeline.post_process(frozen_scope, pending_paths, ...)
         │          └─ capture_turn                     (REALITY_MEMORY authority)
         └─ strip_render_tags → text_output.send        (后发送，REALITY_VISIBLE)

保持不变:
  • Dream guard / cancel / ask_text 直发，不写 memory
  • visible strip 仍在 text_output.send 之前
  • capture_turn 仍是权威 scrub 点

顺序拍板（2026-07-08）：轮次完整性 > 投递确认。send 失败时记忆已写入，接受
"她没看到但角色记得"；不做补偿删除、不做重发队列。

Naming: D-prefix = R1-D specific guard.

Brief 50 · 工单C.4：test_r1c_qq_reality_reply_adapter.py 已合并进本文件并删除。
r1c 测的是 R1-C 阶段（adapter 直调 post_process/scrub）的旧链路，大部分已被本
文件 R1-D 阶段（adapter 经 record_assistant_turn/turn_sink）的等价或更强断言
覆盖（如 c3/c8 被 d8/d1 覆盖，c5 系列被 d7 系列覆盖，c6 被更全面的 d10 覆盖，
c2c/c2e/c2f/c4c/c9c 假设的是 adapter 直调 post_process 的旧架构，已过时）。
仍有效且未被覆盖的契约（C-prefix 保留，标注原始编号）：
  - C1/C1b：adapter 函数存在性 + module-level
  - C2d：adapter 必须 await record_assistant_turn（不能 fire-and-forget）
  - C2g：adapter 内必须有 capture_turn 权威 scrub 点注释
  - C4/C4b：调用方（handle_message / _reply_with_tool_result）→ adapter 的
    frozen_scope 转发（区别于 D4 测的是 adapter → record_assistant_turn 转发）
  - C7 系列：QQChannel.send 的 target_id/is_group 支持（r1d 原文件未覆盖 channels/qq.py）
  - C9/C9b：调用方 → adapter 的 pending_paths 转发（区别于 D4b）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

_ROOT = Path(__file__).parent.parent


# ── helpers ────────────────────────────────────────────────────────────────────

def _src(relpath: str) -> str:
    return (_ROOT / relpath).read_text(encoding="utf-8")


def _lines(relpath: str) -> list[str]:
    return _src(relpath).splitlines()


def _function_body_text(src: str, func_name: str) -> str:
    lines = src.splitlines()
    result: list[str] = []
    inside = False
    base_indent: int | None = None
    for ln in lines:
        stripped = ln.lstrip()
        indent = len(ln) - len(stripped)
        if f"def {func_name}(" in ln:
            inside = True
            base_indent = indent
            result.append(ln)
            continue
        if inside:
            if stripped and indent <= base_indent and (
                stripped.startswith("def ")
                or stripped.startswith("async def ")
                or stripped.startswith("class ")
                or stripped.startswith("@")
            ) and f"def {func_name}(" not in ln:
                break
            result.append(ln)
    return "\n".join(result)


# ═══════════════════════════════════════════════════════════════════════════════
# D1. Adapter uses record_assistant_turn (structural)
# ═══════════════════════════════════════════════════════════════════════════════

def test_d1_adapter_calls_record_assistant_turn():
    """D1: _qq_reality_reply_adapter must call record_assistant_turn (or alias _record_turn)."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "record_assistant_turn" in body or "_record_turn" in body, (
        "_qq_reality_reply_adapter: record_assistant_turn not called — "
        "R1-D turn_sink migration missing"
    )


def test_d1b_adapter_does_not_call_post_process_directly():
    """D1b: Adapter must not call _pipeline.post_process directly after R1-D."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    non_comment = [
        ln for ln in body.splitlines()
        if "_pipeline.post_process(" in ln and not ln.strip().startswith("#")
    ]
    assert not non_comment, (
        "_qq_reality_reply_adapter: _pipeline.post_process called directly — "
        "R1-D: must route through record_assistant_turn:\n"
        + "\n".join(ln.strip() for ln in non_comment)
    )


def test_d1c_adapter_does_not_call_scrub_directly():
    """D1c: Adapter must not call scrub_reality_output_text directly after R1-D."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    non_comment = [
        ln for ln in body.splitlines()
        if "scrub_reality_output_text" in ln and not ln.strip().startswith("#")
    ]
    assert not non_comment, (
        "_qq_reality_reply_adapter: scrub_reality_output_text called directly — "
        "R1-D: scrub must live in record_assistant_turn (turn_sink):\n"
        + "\n".join(ln.strip() for ln in non_comment)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D2. turn_sink signature accepts QQ-specific params
# ═══════════════════════════════════════════════════════════════════════════════

def test_d2_turn_sink_accepts_target_id():
    """D2: record_assistant_turn signature must have target_id parameter."""
    src = _src("core/turn_sink.py")
    body = _function_body_text(src, "record_assistant_turn")
    assert "target_id" in body, (
        "turn_sink.record_assistant_turn: target_id param missing — "
        "QQ group routing cannot be forwarded to post_process"
    )


def test_d2b_turn_sink_accepts_is_group():
    """D2b: record_assistant_turn signature must have is_group parameter."""
    src = _src("core/turn_sink.py")
    body = _function_body_text(src, "record_assistant_turn")
    assert "is_group" in body, (
        "turn_sink.record_assistant_turn: is_group param missing"
    )


def test_d2c_turn_sink_accepts_pending_paths():
    """D2c: record_assistant_turn signature must have pending_paths parameter."""
    src = _src("core/turn_sink.py")
    body = _function_body_text(src, "record_assistant_turn")
    assert "pending_paths" in body, (
        "turn_sink.record_assistant_turn: pending_paths param missing — "
        "pending perception confirmation will be lost"
    )


def test_d2d_turn_sink_accepts_frozen_scope():
    """D2d: record_assistant_turn signature must have frozen_scope parameter (N1)."""
    src = _src("core/turn_sink.py")
    body = _function_body_text(src, "record_assistant_turn")
    assert "frozen_scope" in body, (
        "turn_sink.record_assistant_turn: frozen_scope param missing — "
        "N1 scope freeze broken"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D3. turn_sink passes QQ params to post_process
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_post_process_block(body: str) -> str:
    """Return the text of the post_process(…) call block, spanning multiple lines."""
    lines = body.splitlines()
    result: list[str] = []
    depth = 0
    inside = False
    for ln in lines:
        if not inside and "post_process(" in ln:
            inside = True
        if inside:
            result.append(ln)
            depth += ln.count("(") - ln.count(")")
            if depth <= 0:
                break
    return "\n".join(result)


def test_d3_turn_sink_passes_frozen_scope_to_post_process():
    """D3: record_assistant_turn must forward frozen_scope to pipeline.post_process."""
    src = _src("core/turn_sink.py")
    body = _function_body_text(src, "record_assistant_turn")
    # Post_process call may span multiple lines; search the entire body after post_process(
    pp_block = _extract_post_process_block(body)
    assert "frozen_scope" in pp_block, (
        "turn_sink.record_assistant_turn: frozen_scope not passed to post_process — "
        "N1 scope freeze broken inside turn_sink"
    )


def test_d3b_turn_sink_passes_pending_paths_to_post_process():
    """D3b: record_assistant_turn must forward pending_paths to pipeline.post_process."""
    src = _src("core/turn_sink.py")
    body = _function_body_text(src, "record_assistant_turn")
    pp_block = _extract_post_process_block(body)
    assert "pending_paths" in pp_block, (
        "turn_sink.record_assistant_turn: pending_paths not passed to post_process"
    )


def test_d3c_turn_sink_passes_target_id_to_post_process():
    """D3c: record_assistant_turn must forward target_id to pipeline.post_process."""
    src = _src("core/turn_sink.py")
    body = _function_body_text(src, "record_assistant_turn")
    pp_block = _extract_post_process_block(body)
    assert "target_id" in pp_block, (
        "turn_sink.record_assistant_turn: target_id not passed to post_process — "
        "TTS/sticker side effects cannot route to correct QQ target"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D4. Adapter passes QQ params to record_assistant_turn
# ═══════════════════════════════════════════════════════════════════════════════

def test_d4_adapter_passes_frozen_scope():
    """D4: Adapter must pass frozen_scope to record_assistant_turn."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "frozen_scope=frozen_scope" in body, (
        "_qq_reality_reply_adapter: frozen_scope not forwarded to record_assistant_turn — "
        "N1 scope-freeze regression"
    )


def test_d4b_adapter_passes_pending_paths():
    """D4b: Adapter must pass pending_paths to record_assistant_turn."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "pending_paths=" in body, (
        "_qq_reality_reply_adapter: pending_paths not forwarded to record_assistant_turn"
    )


def test_d4c_adapter_passes_target_id():
    """D4c: Adapter must pass target_id to record_assistant_turn."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "target_id=target_id" in body, (
        "_qq_reality_reply_adapter: target_id not forwarded to record_assistant_turn — "
        "QQ group routing or TTS side-effects will be wrong"
    )


def test_d4d_adapter_passes_is_group():
    """D4d: Adapter must pass is_group to record_assistant_turn."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "is_group=is_group" in body, (
        "_qq_reality_reply_adapter: is_group not forwarded to record_assistant_turn"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D5. bypass_gate=True and fanout=[] in adapter call
# ═══════════════════════════════════════════════════════════════════════════════

def test_d5_adapter_uses_bypass_gate():
    """
    D5: Adapter must call record_assistant_turn with bypass_gate=True.
    The QQ adapter is already inside conversation_lock; re-entering would deadlock.
    """
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "bypass_gate=True" in body, (
        "_qq_reality_reply_adapter: bypass_gate=True missing — "
        "turn_sink would re-enter conversation_lock causing deadlock"
    )


def test_d5b_adapter_uses_empty_fanout():
    """
    D5b: Adapter must call record_assistant_turn with fanout=[] (empty list).
    Visible send is already done via text_output.send; turn_sink must not send again.
    """
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "fanout=[]" in body, (
        "_qq_reality_reply_adapter: fanout=[] missing — "
        "turn_sink would double-send the QQ message"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D6. Visible strip still happens before text_output.send
# ═══════════════════════════════════════════════════════════════════════════════

def test_d6_strip_before_send():
    """D6: strip_render_tags must still be applied before text_output.send."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    strip_pos = body.find("strip_render_tags")
    send_pos = body.find("text_output.send(")
    assert strip_pos != -1, "Adapter missing strip_render_tags"
    assert send_pos != -1, "Adapter missing text_output.send"
    assert strip_pos < send_pos, (
        "strip_render_tags appears AFTER text_output.send in adapter — "
        "visible output may contain raw render tags"
    )


def test_d6b_turn_sink_before_send():
    """D6b (Brief 34 §4): record_assistant_turn must appear before text_output.send.

    拍板 2026-07-08：轮次完整性 > 投递确认，先写记忆后发送——send 失败时记忆已写入，
    不可出现"她看到了但我忘了"。此断言方向在 Brief 34 落地时由
    test_d6b_send_before_turn_sink 反转而来。
    """
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    send_pos = body.find("text_output.send(")
    ts_pos = body.find("_record_turn(")
    if ts_pos == -1:
        ts_pos = body.find("record_assistant_turn(")
    assert send_pos != -1 and ts_pos != -1
    assert ts_pos < send_pos, (
        "record_assistant_turn appears AFTER text_output.send — "
        "memory write should precede visible delivery"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D7. Dream guard / cancel / ask_text remain direct (no memory write)
# ═══════════════════════════════════════════════════════════════════════════════

def test_d7_dream_guard_sends_direct():
    """D7: Dream guard SYSTEM_SHORT_TEXT must remain direct, not through record_assistant_turn."""
    src = _src("main.py")
    lines = src.splitlines()
    in_dg = False
    adapter_in_guard = False
    for ln in lines:
        if "_dg_result == _DGS.BLOCK_ACTIVE" in ln or "_dg_result == _DGS.BLOCK_UNCERTAIN" in ln:
            in_dg = True
        if in_dg and "return" in ln and not ln.strip().startswith("#"):
            in_dg = False
        if in_dg and ("record_assistant_turn" in ln or "_record_turn" in ln):
            adapter_in_guard = True
    assert not adapter_in_guard, (
        "Dream guard block calls record_assistant_turn — "
        "SYSTEM_SHORT_TEXT must remain direct (no memory write)"
    )


def test_d7b_cancel_confirm_is_direct_send():
    """D7b: Cancel-confirm SYSTEM_SHORT_TEXT must stay direct, not through record_assistant_turn."""
    src = _src("main.py")
    adapter_body = _function_body_text(src, "_qq_reality_reply_adapter")
    assert "已取消" not in adapter_body, (
        "Cancel confirm send ended up inside _qq_reality_reply_adapter — "
        "SYSTEM_SHORT_TEXT must stay direct"
    )


def test_d7c_ask_text_not_in_adapter():
    """D7c: TOOL_CONFIRMATION_PROMPT (ask_text) must not appear in adapter."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "ask_text" not in body, (
        "_qq_reality_reply_adapter contains 'ask_text' — "
        "TOOL_CONFIRMATION_PROMPT must remain a direct send"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D8. handle_message and _reply_with_tool_result both still call the adapter
# ═══════════════════════════════════════════════════════════════════════════════

def test_d8_handle_message_calls_adapter():
    """D8: handle_message must still call _qq_reality_reply_adapter."""
    body = _function_body_text(_src("main.py"), "handle_message")
    assert "_qq_reality_reply_adapter(" in body, (
        "handle_message: _qq_reality_reply_adapter not called"
    )


def test_d8b_tool_reply_calls_adapter():
    """D8b: _reply_with_tool_result must still call _qq_reality_reply_adapter."""
    body = _function_body_text(_src("main.py"), "_reply_with_tool_result")
    assert "_qq_reality_reply_adapter(" in body, (
        "_reply_with_tool_result: _qq_reality_reply_adapter not called"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D9. Behavioural: scrub + memory chain intact via turn_sink
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_d9_adapter_passes_raw_text_and_scrub_occurs(sandbox, monkeypatch):
    """
    D9: _qq_reality_reply_adapter must pass the raw (pre-visible-strip) segments text
    to record_assistant_turn so turn_sink can scrub action lines from memory.
    Verifies that action text present in segments is absent from post_process input.
    """
    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", AsyncMock())

    import main as _main

    from core.memory.scope import MemoryScope
    fake_pipeline = MagicMock()
    fake_pipeline.character = MagicMock()
    fake_pipeline.character.name = "TestChar"

    captured_memory: list[str] = []

    async def spy_pp(uid, content, reply, *args, **kwargs):
        captured_memory.append(reply)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake_pipeline.post_process_critical = spy_pp
    fake_pipeline.post_process_slow = AsyncMock(return_value={"turn_id": "t1", "emotion": "neutral"})
    monkeypatch.setattr(_main, "_pipeline", fake_pipeline)

    frozen_scope = MemoryScope.reality_scope("u_test", "yexuan")
    segments = ["（她低头轻轻抬起手）", "好的，我知道了。"]

    await _main._qq_reality_reply_adapter(
        segments, "u_test", "用户说了什么", "u_test", False,
        frozen_scope=frozen_scope,
        pending_paths=[],
    )

    assert captured_memory, "post_process was not called via turn_sink"
    mem = captured_memory[0]
    assert "（她低头轻轻抬起手）" not in mem, (
        f"Action line leaked into memory via adapter: {mem!r}"
    )
    assert "好的，我知道了" in mem, (
        f"Dialogue text missing from memory: {mem!r}"
    )


@pytest.mark.asyncio
async def test_d9b_adapter_visible_keeps_actions_memory_strips_pure_action_lines(sandbox, monkeypatch):
    """
    D9b: Visible QQ output keeps action description lines (strip_render_tags preserves them).
    Pure action-only lines are scrubbed from memory by scrub_reality_output_text.
    Dialogue text appears in both visible and memory.
    """
    sent_texts: list = []

    import core.output.text_output as _to
    async def spy_send(target, segs, is_group):
        sent_texts.extend(segs)
    monkeypatch.setattr(_to, "send", spy_send)

    import main as _main

    from core.memory.scope import MemoryScope
    fake_pipeline = MagicMock()
    fake_pipeline.character = MagicMock()
    fake_pipeline.character.name = "TestChar"

    captured_memory: list[str] = []

    async def spy_pp(uid, content, reply, *args, **kwargs):
        captured_memory.append(reply)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake_pipeline.post_process_critical = spy_pp
    fake_pipeline.post_process_slow = AsyncMock(return_value={"turn_id": "t1", "emotion": "neutral"})
    monkeypatch.setattr(_main, "_pipeline", fake_pipeline)

    frozen_scope = MemoryScope.reality_scope("u_test2", "yexuan")
    # Two segments: pure action line (should be scrubbed from memory) + dialogue
    segments = ["（她抬起头，轻轻点了点）", "好的，没问题。"]

    await _main._qq_reality_reply_adapter(
        segments, "u_test2", "测试", "u_test2", False,
        frozen_scope=frozen_scope,
        pending_paths=[],
    )

    # Visible: action description stays (strip_render_tags only removes <say> etc)
    assert sent_texts, "text_output.send was not called"
    visible = " ".join(sent_texts)
    assert "（她抬起头，轻轻点了点）" in visible, (
        f"Action description missing from visible output: {visible!r}"
    )
    assert "好的，没问题" in visible, (
        f"Dialogue text missing from visible output: {visible!r}"
    )

    # Memory: pure action line scrubbed; dialogue remains
    assert captured_memory, "post_process was not called"
    mem = captured_memory[0]
    assert "（她抬起头，轻轻点了点）" not in mem, (
        f"Action line leaked into memory: {mem!r}"
    )
    assert "好的，没问题" in mem, (
        f"Dialogue text missing from memory: {mem!r}"
    )


@pytest.mark.asyncio
async def test_d9c_adapter_group_routing_preserved(sandbox, monkeypatch):
    """
    D9c: Group chat target_id and is_group=True must be forwarded to both
    text_output.send and post_process (via record_assistant_turn).
    """
    send_calls: list = []

    import core.output.text_output as _to
    async def spy_send(target, segs, is_group):
        send_calls.append((target, is_group))
    monkeypatch.setattr(_to, "send", spy_send)

    import main as _main

    from core.memory.scope import MemoryScope
    fake_pipeline = MagicMock()
    fake_pipeline.character = MagicMock()
    fake_pipeline.character.name = "TestChar"

    pp_kwargs: list[dict] = []

    async def spy_pp(uid, content, reply, *args, **kwargs):
        pp_kwargs.append(kwargs)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake_pipeline.post_process_critical = spy_pp
    fake_pipeline.post_process_slow = AsyncMock(return_value={"turn_id": "t1", "emotion": "neutral"})
    monkeypatch.setattr(_main, "_pipeline", fake_pipeline)

    frozen_scope = MemoryScope.reality_scope("u_grp", "yexuan")

    await _main._qq_reality_reply_adapter(
        ["群里好啊！"], "u_grp", "hi", "group_123", True,
        frozen_scope=frozen_scope,
        pending_paths=[],
    )

    # Visible: group routing correct
    assert send_calls, "text_output.send not called"
    assert send_calls[0] == ("group_123", True), (
        f"text_output.send called with wrong target/is_group: {send_calls[0]}"
    )

    # Memory: is_group forwarded to post_process
    assert pp_kwargs, "post_process not called"
    assert pp_kwargs[0].get("is_group") is True, (
        f"is_group not forwarded to post_process: {pp_kwargs[0]}"
    )
    assert pp_kwargs[0].get("target_id") == "group_123", (
        f"target_id not forwarded to post_process: {pp_kwargs[0]}"
    )


@pytest.mark.asyncio
async def test_d9d_frozen_scope_forwarded_to_post_process(sandbox, monkeypatch):
    """
    D9d: frozen_scope must reach pipeline.post_process via record_assistant_turn (N1).
    """
    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", AsyncMock())

    import main as _main

    from core.memory.scope import MemoryScope
    fake_pipeline = MagicMock()
    fake_pipeline.character = MagicMock()
    fake_pipeline.character.name = "TestChar"

    pp_kwargs: list[dict] = []

    async def spy_pp(uid, content, reply, *args, **kwargs):
        pp_kwargs.append(kwargs)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake_pipeline.post_process_critical = spy_pp
    fake_pipeline.post_process_slow = AsyncMock(return_value={"turn_id": "t1", "emotion": "neutral"})
    monkeypatch.setattr(_main, "_pipeline", fake_pipeline)

    frozen_scope = MemoryScope.reality_scope("u_scope", "yexuan")

    await _main._qq_reality_reply_adapter(
        ["こんにちは。"], "u_scope", "hi", "u_scope", False,
        frozen_scope=frozen_scope,
        pending_paths=["p1", "p2"],
    )

    assert pp_kwargs, "post_process not called"
    assert pp_kwargs[0].get("frozen_scope") is frozen_scope, (
        "frozen_scope not forwarded to post_process — N1 scope-freeze broken"
    )
    assert pp_kwargs[0].get("pending_paths") == ["p1", "p2"], (
        "pending_paths not forwarded to post_process"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D10. main.py has no bare _pipeline.post_process calls
# ═══════════════════════════════════════════════════════════════════════════════

def test_d10_main_no_direct_post_process():
    """D10: main.py must contain zero direct _pipeline.post_process calls after R1-D."""
    src = _src("main.py")
    violations = [
        f"line {i+1}: {ln.strip()}"
        for i, ln in enumerate(src.splitlines())
        if "_pipeline.post_process(" in ln and not ln.strip().startswith("#")
    ]
    assert not violations, (
        "main.py: direct _pipeline.post_process calls found — "
        "R1-D: all memory writes must route through record_assistant_turn:\n"
        + "\n".join(violations)
    )


def test_d10b_main_no_direct_scrub():
    """D10b: main.py must contain zero direct scrub_reality_output_text calls after R1-D."""
    src = _src("main.py")
    violations = [
        f"line {i+1}: {ln.strip()}"
        for i, ln in enumerate(src.splitlines())
        if "scrub_reality_output_text" in ln and not ln.strip().startswith("#")
    ]
    assert not violations, (
        "main.py: direct scrub_reality_output_text calls found — "
        "R1-D: scrub must live in turn_sink (defense-in-depth) and capture_turn (authority):\n"
        + "\n".join(violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C1. Adapter function exists in main.py（merged from test_r1c，Brief 50 · 工单C.4）
# ═══════════════════════════════════════════════════════════════════════════════

def test_c1_adapter_exists_in_main():
    """C1: _qq_reality_reply_adapter must be defined as an async function in main.py."""
    src = _src("main.py")
    assert "async def _qq_reality_reply_adapter(" in src, (
        "main.py: _qq_reality_reply_adapter not found — R1-C adapter was removed or renamed"
    )


def test_c1b_adapter_is_module_level():
    """C1b: _qq_reality_reply_adapter must be a module-level function (indented 0)."""
    for ln in _lines("main.py"):
        if "async def _qq_reality_reply_adapter(" in ln:
            assert not ln[0].isspace(), (
                "_qq_reality_reply_adapter is indented — must be a module-level function"
            )
            break
    else:
        raise AssertionError("_qq_reality_reply_adapter definition not found in main.py")


# ═══════════════════════════════════════════════════════════════════════════════
# C2d/C2g. Adapter await + capture_turn comment（merged from test_r1c）
# ═══════════════════════════════════════════════════════════════════════════════

def test_c2d_adapter_awaits_turn_sink():
    """
    C2d (R1-D): Adapter must await record_assistant_turn (N10: critical writes must not drop).
    """
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "await _record_turn(" in body or "await record_assistant_turn(" in body, (
        "_qq_reality_reply_adapter: record_assistant_turn not awaited — "
        "N10 regression (memory writes may be dropped)"
    )


def test_c2g_adapter_has_capture_turn_comment():
    """
    C2g: Adapter body must contain a comment referencing capture_turn, documenting
    that capture_turn is the authority scrub point (R6-B C10 pattern).
    """
    lines = _lines("main.py")
    in_adapter = False
    found = False
    for ln in lines:
        if "async def _qq_reality_reply_adapter(" in ln:
            in_adapter = True
        if in_adapter and ln.startswith("async def ") and "_qq_reality_reply_adapter" not in ln:
            break
        if in_adapter and ln.startswith("def ") and "_qq_reality_reply_adapter" not in ln:
            break
        if in_adapter and "capture_turn" in ln and ln.strip().startswith("#"):
            found = True

    assert found, (
        "_qq_reality_reply_adapter: no comment mentioning capture_turn — "
        "add a note that capture_turn is the REALITY_MEMORY authority scrub point"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C4/C4b. Caller → adapter frozen_scope forwarding（merged from test_r1c；
# 区别于上面 D4：D4 测的是 adapter → record_assistant_turn 的转发）
# ═══════════════════════════════════════════════════════════════════════════════

def test_c4_handle_message_passes_frozen_scope():
    """C4: handle_message must pass frozen_scope=_frozen_scope to the adapter (N1)."""
    src = _src("main.py")
    hm_body = _function_body_text(src, "handle_message")
    assert "frozen_scope=_frozen_scope" in hm_body, (
        "handle_message: frozen_scope=_frozen_scope not passed to adapter — "
        "N1 scope-freeze regression"
    )


def test_c4b_tool_reply_passes_frozen_scope():
    """C4b: _reply_with_tool_result must pass frozen_scope=frozen_scope to the adapter."""
    src = _src("main.py")
    tr_body = _function_body_text(src, "_reply_with_tool_result")
    assert "frozen_scope=frozen_scope" in tr_body, (
        "_reply_with_tool_result: frozen_scope not forwarded to adapter — "
        "N1 scope-freeze regression"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C7. QQChannel.send group support（merged from test_r1c；r1d 原文件未覆盖 channels/qq.py）
# ═══════════════════════════════════════════════════════════════════════════════

def test_c7_qq_channel_send_accepts_target_id():
    """C7: QQChannel.send must accept an optional target_id parameter."""
    src = _src("channels/qq.py")
    send_body = _function_body_text(src, "send")
    assert "target_id" in send_body, (
        "QQChannel.send: target_id parameter missing — R1-C prerequisite not met"
    )


def test_c7b_qq_channel_send_accepts_is_group():
    """C7b: QQChannel.send must accept an optional is_group parameter."""
    src = _src("channels/qq.py")
    send_body = _function_body_text(src, "send")
    assert "is_group" in send_body, (
        "QQChannel.send: is_group parameter missing"
    )


def test_c7c_qq_channel_no_hardcoded_group_false():
    """C7c: QQChannel.send must not hardcode is_group=False in the send_message call."""
    src = _src("channels/qq.py")
    send_body = _function_body_text(src, "send")
    assert "is_group=False" not in send_body, (
        "QQChannel.send: is_group=False hardcode remains — "
        "group routing would be silently broken when called with is_group=True"
    )


def test_c7d_qq_channel_passes_is_group_to_adapter():
    """C7d: QQChannel.send must forward the is_group variable (via text_output.send).

    FIX-08: QQChannel.send now routes through text_output.send for segmented delivery;
    is_group is forwarded there rather than directly to qq_adapter.send_message.
    """
    src = _src("channels/qq.py")
    send_body = _function_body_text(src, "send")
    found_forwarding_call = False
    for ln in send_body.splitlines():
        stripped = ln.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
            continue
        if ("text_output.send(" in ln or "send_message(" in ln) and stripped.startswith("await"):
            found_forwarding_call = True
            assert "is_group" in ln, (
                f"QQChannel.send: forwarding call does not pass is_group: {stripped}"
            )
    assert found_forwarding_call, (
        "QQChannel.send: no `await text_output.send(` or `await send_message(` call found"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C9/C9b. Caller → adapter pending_paths forwarding（merged from test_r1c；
# 区别于上面 D4b：D4b 测的是 adapter → record_assistant_turn 的转发）
# ═══════════════════════════════════════════════════════════════════════════════

def test_c9_handle_message_passes_pending_paths():
    """C9: handle_message must pass pending_paths to the adapter."""
    src = _src("main.py")
    hm_body = _function_body_text(src, "handle_message")
    assert "pending_paths=" in hm_body, (
        "handle_message: pending_paths not passed to adapter — "
        "pending_paths context would be lost"
    )


def test_c9b_tool_reply_passes_pending_paths():
    """C9b: _reply_with_tool_result must also pass pending_paths to the adapter."""
    src = _src("main.py")
    tr_body = _function_body_text(src, "_reply_with_tool_result")
    assert "pending_paths=" in tr_body, (
        "_reply_with_tool_result: pending_paths not passed to adapter"
    )
