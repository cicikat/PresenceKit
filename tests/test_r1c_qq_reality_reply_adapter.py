"""
tests/test_r1c_qq_reality_reply_adapter.py — R1-C/D: QQ Reality Reply Adapter

Contracts for the _qq_reality_reply_adapter introduced in R1-C and updated in
R1-D to route the memory write chain through turn_sink.record_assistant_turn.

───────────────────────────────────────────────────────────────────────────────
Before R1-C (R1-B baseline):
  handle_message         → strip_render_tags → text_output.send → scrub → post_process
  _reply_with_tool_result → strip_render_tags → text_output.send → scrub → post_process

After R1-C:
  handle_message          → _qq_reality_reply_adapter(frozen_scope=_frozen_scope)
  _reply_with_tool_result → _qq_reality_reply_adapter(frozen_scope=frozen_scope)
  _qq_reality_reply_adapter → strip_render_tags → text_output.send (REALITY_VISIBLE)
                            → scrub_reality_output_text → post_process → capture_turn

After R1-D (current):
  _qq_reality_reply_adapter → strip_render_tags → text_output.send (REALITY_VISIBLE)
                            → record_assistant_turn(turn_sink)
                                → scrub + post_process → capture_turn (REALITY_MEMORY)

QQChannel.send: target_id / is_group accepted (no longer hardcoded is_group=False).

Systems short texts (Dream guard / cancel / ask_text) still go directly via
text_output.send or _to_dg.send — NOT through the adapter, NOT written to memory.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parent.parent


# ── helpers ────────────────────────────────────────────────────────────────────

def _src(relpath: str) -> str:
    return (_ROOT / relpath).read_text(encoding="utf-8")


def _lines(relpath: str) -> list[str]:
    return _src(relpath).splitlines()


def _function_body_text(src: str, func_name: str) -> str:
    """Return source text inside the named function (stops at next same-level def/class)."""
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


def _non_comment_lines(relpath: str) -> list[tuple[int, str]]:
    return [
        (i + 1, ln)
        for i, ln in enumerate(_lines(relpath))
        if ln.strip() and not ln.strip().startswith("#")
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# C1. Adapter function exists in main.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_c1_adapter_exists_in_main():
    """C1: _qq_reality_reply_adapter must be defined as an async function in main.py."""
    src = _src("main.py")
    assert "async def _qq_reality_reply_adapter(" in src, (
        "main.py: _qq_reality_reply_adapter not found — R1-C adapter was removed or renamed"
    )


def test_c1b_adapter_is_module_level():
    """
    C1b: _qq_reality_reply_adapter must be a module-level function (indented 0).
    A nested definition would not be accessible from handle_message or
    _reply_with_tool_result.
    """
    for ln in _lines("main.py"):
        if "async def _qq_reality_reply_adapter(" in ln:
            assert not ln[0].isspace(), (
                "_qq_reality_reply_adapter is indented — must be a module-level function"
            )
            break
    else:
        raise AssertionError("_qq_reality_reply_adapter definition not found in main.py")


# ═══════════════════════════════════════════════════════════════════════════════
# C2. Adapter implements the full LLM_ASSISTANT_REPLY chain
# ═══════════════════════════════════════════════════════════════════════════════

def test_c2_adapter_calls_strip_render_tags():
    """C2: Adapter must call strip_render_tags (REALITY_VISIBLE processing)."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "strip_render_tags" in body, (
        "_qq_reality_reply_adapter: strip_render_tags missing — visible output not cleaned"
    )


def test_c2b_adapter_calls_text_output_send():
    """C2b: Adapter must call text_output.send to deliver the visible QQ message."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "text_output.send(" in body, (
        "_qq_reality_reply_adapter: text_output.send missing — QQ message not dispatched"
    )


def test_c2c_adapter_routes_memory_through_turn_sink():
    """
    C2c (R1-D): Adapter must call record_assistant_turn (turn_sink).
    Scrub is now inside record_assistant_turn; the adapter must not duplicate it.
    """
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "record_assistant_turn" in body or "_record_turn" in body, (
        "_qq_reality_reply_adapter: record_assistant_turn not called — "
        "R1-D turn_sink migration missing (memory pre-scrub + post_process not unified)"
    )


def test_c2d_adapter_awaits_turn_sink():
    """
    C2d (R1-D): Adapter must await record_assistant_turn (N10: critical writes must not drop).
    post_process is now invoked inside record_assistant_turn.
    """
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "await _record_turn(" in body or "await record_assistant_turn(" in body, (
        "_qq_reality_reply_adapter: record_assistant_turn not awaited — "
        "N10 regression (memory writes may be dropped)"
    )


def test_c2e_adapter_strip_before_send():
    """C2e: strip_render_tags must be applied before text_output.send in the adapter."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    strip_pos = body.find("strip_render_tags")
    send_pos = body.find("text_output.send(")
    assert strip_pos != -1 and send_pos != -1, (
        "Adapter missing strip_render_tags or text_output.send"
    )
    assert strip_pos < send_pos, (
        "_qq_reality_reply_adapter: strip_render_tags appears AFTER text_output.send — "
        "visible output may contain raw render tags"
    )


def test_c2f_adapter_send_before_turn_sink():
    """
    C2f (R1-D): text_output.send must appear before record_assistant_turn in the adapter.
    Visible delivery should happen before the (potentially slow) memory write chain.
    """
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    send_pos = body.find("text_output.send(")
    ts_pos = body.find("_record_turn(")
    if ts_pos == -1:
        ts_pos = body.find("record_assistant_turn(")
    assert send_pos != -1, "Adapter missing text_output.send"
    assert ts_pos != -1, "Adapter missing record_assistant_turn/_record_turn"
    assert send_pos < ts_pos, (
        "_qq_reality_reply_adapter: text_output.send appears AFTER record_assistant_turn — "
        "visible delivery should precede memory write"
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
# C3. handle_message and _reply_with_tool_result both call the adapter
# ═══════════════════════════════════════════════════════════════════════════════

def test_c3_handle_message_calls_adapter():
    """C3: handle_message must call _qq_reality_reply_adapter for its LLM reply."""
    src = _src("main.py")
    hm_body = _function_body_text(src, "handle_message")
    assert "_qq_reality_reply_adapter(" in hm_body, (
        "handle_message: _qq_reality_reply_adapter not called — "
        "LLM reply path not using the unified adapter"
    )


def test_c3b_tool_reply_calls_adapter():
    """C3b: _reply_with_tool_result must call _qq_reality_reply_adapter."""
    src = _src("main.py")
    tr_body = _function_body_text(src, "_reply_with_tool_result")
    assert "_qq_reality_reply_adapter(" in tr_body, (
        "_reply_with_tool_result: _qq_reality_reply_adapter not called — "
        "tool-reply path not using the unified adapter"
    )


def test_c3c_both_paths_use_same_adapter():
    """C3c: Both calling functions reference exactly the same adapter name."""
    src = _src("main.py")
    hm_body = _function_body_text(src, "handle_message")
    tr_body = _function_body_text(src, "_reply_with_tool_result")
    assert "_qq_reality_reply_adapter(" in hm_body
    assert "_qq_reality_reply_adapter(" in tr_body


# ═══════════════════════════════════════════════════════════════════════════════
# C4. frozen_scope forwarded correctly to adapter
# ═══════════════════════════════════════════════════════════════════════════════

def test_c4_handle_message_passes_frozen_scope():
    """
    C4: handle_message must pass frozen_scope=_frozen_scope to the adapter (N1).
    The adapter then forwards it to post_process.
    """
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


def test_c4c_adapter_passes_frozen_scope_to_post_process():
    """C4c: Adapter must pass frozen_scope to post_process."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "frozen_scope=frozen_scope" in body, (
        "_qq_reality_reply_adapter: frozen_scope not passed to post_process — "
        "N1 scope-freeze broken inside adapter"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C5. System short texts do NOT go through the adapter
# ═══════════════════════════════════════════════════════════════════════════════

def test_c5_dream_guard_sends_direct_not_adapter():
    """
    C5: Dream guard SYSTEM_SHORT_TEXT sends must remain direct (_to_dg.send alias)
    and not be routed through _qq_reality_reply_adapter.
    These sends return immediately and do not write memory.
    """
    src = _src("main.py")
    # Dream guard lines use _to_dg alias; none should call _qq_reality_reply_adapter
    lines = src.splitlines()
    in_dg = False
    adapter_in_guard = False
    for ln in lines:
        # Dream guard block starts after owner_id check and ends at notify_owner_turn
        if "_dg_result == _DGS.BLOCK_ACTIVE" in ln or "_dg_result == _DGS.BLOCK_UNCERTAIN" in ln:
            in_dg = True
        if in_dg and "return" in ln and not ln.strip().startswith("#"):
            in_dg = False
        if in_dg and "_qq_reality_reply_adapter" in ln:
            adapter_in_guard = True

    assert not adapter_in_guard, (
        "Dream guard block calls _qq_reality_reply_adapter — "
        "SYSTEM_SHORT_TEXT must remain direct (no memory write)"
    )


def test_c5b_cancel_confirm_is_direct_send():
    """
    C5b: The cancel-confirm SYSTEM_SHORT_TEXT ("好的，已取消～") must go directly
    through text_output.send, not through _qq_reality_reply_adapter.
    """
    src = _src("main.py")
    for i, ln in enumerate(src.splitlines(), 1):
        if "已取消" in ln and "text_output.send" in ln:
            # This direct send must not be in the adapter function
            adapter_body = _function_body_text(src, "_qq_reality_reply_adapter")
            assert "已取消" not in adapter_body, (
                "Cancel confirm send ended up inside _qq_reality_reply_adapter — "
                "SYSTEM_SHORT_TEXT must stay direct"
            )
            return
    # If the send is not found at all, that's also a problem
    assert False, "main.py: cancel-confirm text_output.send not found"


def test_c5c_ask_text_sends_are_direct():
    """
    C5c: TOOL_CONFIRMATION_PROMPT sends (ask_text) must be direct text_output.send
    calls, not routed through _qq_reality_reply_adapter.
    """
    src = _src("main.py")
    adapter_body = _function_body_text(src, "_qq_reality_reply_adapter")
    assert "ask_text" not in adapter_body, (
        "_qq_reality_reply_adapter contains 'ask_text' — "
        "TOOL_CONFIRMATION_PROMPT must remain a direct send, not go through adapter"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C6. LLM reply paths do not directly call post_process or scrub (R1-D: via turn_sink)
# ═══════════════════════════════════════════════════════════════════════════════

def test_c6_handle_message_no_direct_post_process():
    """
    C6: handle_message must not directly call _pipeline.post_process.
    R1-C routed through the adapter; R1-D further routes through turn_sink.
    A direct call would mean the single-exit invariant is broken.
    """
    src = _src("main.py")
    hm_body = _function_body_text(src, "handle_message")
    non_comment = [
        ln for ln in hm_body.splitlines()
        if "_pipeline.post_process(" in ln and not ln.strip().startswith("#")
    ]
    assert not non_comment, (
        "handle_message: _pipeline.post_process called directly — "
        "must route through _qq_reality_reply_adapter → record_assistant_turn:\n"
        + "\n".join(ln.strip() for ln in non_comment)
    )


def test_c6b_tool_reply_no_direct_post_process():
    """C6b: _reply_with_tool_result must not directly call _pipeline.post_process."""
    src = _src("main.py")
    tr_body = _function_body_text(src, "_reply_with_tool_result")
    non_comment = [
        ln for ln in tr_body.splitlines()
        if "_pipeline.post_process(" in ln and not ln.strip().startswith("#")
    ]
    assert not non_comment, (
        "_reply_with_tool_result: _pipeline.post_process called directly — "
        "must route through _qq_reality_reply_adapter → record_assistant_turn:\n"
        + "\n".join(ln.strip() for ln in non_comment)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C7. QQChannel.send group support (R1-C prerequisite fixed)
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
    """C7d: QQChannel.send must forward the is_group variable to send_message."""
    src = _src("channels/qq.py")
    send_body = _function_body_text(src, "send")
    # send_message must receive `is_group` as a variable (not a literal False)
    assert "send_message(" in send_body, (
        "QQChannel.send: send_message call missing"
    )
    # The is_group variable should appear in the send_message call line
    for ln in send_body.splitlines():
        if "send_message(" in ln and not ln.strip().startswith("#"):
            assert "is_group" in ln, (
                f"QQChannel.send: send_message call does not pass is_group: {ln.strip()}"
            )
            break


# ═══════════════════════════════════════════════════════════════════════════════
# C8. record_assistant_turn is used (R1-D: full turn_sink migration complete)
# ═══════════════════════════════════════════════════════════════════════════════

def test_c8_adapter_uses_record_assistant_turn():
    """
    C8 (R1-D): _qq_reality_reply_adapter must call record_assistant_turn.
    R1-D migrated the memory write chain from direct post_process to turn_sink.
    """
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "record_assistant_turn" in body or "_record_turn" in body, (
        "_qq_reality_reply_adapter does not call record_assistant_turn — "
        "R1-D turn_sink migration missing"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C9. pending_paths forwarded through adapter to post_process
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


def test_c9c_adapter_passes_pending_paths_to_post_process():
    """C9c: Adapter must forward pending_paths to post_process."""
    body = _function_body_text(_src("main.py"), "_qq_reality_reply_adapter")
    assert "pending_paths" in body, (
        "_qq_reality_reply_adapter: pending_paths not forwarded to post_process"
    )
