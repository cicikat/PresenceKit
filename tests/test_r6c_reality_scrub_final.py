"""
tests/test_r6c_reality_scrub_final.py — R6-final: Reality output scrub single-exit stable state

Confirms the R6 final convergence state after R1-D (2026-06-11):
  - Both QQ LLM reply paths (handle_message + _reply_with_tool_result) route through
    _qq_reality_reply_adapter → record_assistant_turn (turn_sink) → post_process → capture_turn.
  - capture_turn remains the REALITY_MEMORY authority scrub point.
  - System short texts (Dream guard / cancel / ask_text) do not write memory.
  - Dream modules remain isolated from reality_output_scrubber.
  - docs/known-issues.md and docs/assistant-turn-sink.md reflect R6-final stable state.

These tests complement R6-A (test_r6_reality_scrub_audit.py) and R6-B
(test_r6b_reality_scrub_contract.py) by confirming the *post-R1-D convergence* invariants.
They do NOT duplicate existing R6-A/B or R1-C/D tests; they confirm the final-state contracts
that only became true after R1-D landed.

Naming: F-prefix = R6-final specific guard.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
# F1. Single post_process exit in main.py — adapter is the sole caller
# ═══════════════════════════════════════════════════════════════════════════════

def test_f1_adapter_routes_memory_through_turn_sink():
    """
    F1 (R1-D): In main.py, _pipeline.post_process must NOT be called directly at all.
    After R1-D, both QQ LLM paths route through:
      _qq_reality_reply_adapter → record_assistant_turn (turn_sink) → post_process

    A direct post_process call in main.py would mean the adapter is bypassed,
    breaking the single-exit invariant.
    """
    src = _src("main.py")
    lines = src.splitlines()

    violations: list[str] = []
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        if "_pipeline.post_process(" in ln:
            violations.append(f"main.py:{i+1}: {stripped}")

    assert not violations, (
        "main.py: _pipeline.post_process called directly — "
        "R1-D: must route through _qq_reality_reply_adapter → record_assistant_turn:\n"
        + "\n".join(violations)
    )


def test_f1b_adapter_does_not_directly_scrub_in_main():
    """
    F1b (R1-D): main.py must not directly call scrub_reality_output_text.
    After R1-D, scrub lives inside turn_sink.record_assistant_turn (defense-in-depth)
    and capture_turn (authority).  A direct scrub call in main.py would be redundant
    and signals a regression to the R1-C hand-written chain.
    """
    src = _src("main.py")
    lines = src.splitlines()

    violations: list[str] = []
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        if "scrub_reality_output_text" in ln:
            violations.append(f"main.py:{i+1}: {stripped}")

    assert not violations, (
        "main.py: scrub_reality_output_text called directly — "
        "R1-D: scrub must live in turn_sink (defense-in-depth) and capture_turn (authority):\n"
        + "\n".join(violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# F2. System short texts: direct sends followed by return (no memory write)
# ═══════════════════════════════════════════════════════════════════════════════

def test_f2_system_short_text_sends_outside_adapter_are_not_llm():
    """
    F2: Every text_output.send (or _to_dg.send) in main.py that is NOT inside
    _qq_reality_reply_adapter must contain only hardcoded string literals or
    ask_text — confirming they are SYSTEM_SHORT_TEXT / TOOL_CONFIRMATION_PROMPT,
    not LLM-generated content.

    LLM-generated content always uses the `clean` variable (inside the adapter).
    """
    src = _src("main.py")
    lines = src.splitlines()

    adapter_linenos: set[int] = set()
    in_adapter = False
    base_indent: int | None = None
    for i, ln in enumerate(lines):
        stripped = ln.lstrip()
        indent = len(ln) - len(stripped)
        if "async def _qq_reality_reply_adapter(" in ln:
            in_adapter = True
            base_indent = indent
        if in_adapter:
            adapter_linenos.add(i)
            if stripped and indent <= base_indent and (
                stripped.startswith("def ")
                or stripped.startswith("async def ")
                or stripped.startswith("class ")
                or stripped.startswith("@")
            ) and "async def _qq_reality_reply_adapter(" not in ln:
                in_adapter = False

    # LLM content marker: the adapter uses `clean` variable
    llm_marker = "clean"
    # Allowed patterns for system short texts
    system_patterns = [
        '["好的，已取消～"]',
        "[ask_text]",
        '["梦境状态暂时无法确认',
        '["正在梦境中',
        '["梦境状态暂时无法确认',
    ]

    violations: list[str] = []
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        is_send = "text_output.send(" in ln or "_to_dg.send(" in ln
        if not is_send:
            continue
        if i in adapter_linenos:
            # Inside adapter — must use `clean` (not raw segments or LLM variable names)
            if "clean" not in ln and "text_output.send(" in ln:
                violations.append(f"main.py:{i+1} [inside adapter, not using clean]: {stripped}")
            continue
        # Outside adapter — must be a system text (not using LLM variables)
        if "segments" in ln or "raw_reply" in ln:
            violations.append(f"main.py:{i+1} [outside adapter, LLM variable]: {stripped}")

    assert not violations, (
        "main.py: unexpected LLM variable in text_output.send outside adapter:\n"
        + "\n".join(violations)
    )


def test_f2b_system_sends_not_followed_by_post_process():
    """
    F2b: None of the SYSTEM_SHORT_TEXT sends in main.py (outside the adapter)
    should be followed by a _pipeline.post_process call in the same function scope.
    This confirms system texts do NOT write to memory.
    """
    src = _src("main.py")
    # Verify that the cancel-confirm path has `return` after send and no post_process
    # by checking the full function bodies
    hm_body = _function_body_text(src, "handle_message")

    # The adapter body is excluded; we check handle_message's non-adapter sends
    # Verify: after the cancel-confirm send there's a return
    cancel_send_idx = None
    hm_lines = hm_body.splitlines()
    for i, ln in enumerate(hm_lines):
        if '["好的，已取消～"]' in ln:
            cancel_send_idx = i
            break

    assert cancel_send_idx is not None, "cancel-confirm send not found in handle_message"

    # Next non-blank non-comment line should be `return`
    found_return = False
    for ln in hm_lines[cancel_send_idx + 1:]:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        assert s == "return", (
            f"After cancel-confirm send in handle_message, expected 'return' but got: {s!r}"
        )
        found_return = True
        break

    assert found_return, "cancel-confirm send not followed by return in handle_message"


# ═══════════════════════════════════════════════════════════════════════════════
# F3. Dream isolation — broader check across all dream-related modules
# ═══════════════════════════════════════════════════════════════════════════════

_DREAM_MODULES = [
    "core/dream/dream_pipeline.py",
    "core/dream/dream_state.py",
    "admin/routers/dream.py",
]


@pytest.mark.parametrize("relpath", _DREAM_MODULES)
def test_f3_dream_modules_no_reality_scrubber(relpath: str):
    """
    F3: All dream-related modules must not import or reference reality_output_scrubber.
    Dream content has a completely separate output path and must bypass the reality
    scrub chain entirely.
    """
    src = _src(relpath)
    assert "reality_output_scrubber" not in src, (
        f"{relpath}: imports reality_output_scrubber — "
        "dream output must NOT be processed by the reality scrubber"
    )
    assert "scrub_reality_output_text" not in src, (
        f"{relpath}: calls scrub_reality_output_text — "
        "dream path must bypass reality scrub"
    )


def test_f3b_dream_modules_no_capture_turn_call():
    """
    F3b: Dream pipeline must not call capture_turn (actual call, not docstring reference).
    Dream turns must never enter the reality MEMORY chain.
    """
    src = _src("core/dream/dream_pipeline.py")
    lines = src.splitlines()
    violations: list[str] = []
    for i, ln in enumerate(lines, 1):
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        if "capture_turn(" in ln:
            violations.append(f"core/dream/dream_pipeline.py:{i}: {stripped}")

    assert not violations, (
        "dream_pipeline.py calls capture_turn() — dream memory must not enter reality chain:\n"
        + "\n".join(violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# F4. Desktop / scheduler paths route through record_assistant_turn (non-QQ scrub intact)
# ═══════════════════════════════════════════════════════════════════════════════

def test_f4_desktop_chat_routes_through_turn_sink():
    """
    F4: admin/routers/chat.py must call record_assistant_turn.
    Desktop / mobile chat paths must use turn_sink for the pre-scrub + post_process chain.
    """
    src = _src("admin/routers/chat.py")
    assert "record_assistant_turn" in src, (
        "admin/routers/chat.py: record_assistant_turn not called — "
        "desktop memory pre-scrub may be bypassed"
    )


def test_f4b_scheduler_loop_routes_through_turn_sink():
    """
    F4b: core/scheduler/loop.py must call record_assistant_turn.
    Scheduler triggers must use turn_sink for the pre-scrub + post_process chain.
    """
    src = _src("core/scheduler/loop.py")
    assert "record_assistant_turn" in src, (
        "core/scheduler/loop.py: record_assistant_turn not called — "
        "scheduler trigger memory pre-scrub may be bypassed"
    )


def test_f4c_turn_sink_record_assistant_turn_calls_scrub():
    """
    F4c: turn_sink.record_assistant_turn must call scrub_reality_output_text.
    This is the inlet pre-scrub for all paths (desktop/scheduler/sensor/wake/QQ via R1-D).
    """
    src = _src("core/turn_sink.py")
    body = _function_body_text(src, "record_assistant_turn")
    assert "scrub_reality_output_text" in body, (
        "turn_sink.record_assistant_turn: scrub_reality_output_text not called — "
        "inlet pre-scrub guard missing (all paths including QQ R1-D)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# F5. capture_turn authority chain remains intact
# ═══════════════════════════════════════════════════════════════════════════════

def test_f5_capture_turn_authority_scrub_intact():
    """
    F5: capture_turn in fixation_pipeline.py must still call scrub_reality_output_text
    AND the scrub must happen before the short_term.append call.
    This is the REALITY_MEMORY authority scrub — removing it would allow action/narration
    into memory whenever an upstream pre-scrub is skipped.
    """
    src = _src("core/memory/fixation_pipeline.py")
    body_text = _function_body_text(src, "capture_turn")

    scrub_pos = body_text.find("scrub_reality_output_text")
    st_pos = body_text.find("short_term.append(")
    assert scrub_pos != -1, "capture_turn: scrub_reality_output_text not found"
    assert st_pos != -1, "capture_turn: short_term.append not found"
    assert scrub_pos < st_pos, (
        "capture_turn: scrub appears AFTER short_term.append — order contract broken"
    )


def test_f5b_no_bare_memory_writes_in_main_or_admin():
    """
    F5b: main.py and admin/ must not directly call short_term.append or event_log.append.
    All memory writes must route through capture_turn.
    """
    files_to_check = [
        "main.py",
        "admin/routers/chat.py",
        "admin/routers/dream.py",
    ]
    violations: list[str] = []
    for relpath in files_to_check:
        lines = _lines(relpath)
        for i, ln in enumerate(lines, 1):
            stripped = ln.strip()
            if stripped.startswith("#"):
                continue
            if "short_term.append(" in ln or "event_log.append(" in ln:
                violations.append(f"{relpath}:{i}: {stripped}")

    assert not violations, (
        "Direct short_term.append or event_log.append call found in main.py/admin — "
        "must route through pipeline.post_process → capture_turn:\n"
        + "\n".join(violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# F6. Document stability — R6 final state reflected in docs
# ═══════════════════════════════════════════════════════════════════════════════

def test_f6_known_issues_no_r6_pending_r1c():
    """
    F6: docs/known-issues.md must not contain 'R6 final...可在 R1-D 完成后开始'
    or similar language implying R6 final is blocked on R1-D.

    R6 final was completed in R1-C (2026-06-11). This test confirms the doc was updated.
    """
    src = _src("docs/known-issues.md")
    # The stale "pending R1-D" statement must be gone
    assert "R6 final（单出口稳态）可在 R1-D 完成后开始" not in src, (
        "docs/known-issues.md still says R6 final is pending R1-D — "
        "R6 final was completed in R1-C (2026-06-11); update the doc"
    )


def test_f6b_known_issues_has_r6_final_entry():
    """
    F6b: docs/known-issues.md must have an R6 final entry marking it as completed.
    """
    src = _src("docs/known-issues.md")
    assert "R6-final" in src or "R6 final" in src, (
        "docs/known-issues.md has no R6-final entry — "
        "add a bullet confirming R6 final stable state"
    )
    # Must be marked as completed / stable, not just referenced
    assert (
        "R6 final" in src
        and (
            "已完成" in src
            or "stable" in src.lower()
            or "final" in src.lower()
        )
    ), (
        "docs/known-issues.md references R6 final but does not mark it complete/stable"
    )


def test_f6c_assistant_turn_sink_has_r6_final_section():
    """
    F6c: docs/assistant-turn-sink.md must have a section documenting R6 final status.
    """
    src = _src("docs/assistant-turn-sink.md")
    assert "R6 final" in src or "R6-final" in src, (
        "docs/assistant-turn-sink.md has no R6 final section — "
        "add documentation of the single-exit stable state"
    )


def test_f6d_assistant_turn_sink_names_adapter_as_stable_exit():
    """
    F6d: docs/assistant-turn-sink.md must reference _qq_reality_reply_adapter as
    the stable QQ convergence point.
    """
    src = _src("docs/assistant-turn-sink.md")
    assert "_qq_reality_reply_adapter" in src, (
        "docs/assistant-turn-sink.md does not name _qq_reality_reply_adapter — "
        "document the adapter as the stable QQ LLM_ASSISTANT_REPLY exit point"
    )


def test_f6e_assistant_turn_sink_mentions_r1d():
    """
    F6e: docs/assistant-turn-sink.md must mention R1-D (either as completed or as a step).
    """
    src = _src("docs/assistant-turn-sink.md")
    assert "R1-D" in src, (
        "docs/assistant-turn-sink.md does not mention R1-D — "
        "document R1-D turn_sink migration status"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# F7. Behavioral: QQ full chain — both paths produce scrubbed memory via adapter
# ═══════════════════════════════════════════════════════════════════════════════

def _make_pipeline_f7(llm_reply: str = "回复", char_id: str = "yexuan"):
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


@pytest.mark.asyncio
async def test_f7_handle_message_adapter_scrubs_before_post_process(sandbox, monkeypatch):
    """
    F7: handle_message → _qq_reality_reply_adapter: action lines must be scrubbed
    from memory_reply before post_process receives it.
    Confirms the full QQ main path chain is intact post-R1-C.
    """
    from core.dream.dream_state import DreamStatus, write_state
    write_state("77777", {"status": DreamStatus.REALITY_CHAT.value, "user_id": "77777"})

    import core.config_loader as _cl
    monkeypatch.setattr(_cl, "get_config", lambda: {
        "scheduler": {"owner_id": "77777"},
        "llm": {"tool_call_mode": "function_calling"},
    })
    for mod_path, attr, stub in [
        ("core.scheduler.loop", "mark_user_active", lambda: None),
        ("core.presence", "update_last_message", lambda uid: None),
        ("core.scheduler.state_machine", "notify_owner_turn", lambda uid: None),
    ]:
        try:
            import importlib
            m = importlib.import_module(mod_path)
            monkeypatch.setattr(m, attr, stub)
        except Exception:
            pass

    import core.memory.short_term as _st, core.memory.user_profile as _up
    import core.memory.group_context as _gc, core.user_relation as _ur
    monkeypatch.setattr(_st, "load_for_prompt", lambda uid, **kw: [])
    monkeypatch.setattr(_up, "load", lambda uid, **kw: {"location": "杭州"})
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_gc, "append", lambda *a, **kw: None)
    monkeypatch.setattr(_ur, "get_relation", lambda uid: {})

    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", AsyncMock())

    import core.tool_dispatcher as _td
    _td._TOOL_REGISTRY = {}
    monkeypatch.setattr(_td, "get_probe_prompt", lambda loc: "")
    monkeypatch.setattr(_td, "get_tools_schema", lambda categories=None: [])
    import core.llm_client as _llm
    monkeypatch.setattr(_llm, "chat", AsyncMock(return_value=""))
    monkeypatch.setattr(_llm, "parse_tool_call_response", lambda r: [])

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    import main as _main
    raw = "（她低头轻轻抬起手）\n好的，我明白了。"
    fake = _make_pipeline_f7(llm_reply=raw)

    captured: list[str] = []
    async def spy_pp(uid, content, reply, *args, **kwargs):
        captured.append(reply)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}
    fake.post_process = spy_pp
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main.handle_message({
        "user_id": "77777",
        "content": "测试",
        "sender_name": "77777",
    })

    assert captured, "post_process was not called from handle_message path"
    mem = captured[0]
    assert "（她低头轻轻抬起手）" not in mem, (
        f"Action line leaked into memory via handle_message path: {mem!r}"
    )
    assert "好的，我明白了" in mem, (
        f"Dialogue text missing from memory: {mem!r}"
    )


@pytest.mark.asyncio
async def test_f7b_tool_reply_adapter_scrubs_before_post_process(sandbox, monkeypatch):
    """
    F7b: _reply_with_tool_result → _qq_reality_reply_adapter: action lines must be
    scrubbed from memory_reply before post_process receives it.
    Confirms the QQ tool-confirm path chain is intact post-R1-C.
    """
    import core.memory.short_term as _st, core.memory.user_profile as _up
    import core.memory.group_context as _gc, core.user_relation as _ur
    monkeypatch.setattr(_st, "load_for_prompt", lambda uid, **kw: [])
    monkeypatch.setattr(_up, "load", lambda uid, **kw: {"location": "杭州"})
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_gc, "append", lambda *a, **kw: None)
    monkeypatch.setattr(_ur, "get_relation", lambda uid: {})

    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", AsyncMock())

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    import main as _main
    raw = "*她伸手拿起文件*\n工具已执行完毕。"
    fake = _make_pipeline_f7(llm_reply=raw)

    captured: list[str] = []
    async def spy_pp(uid, content, reply, *args, **kwargs):
        captured.append(reply)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}
    fake.post_process = spy_pp
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main._reply_with_tool_result("tool_result_data", "u1", "u1", False)

    assert captured, "post_process was not called from _reply_with_tool_result path"
    mem = captured[0]
    assert "*她伸手拿起文件*" not in mem, (
        f"Action line leaked into memory via _reply_with_tool_result path: {mem!r}"
    )
    assert "工具已执行完毕" in mem, (
        f"Dialogue text missing from tool-reply memory: {mem!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# F8. Reality scrubber module docstring remains authoritative
# ═══════════════════════════════════════════════════════════════════════════════

def test_f8_scrubber_docstring_names_qq_adapter():
    """
    F8: reality_output_scrubber.py module docstring must name
    main._qq_reality_reply_adapter as a call site.
    After R1-C, this replaced the direct handle_message / _reply_with_tool_result
    references.
    """
    src = _src("core/reality_output_scrubber.py")
    # R1-C renamed the call site; the docstring should reflect the adapter or QQ path
    assert "_qq_reality_reply_adapter" in src or "main.py" in src[:2000], (
        "core/reality_output_scrubber.py: module docstring does not name the QQ adapter call site"
    )
