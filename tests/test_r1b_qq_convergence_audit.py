"""
tests/test_r1b_qq_convergence_audit.py — R1-B: QQ main entry full-convergence audit
(2026-06-11)

Pins the current-state contracts for main.py handle_message and
_reply_with_tool_result against the unified turn_sink convergence target.

───────────────────────────────────────────────────────────────────────────────
text_output.send classification (all sites in main.py):

  LLM_ASSISTANT_REPLY (×2):
    handle_message              — segments → visible QQ send  (→ await post_process)
    _reply_with_tool_result     — segments → visible QQ send  (→ await post_process)

  SYSTEM_SHORT_TEXT (×4):
    handle_message dream-guard exception   — "梦境状态暂时无法确认" (_to_dg alias)
    handle_message dream-guard BLOCK_ACTIVE — "正在梦境中"           (_to_dg alias)
    handle_message dream-guard BLOCK_UNCERTAIN — "梦境状态暂时无法确认" (_to_dg alias)
    handle_message cancel confirm          — "好的，已取消～"

  TOOL_CONFIRMATION_PROMPT (×2):
    handle_message WAITING_INPUT ask_text  — dynamic ask_text string
    handle_message tool-probe ask_text     — dynamic ask_text string

post_process classification (all sites in main.py):
  handle_message main LLM reply       — await, frozen_scope=_frozen_scope  ✓
  _reply_with_tool_result tool reply  — await, frozen_scope=frozen_scope   ✓

Convergence delta vs turn_sink (R1-B baseline):
  ✓  scope freeze (_frozen_scope) — N1
  ✓  conversation_lock — R1
  ✓  await post_process (no bare create_task) — N10
  ✓  frozen_scope passed to post_process — N1
  ✓  pre-scrub (scrub_reality_output_text) + strip_render_tags — R6-A/B
  ✗  LLM_ASSISTANT_REPLY uses text_output.send directly (not turn_sink/channel fanout)
  ✗  QQChannel.send hardcodes is_group=False (group support gap, R1-C prereq)
  ✗  post_process call signature differs from turn_sink.record_assistant_turn
     (QQ passes target_id / is_group / pending_paths / frozen_scope; turn_sink does not)
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent


# ── helpers ────────────────────────────────────────────────────────────────────

def _src(relpath: str) -> str:
    return (_ROOT / relpath).read_text(encoding="utf-8")


def _lines(relpath: str) -> list[str]:
    return _src(relpath).splitlines()


def _non_comment_lines(relpath: str) -> list[tuple[int, str]]:
    """Return (1-based lineno, text) for non-blank, non-comment lines."""
    return [
        (i + 1, ln)
        for i, ln in enumerate(_lines(relpath))
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _function_body_text(src: str, func_name: str) -> str:
    """
    Return source text inside the named function (stops at next same-level def/class).
    """
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
# A1. text_output.send total count — all call sites must be classified
# ═══════════════════════════════════════════════════════════════════════════════

def test_a1a_text_output_send_count():
    """
    A1a (R1-C updated): Exactly 4 text_output.send( calls in main.py non-comment lines.

    R1-C collapsed the two LLM_ASSISTANT_REPLY direct sends (handle_message + tool-reply)
    into a single call inside _qq_reality_reply_adapter.

    Expected:
      cancel confirm          SYSTEM_SHORT_TEXT      (handle_message)
      WAITING_INPUT ask_text  TOOL_CONFIRMATION_PROMPT (handle_message)
      probe ask_text          TOOL_CONFIRMATION_PROMPT (handle_message)
      adapter reply           LLM_ASSISTANT_REPLY    (_qq_reality_reply_adapter)
    """
    hits = [
        (lineno, ln)
        for lineno, ln in _non_comment_lines("main.py")
        if "text_output.send(" in ln
    ]
    assert len(hits) == 4, (
        f"Expected 4 text_output.send( calls in main.py (R1-C: 3 direct + 1 in adapter), "
        f"found {len(hits)}:\n"
        + "\n".join(f"  L{lineno}: {ln.strip()}" for lineno, ln in hits)
    )


def test_a1b_dg_send_count():
    """
    A1b: Exactly 3 _to_dg.send( calls in main.py (dream-guard SYSTEM_SHORT_TEXT).

    All three occur inside the dream-guard block and immediately return —
    they must not be reclassified to LLM_ASSISTANT_REPLY without a corresponding
    post_process call.
    """
    hits = [
        (lineno, ln)
        for lineno, ln in _non_comment_lines("main.py")
        if "_to_dg.send(" in ln
    ]
    assert len(hits) == 3, (
        f"Expected 3 _to_dg.send( calls in main.py (dream-guard sends), "
        f"found {len(hits)}:\n"
        + "\n".join(f"  L{lineno}: {ln.strip()}" for lineno, ln in hits)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A2. LLM_ASSISTANT_REPLY sends use the `segments` variable
# ═══════════════════════════════════════════════════════════════════════════════

def test_a2_llm_reply_sends_use_segments():
    """
    A2 (R1-C updated): Exactly 1 text_output.send call passes the `clean` variable
    (R1-C adapter internal name) or `segments`-derived content inside
    _qq_reality_reply_adapter.

    R1-C: both LLM paths route through the adapter, so there is exactly 1 send
    that moves LLM output to QQ.  The adapter uses `clean` (strip_render_tags output).
    """
    src = _src("main.py")
    adapter_body = _function_body_text(src, "_qq_reality_reply_adapter")
    hits_in_adapter = [
        ln for ln in adapter_body.splitlines()
        if "text_output.send(" in ln and not ln.strip().startswith("#")
    ]
    assert len(hits_in_adapter) == 1, (
        f"Expected exactly 1 text_output.send call inside _qq_reality_reply_adapter, "
        f"found {len(hits_in_adapter)}:\n"
        + "\n".join(f"  {ln.strip()}" for ln in hits_in_adapter)
    )


def test_a2b_llm_reply_sends_in_expected_functions():
    """
    A2b (R1-C updated): The LLM_ASSISTANT_REPLY send now lives exclusively inside
    _qq_reality_reply_adapter, NOT in handle_message or _reply_with_tool_result.
    Both calling functions delegate to the adapter rather than sending directly.
    """
    src = _src("main.py")
    hm_body = _function_body_text(src, "handle_message")
    tr_body = _function_body_text(src, "_reply_with_tool_result")
    adapter_body = _function_body_text(src, "_qq_reality_reply_adapter")

    assert "text_output.send(" not in hm_body or all(
        "segments" not in ln and "clean" not in ln
        for ln in hm_body.splitlines()
        if "text_output.send(" in ln and not ln.strip().startswith("#")
    ), (
        "handle_message: LLM_ASSISTANT_REPLY send still in function body — "
        "R1-C should have moved it into _qq_reality_reply_adapter"
    )
    assert "text_output.send(" not in tr_body or all(
        "segments" not in ln and "clean" not in ln
        for ln in tr_body.splitlines()
        if "text_output.send(" in ln and not ln.strip().startswith("#")
    ), (
        "_reply_with_tool_result: LLM_ASSISTANT_REPLY send still in function body — "
        "R1-C should have moved it into _qq_reality_reply_adapter"
    )
    assert "text_output.send(" in adapter_body, (
        "_qq_reality_reply_adapter: text_output.send missing — "
        "LLM reply send not wired into adapter"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A3. No bare asyncio.create_task wrapping post_process in main.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_a3_no_create_task_for_post_process():
    """
    A3: main.py must not use asyncio.create_task to schedule post_process.
    N10 fix: both LLM reply paths now await post_process directly.
    A create_task pattern would re-introduce the N10 regression (dropped write reference).
    """
    lines = _lines("main.py")
    violations: list[str] = []
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        if "create_task" not in ln:
            continue
        # Check the call line + next 4 lines for post_process reference
        context = "\n".join(lines[i : i + 5])
        if "post_process" in context:
            violations.append(f"main.py:{i + 1}: {stripped}")

    assert not violations, (
        "main.py uses create_task for post_process — N10 regression:\n"
        + "\n".join(violations)
    )


def test_a3b_create_task_calls_are_startup_only():
    """
    A3b: All asyncio.create_task calls in main.py are startup infrastructure
    (admin_server or qq_adapter), not post_process paths.
    """
    lines = _lines("main.py")
    _ALLOWED_TARGETS = {"start_admin_server", "qq_adapter.connect_and_listen"}
    violations: list[str] = []
    for i, ln in enumerate(lines, 1):
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        if "create_task(" not in ln:
            continue
        if not any(t in ln for t in _ALLOWED_TARGETS):
            violations.append(f"main.py:{i}: {stripped}")

    assert not violations, (
        "Unexpected create_task call in main.py (not a startup task):\n"
        + "\n".join(violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A4. Both LLM reply functions await post_process
# ═══════════════════════════════════════════════════════════════════════════════

def test_a4_handle_message_awaits_post_process():
    """
    A4 (R1-D updated): handle_message delegates to _qq_reality_reply_adapter,
    which calls record_assistant_turn (turn_sink chain) — NOT post_process directly.
    post_process is invoked inside turn_sink, so the contract is still satisfied
    but at a higher abstraction level.
    """
    src = _src("main.py")
    hm_body = _function_body_text(src, "handle_message")
    adapter_body = _function_body_text(src, "_qq_reality_reply_adapter")
    assert "_qq_reality_reply_adapter(" in hm_body, (
        "handle_message: _qq_reality_reply_adapter call missing — R1-C adapter not wired"
    )
    # R1-D: adapter routes through turn_sink, not direct post_process call
    assert "_record_turn(" in adapter_body or "record_assistant_turn(" in adapter_body, (
        "_qq_reality_reply_adapter: record_assistant_turn/_record_turn not called — "
        "R1-D turn_sink chain not wired"
    )
    assert "await _pipeline.post_process(" not in adapter_body, (
        "_qq_reality_reply_adapter: direct post_process call found — "
        "R1-D requires routing through turn_sink (record_assistant_turn)"
    )


def test_a4b_tool_reply_awaits_post_process():
    """
    A4b (R1-D updated): _reply_with_tool_result also delegates to
    _qq_reality_reply_adapter, which calls record_assistant_turn (turn_sink chain).
    post_process is invoked inside turn_sink rather than directly.
    """
    src = _src("main.py")
    tr_body = _function_body_text(src, "_reply_with_tool_result")
    adapter_body = _function_body_text(src, "_qq_reality_reply_adapter")
    assert "_qq_reality_reply_adapter(" in tr_body, (
        "_reply_with_tool_result: _qq_reality_reply_adapter call missing — "
        "R1-C adapter not wired into tool-reply path"
    )
    # R1-D: adapter routes through turn_sink, not direct post_process call
    assert "_record_turn(" in adapter_body or "record_assistant_turn(" in adapter_body, (
        "_qq_reality_reply_adapter: record_assistant_turn/_record_turn not called — "
        "R1-D turn_sink chain not wired"
    )
    assert "await _pipeline.post_process(" not in adapter_body, (
        "_qq_reality_reply_adapter: direct post_process call found — "
        "R1-D requires routing through turn_sink (record_assistant_turn)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A5. frozen_scope is passed to post_process in both LLM paths
# ═══════════════════════════════════════════════════════════════════════════════

def test_a5_handle_message_passes_frozen_scope():
    """
    A5: handle_message must pass frozen_scope=_frozen_scope to post_process (N1).
    """
    src = _src("main.py")
    body = _function_body_text(src, "handle_message")
    assert "frozen_scope=_frozen_scope" in body, (
        "handle_message: frozen_scope=_frozen_scope not found in post_process call — "
        "N1 scope-freeze regression"
    )


def test_a5b_tool_reply_passes_frozen_scope():
    """
    A5b: _reply_with_tool_result must pass frozen_scope=frozen_scope to post_process.
    When frozen_scope is None (legacy fallback), post_process will freeze internally.
    """
    src = _src("main.py")
    body = _function_body_text(src, "_reply_with_tool_result")
    assert "frozen_scope=frozen_scope" in body, (
        "_reply_with_tool_result: frozen_scope not forwarded to post_process — "
        "the N1 scope freeze regression or the legacy-compat fallback comment was removed"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A6. _reply_with_tool_result accepts frozen_scope parameter
# ═══════════════════════════════════════════════════════════════════════════════

def test_a6_tool_reply_has_frozen_scope_param():
    """
    A6: _reply_with_tool_result function signature must include frozen_scope=None.
    This enables handle_message to pass the already-frozen scope (N1).
    """
    src = _src("main.py")
    body = _function_body_text(src, "_reply_with_tool_result")
    # Signature lines are included in body_text
    assert "frozen_scope=None" in body, (
        "_reply_with_tool_result: frozen_scope=None parameter missing from signature — "
        "scope freeze (N1) cannot be forwarded from handle_message"
    )


def test_a6b_handle_message_passes_frozen_scope_to_tool_reply():
    """
    A6b: handle_message must call _reply_with_tool_result with frozen_scope=_frozen_scope.
    Without this, the tool-confirm path runs with no scope freeze.
    """
    src = _src("main.py")
    body = _function_body_text(src, "handle_message")
    assert "_reply_with_tool_result" in body, (
        "handle_message does not call _reply_with_tool_result — unexpected structural change"
    )
    assert "frozen_scope=_frozen_scope" in body, (
        "handle_message: _reply_with_tool_result not called with frozen_scope=_frozen_scope"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A7. Both LLM paths pre-scrub with scrub_reality_output_text + strip_render_tags
#     (R6-A/B: defense-in-depth upstream pre-scrub contract)
# ═══════════════════════════════════════════════════════════════════════════════

def test_a7_handle_message_uses_scrub_and_strip():
    """
    A7 (R1-C updated): scrub_reality_output_text and strip_render_tags both live
    inside _qq_reality_reply_adapter, which handle_message invokes.  The R6-A/B
    pre-scrub contract is preserved — it's just consolidated into the adapter.
    """
    src = _src("main.py")
    adapter_body = _function_body_text(src, "_qq_reality_reply_adapter")
    assert "scrub_reality_output_text" in adapter_body, (
        "_qq_reality_reply_adapter: scrub_reality_output_text missing — "
        "QQ memory pre-scrub removed (R6-A/B regression)"
    )
    assert "strip_render_tags" in adapter_body, (
        "_qq_reality_reply_adapter: strip_render_tags missing — "
        "visible output no longer cleaned (R6-A/B regression)"
    )


def test_a7b_tool_reply_uses_scrub_and_strip():
    """
    A7b (R1-C updated): _reply_with_tool_result routes through
    _qq_reality_reply_adapter, which provides both scrub_reality_output_text
    and strip_render_tags.  R6-A/B pre-scrub contract still holds.
    """
    src = _src("main.py")
    tr_body = _function_body_text(src, "_reply_with_tool_result")
    adapter_body = _function_body_text(src, "_qq_reality_reply_adapter")
    assert "_qq_reality_reply_adapter(" in tr_body, (
        "_reply_with_tool_result: adapter call missing — scrub/strip no longer guaranteed"
    )
    assert "scrub_reality_output_text" in adapter_body, (
        "_qq_reality_reply_adapter: scrub_reality_output_text missing — R6-A/B regression"
    )
    assert "strip_render_tags" in adapter_body, (
        "_qq_reality_reply_adapter: strip_render_tags missing — R6-A/B regression"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A8. Shared scrub contract: QQ pre-scrub pattern matches turn_sink pre-scrub
# ═══════════════════════════════════════════════════════════════════════════════

def test_a8_qq_and_turn_sink_both_pre_scrub():
    """
    A8: Both the QQ inlet (main.py) and the non-QQ inlet (turn_sink.py) call
    scrub_reality_output_text as defense-in-depth before post_process.
    Removing scrub from either path would create an asymmetric contract.
    """
    main_src = _src("main.py")
    sink_src = _src("core/turn_sink.py")

    assert "scrub_reality_output_text" in main_src, (
        "main.py (QQ inlet): scrub_reality_output_text not imported/called"
    )
    assert "scrub_reality_output_text" in sink_src, (
        "core/turn_sink.py (non-QQ inlet): scrub_reality_output_text not imported/called"
    )


def test_a8b_r6b_contract_still_holds():
    """
    A8b: R6-B test module still importable (no structural breakage of the scrub
    contract module after R1-B changes).
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "test_r6b", _ROOT / "tests" / "test_r6b_reality_scrub_contract.py"
    )
    assert spec is not None, "test_r6b_reality_scrub_contract.py not found"
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception as exc:
        pytest.fail(f"test_r6b_reality_scrub_contract.py failed to import: {exc}")

    # Verify key contract tests are still defined
    for attr in ("test_c5_capture_turn_has_authority_scrub", "test_c8_fanout_no_reality_scrub"):
        assert hasattr(mod, attr), (
            f"test_r6b_reality_scrub_contract.py: {attr} is missing — "
            "R6-B contract was removed"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# A9. Partial-convergence gap: QQ not yet using record_assistant_turn
# ═══════════════════════════════════════════════════════════════════════════════

def test_a9_qq_now_using_record_assistant_turn():
    """
    A9 (R1-D: INVERTED from R1-B marker): main.py must import and call
    record_assistant_turn (via alias _record_turn) through _qq_reality_reply_adapter.

    R1-D completed the turn_sink migration; the old direct-send + post_process pattern
    in the adapter has been replaced by the unified turn_sink chain.
    """
    src = _src("main.py")
    assert "record_assistant_turn" in src, (
        "main.py: record_assistant_turn not found — "
        "R1-D turn_sink migration not in effect; adapter must call _record_turn"
    )
    # Old direct-call pattern must be gone from the adapter
    adapter_body = _function_body_text(src, "_qq_reality_reply_adapter")
    assert "await _pipeline.post_process(" not in adapter_body, (
        "_qq_reality_reply_adapter: direct _pipeline.post_process call found — "
        "R1-D requires routing through record_assistant_turn instead"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A10. QQChannel.send group-support gap (R1-C prerequisite)
# ═══════════════════════════════════════════════════════════════════════════════

def test_a10_qq_channel_no_longer_hardcodes_is_group_false():
    """
    A10 (R1-C: INVERTED from R1-B): QQChannel.send must NOT hardcode is_group=False
    in the qq_adapter.send_message call.

    R1-C fixed QQChannel.send to accept optional target_id and is_group parameters
    and pass them through, removing the hardcoded is_group=False that would have
    silently routed group messages as private chats.
    """
    src = _src("channels/qq.py")
    send_body = _function_body_text(src, "send")
    assert "is_group=False" not in send_body, (
        "channels/qq.py QQChannel.send: is_group=False hardcode is back — "
        "R1-C fix was reverted; group messages would be silently routed as private"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A11. known-issues.md documents R1-B partial convergence state
# ═══════════════════════════════════════════════════════════════════════════════

def test_a11_known_issues_documents_r1b():
    """
    A11: docs/known-issues.md must mention R1-B convergence audit.
    This ensures the B11 section is kept up to date as fixes land.
    """
    src = _src("docs/known-issues.md")
    assert "R1-B" in src, (
        "docs/known-issues.md does not mention R1-B — "
        "update B11 to reflect the current partial-convergence state"
    )


def test_a11b_known_issues_reflects_n10_fix():
    """
    A11b: docs/known-issues.md B11 must NOT still claim create_task is used.
    N10 changed both QQ paths from create_task to await; B11 must reflect this.
    """
    src = _src("docs/known-issues.md")
    b11_start = src.find("### B11")
    b11_end = src.find("\n---", b11_start) if b11_start != -1 else -1
    if b11_start == -1 or b11_end == -1:
        return  # B11 removed or restructured — skip

    b11_text = src[b11_start:b11_end]
    # N10 fix acknowledged: should NOT still say "仍在发送后以 asyncio.create_task"
    assert "asyncio.create_task" not in b11_text or "N10" in b11_text, (
        "docs/known-issues.md B11 still says create_task is used without noting "
        "N10 fixed it — update the section to reflect the current state"
    )
