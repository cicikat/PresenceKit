"""
tests/test_r6b_reality_scrub_contract.py — R6-B: Reality scrub authority contract

Pins the R6-A audit conclusions as explicit ownership contracts so that future
additions cannot accidentally bypass the scrub chain.

Contract summary (from R6-A audit):
  capture_turn    — REALITY_MEMORY authority scrub point (final, must not be removed)
  main.py (×2)   — QQ inlet pre-scrub (defense-in-depth, idempotent with capture_turn)
  turn_sink       — non-QQ inlet pre-scrub (defense-in-depth, idempotent with capture_turn)
  DREAM paths     — must never call scrub_reality_output_text
  REALITY_VISIBLE — uses strip_render_tags only (no reality scrub)

Test structure:
  C1–C4  Static ownership: short_term.append / event_log.append only in capture_turn
  C5     Pipeline routes to capture_turn, not direct memory writes
  C6     Dream files do not import reality_output_scrubber
  C7–C8  scrub_reality_output_text idempotency
  C9     reality_output_scrubber module docstring names the correct call sites
  C10    REALITY_VISIBLE does not call reality scrub
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent


# ── static helpers ─────────────────────────────────────────────────────────────

def _src(relpath: str) -> str:
    return (_ROOT / relpath).read_text(encoding="utf-8")


def _lines(relpath: str) -> list[str]:
    return (_ROOT / relpath).read_text(encoding="utf-8").splitlines()


def _grep(relpath: str, symbol: str) -> list[int]:
    """1-based line numbers where symbol appears (text search)."""
    return [i + 1 for i, ln in enumerate(_lines(relpath)) if symbol in ln]


def _function_body_lines(src: str, func_name: str) -> list[str]:
    """
    Return all source lines inside the named top-level or module-level function.
    Stops at the next function/class at the same indentation.
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
            # Stop at next top-level def/class at same or lesser indent
            if stripped and indent <= base_indent and (
                stripped.startswith("def ") or stripped.startswith("async def ") or
                stripped.startswith("class ") or stripped.startswith("@")
            ) and f"def {func_name}(" not in ln:
                break
            result.append(ln)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# C1. short_term.append in production code only inside capture_turn
# ═══════════════════════════════════════════════════════════════════════════════

def test_c1_short_term_append_only_in_capture_turn():
    """
    C1: In production core code, short_term.append must only be called from
    within capture_turn.  Any other production call site would bypass the
    authority scrub and allow raw LLM text into history.
    """
    # Scan all .py files under core/ (not tests/)
    violations: list[str] = []
    for py_file in (_ROOT / "core").rglob("*.py"):
        relpath = py_file.relative_to(_ROOT).as_posix()
        src = py_file.read_text(encoding="utf-8")

        # Only look for actual call expressions ("(" present), not docstring references
        if "short_term.append(" not in src:
            continue

        lines = src.splitlines()
        for i, ln in enumerate(lines, 1):
            stripped = ln.strip()
            # Skip comments
            if stripped.startswith("#"):
                continue
            # Require "(" to distinguish call from docstring text reference
            if "short_term.append(" in ln:
                # The only allowed location is inside capture_turn in fixation_pipeline.py
                if relpath != "core/memory/fixation_pipeline.py":
                    violations.append(f"{relpath}:{i}: {ln.strip()}")

    assert not violations, (
        "short_term.append called outside capture_turn in production core/ code — "
        "new callers must route through capture_turn to guarantee authority scrub:\n"
        + "\n".join(violations)
    )


def test_c1b_capture_turn_is_the_short_term_write_site():
    """C1b: fixation_pipeline.capture_turn body must contain short_term.append."""
    src = _src("core/memory/fixation_pipeline.py")
    body = _function_body_lines(src, "capture_turn")
    body_text = "\n".join(body)
    assert "short_term.append" in body_text, (
        "capture_turn no longer calls short_term.append — "
        "the ownership contract is broken"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C2. event_log.append in production code only inside capture_turn
# ═══════════════════════════════════════════════════════════════════════════════

def test_c2_event_log_append_only_in_capture_turn():
    """
    C2: In production core code, event_log.append (assistant-turn write) must only
    be called from within capture_turn.  Direct calls elsewhere would bypass the
    authority scrub.
    """
    violations: list[str] = []
    for py_file in (_ROOT / "core").rglob("*.py"):
        relpath = py_file.relative_to(_ROOT).as_posix()
        # Allow event_log.py itself (function definition) and fixation_pipeline.py
        if relpath in ("core/memory/event_log.py", "core/memory/fixation_pipeline.py"):
            continue
        src = py_file.read_text(encoding="utf-8")
        if "event_log.append" not in src:
            continue
        lines = src.splitlines()
        for i, ln in enumerate(lines, 1):
            stripped = ln.strip()
            if stripped.startswith("#"):
                continue
            if "event_log.append" in ln:
                violations.append(f"{relpath}:{i}: {ln.strip()}")

    assert not violations, (
        "event_log.append called outside capture_turn in production core/ code — "
        "new callers must route through capture_turn:\n"
        + "\n".join(violations)
    )


def test_c2b_capture_turn_is_the_event_log_write_site():
    """C2b: fixation_pipeline.capture_turn body must contain event_log.append."""
    src = _src("core/memory/fixation_pipeline.py")
    body = _function_body_lines(src, "capture_turn")
    body_text = "\n".join(body)
    assert "event_log.append" in body_text, (
        "capture_turn no longer calls event_log.append — ownership contract broken"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C3. main.py does not call short_term.append or event_log.append directly
# ═══════════════════════════════════════════════════════════════════════════════

def test_c3_main_py_no_direct_memory_append():
    """
    C3: main.py must not call short_term.append or event_log.append directly.
    The QQ path routes through post_process → capture_turn.  A direct call
    would skip the authority scrub.
    """
    src = _src("main.py")
    lines = src.splitlines()
    violations: list[str] = []
    for i, ln in enumerate(lines, 1):
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        if "short_term.append" in ln or "event_log.append" in ln:
            violations.append(f"main.py:{i}: {ln.strip()}")

    assert not violations, (
        "main.py calls short_term.append or event_log.append directly — "
        "must route through pipeline.post_process → capture_turn:\n"
        + "\n".join(violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C4. pipeline.py post_process calls capture_turn (not short_term/event_log directly)
# ═══════════════════════════════════════════════════════════════════════════════



def test_c4b_effect_owner_no_direct_short_term_append():
    """
    C4b: the effect owner must not bypass capture_turn for history/event writes.
    All writes must route through capture_turn.
    """
    lines = _lines("core/post_process/turn_effects.py")
    violations: list[str] = []
    for i, ln in enumerate(lines, 1):
        stripped = ln.strip()
        # Skip comments
        if stripped.startswith("#"):
            continue
        if "short_term.append(" in ln or "event_log.append(" in ln:
            violations.append(f"core/post_process/turn_effects.py:{i}: {ln.strip()}")

    assert not violations, (
        "core/pipeline.py calls short_term.append or event_log.append directly — "
        "must route through capture_turn:\n"
        + "\n".join(violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C5. capture_turn still calls scrub_reality_output_text (authority guard intact)
# ═══════════════════════════════════════════════════════════════════════════════

def test_c5_capture_turn_has_authority_scrub():
    """
    C5: capture_turn must still call scrub_reality_output_text.
    This is the REALITY_MEMORY authority scrub — if removed, action/narration can
    enter short_term and event_log whenever upstream pre-scrubs are skipped.
    """
    src = _src("core/memory/fixation_pipeline.py")
    body = _function_body_lines(src, "capture_turn")
    body_text = "\n".join(body)
    assert "scrub_reality_output_text" in body_text, (
        "capture_turn no longer calls scrub_reality_output_text — "
        "the REALITY_MEMORY authority scrub guard is missing"
    )


def test_c5b_scrub_called_before_short_term_write():
    """
    C5b: In capture_turn, scrub must be assigned to _scrubbed_reply before the
    short_term.append calls.  Ensures authority scrub happens before writes.
    """
    src = _src("core/memory/fixation_pipeline.py")
    body = _function_body_lines(src, "capture_turn")
    body_text = "\n".join(body)

    scrub_pos = body_text.find("_scrubbed_reply = _scrub(")
    st_pos = body_text.find("short_term.append(")
    assert scrub_pos != -1, "capture_turn: _scrubbed_reply assignment not found"
    assert st_pos != -1, "capture_turn: short_term.append not found"
    assert scrub_pos < st_pos, (
        "capture_turn: scrub assignment appears AFTER short_term.append — "
        "scrub must happen before write"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C6. Dream files do not import reality_output_scrubber
# ═══════════════════════════════════════════════════════════════════════════════

_DREAM_FILES = [
    "core/dream/dream_pipeline.py",
    "admin/routers/dream.py",
] + [path.relative_to(_ROOT).as_posix()
     for path in sorted((_ROOT / "admin/routers").glob("dream_*.py"))]


@pytest.mark.parametrize("relpath", _DREAM_FILES)
def test_c6_dream_no_reality_scrubber(relpath: str):
    """
    C6: Dream files must never import reality_output_scrubber or call
    scrub_reality_output_text.  Dream content has its own output path and must
    bypass the reality scrub entirely.
    """
    src = _src(relpath)
    assert "reality_output_scrubber" not in src, (
        f"{relpath}: imports reality_output_scrubber — dream output must not "
        "be processed by the reality scrubber"
    )
    assert "scrub_reality_output_text" not in src, (
        f"{relpath}: calls scrub_reality_output_text — dream path violation"
    )


def test_c6b_dream_no_capture_turn():
    """
    C6b: dream_pipeline.py must not call capture_turn.
    Dream turns must not write to short_term via the reality memory chain.
    A docstring that *mentions* capture_turn to document the isolation contract is fine;
    an actual call expression (capture_turn(...)) is not.
    """
    lines = _lines("core/dream/dream_pipeline.py")
    violations: list[str] = []
    for i, ln in enumerate(lines, 1):
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        # Require "(" to distinguish an actual call from a docstring reference
        if "capture_turn(" in ln:
            violations.append(f"core/dream/dream_pipeline.py:{i}: {ln.strip()}")

    assert not violations, (
        "dream_pipeline.py calls capture_turn() — "
        "dream turns must not enter the reality MEMORY chain:\n"
        + "\n".join(violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C7. scrub_reality_output_text idempotency
# ═══════════════════════════════════════════════════════════════════════════════

_IDEMPOTENCY_SAMPLES = [
    # plain dialogue
    "你好，我在。",
    # CJK bracket action + dialogue
    "（轻轻抬起头）\n你好，我在。",
    # EN bracket action
    "(she tilts her head)\n好的。",
    # Markdown do action
    "*抬起头*\n很好。",
    # Markdown feel
    "_心里有点紧张_\n明白了。",
    # env blockquote
    "> 月光透过窗帘\n今晚不错。",
    # narration start
    "她低头看着你。\n我懂了。",
    # action word
    "轻轻摸了摸耳朵。\n不欺负了。",
    # already clean
    "只有这一行对话。",
    # compound multi-line
    "好好好，不欺负了。\n（在你发顶落下一个很轻的吻）\n睡吧。",
    # code block must be preserved
    "看看这个代码：\n```\nprint('hello')\n```\n就是这样。",
    # None input
    None,
]


@pytest.mark.parametrize("text", _IDEMPOTENCY_SAMPLES)
def test_c7_scrub_idempotent(text):
    """
    C7: scrub_reality_output_text(scrub(x)) == scrub(x) for all inputs.
    The defense-in-depth double-scrub pattern (upstream pre-scrub + capture_turn
    scrub) is only safe if scrub is idempotent.
    """
    from core.reality_output_scrubber import scrub_reality_output_text as _scrub

    once = _scrub(text)
    twice = _scrub(once)
    assert once == twice, (
        f"scrub_reality_output_text is NOT idempotent:\n"
        f"  input:       {text!r}\n"
        f"  first pass:  {once!r}\n"
        f"  second pass: {twice!r}"
    )


def test_c7b_alias_idempotent():
    """C7b: scrub_reality_prompt_context_text (alias) is also idempotent."""
    from core.reality_output_scrubber import (
        scrub_reality_output_text,
        scrub_reality_prompt_context_text,
    )
    sample = "（她伸手）\n今天天气不错。"
    assert scrub_reality_prompt_context_text(sample) == scrub_reality_output_text(sample), (
        "Alias scrub_reality_prompt_context_text diverges from scrub_reality_output_text"
    )
    twice = scrub_reality_prompt_context_text(scrub_reality_prompt_context_text(sample))
    once = scrub_reality_prompt_context_text(sample)
    assert once == twice, "scrub_reality_prompt_context_text is NOT idempotent"


# ═══════════════════════════════════════════════════════════════════════════════
# C8. REALITY_VISIBLE path: no reality scrub, only strip_render_tags
# ═══════════════════════════════════════════════════════════════════════════════

def test_c8_fanout_no_reality_scrub():
    """
    C8: turn_sink._fanout (REALITY_VISIBLE path) must NOT call
    scrub_reality_output_text.  Visible output preserves action descriptions for
    chat texture; only REALITY_MEMORY strips them.
    """
    src = _src("core/turn_sink.py")
    lines = src.splitlines()
    in_fanout = False
    violations: list[str] = []
    for i, ln in enumerate(lines, 1):
        if "async def _fanout(" in ln or "def _fanout(" in ln:
            in_fanout = True
        if in_fanout and ln.startswith("async def ") and "_fanout" not in ln:
            break
        if in_fanout and ln.startswith("def ") and "_fanout" not in ln:
            break
        if in_fanout and "scrub_reality_output_text" in ln:
            stripped = ln.strip()
            if not stripped.startswith("#"):
                violations.append(f"core/turn_sink.py:{i}: {ln.strip()}")

    assert not violations, (
        "turn_sink._fanout calls scrub_reality_output_text — "
        "visible output must not apply reality scrub:\n"
        + "\n".join(violations)
    )


def test_c8b_fanout_uses_strip_render_tags():
    """C8b: turn_sink._fanout must still call strip_render_tags for REALITY_VISIBLE."""
    src = _src("core/turn_sink.py")
    lines = src.splitlines()
    in_fanout = False
    found = False
    for ln in lines:
        if "async def _fanout(" in ln or "def _fanout(" in ln:
            in_fanout = True
        if in_fanout and ln.startswith("async def ") and "_fanout" not in ln:
            break
        if in_fanout and ln.startswith("def ") and "_fanout" not in ln:
            break
        if in_fanout and "strip_render_tags" in ln:
            found = True

    assert found, (
        "turn_sink._fanout no longer calls strip_render_tags — "
        "render tags may leak to visible channel output"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C9. reality_output_scrubber module documents the correct call sites
# ═══════════════════════════════════════════════════════════════════════════════

def test_c9_scrubber_module_docstring_names_capture_turn():
    """
    C9: reality_output_scrubber.py module docstring must name capture_turn as a
    call site, confirming the authority ownership is documented in the module itself.
    """
    src = _src("core/reality_output_scrubber.py")
    # Module-level docstring must mention capture_turn
    assert "capture_turn" in src[:2000], (
        "core/reality_output_scrubber.py module header does not mention capture_turn — "
        "update the call-site list to reflect the authority contract"
    )


def test_c9b_scrubber_module_docstring_names_dream_exclusion():
    """
    C9b: reality_output_scrubber.py module docstring must state that Dream content
    bypasses this module.
    """
    src = _src("core/reality_output_scrubber.py")
    assert "dream" in src[:2000].lower(), (
        "core/reality_output_scrubber.py module header does not mention Dream exclusion"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C10. Pre-scrub callers are labeled as defense-in-depth (not sole authority)
# ═══════════════════════════════════════════════════════════════════════════════

def test_c10a_main_handle_message_comment_mentions_capture_turn():
    """
    C10a: The scrub comment in main.py handle_message must mention capture_turn,
    making clear this is an upstream pre-scrub, not the authority.
    """
    lines = _lines("main.py")
    # Find the scrub block in handle_message (before _reply_with_tool_result)
    in_handle = False
    scrub_comment_region: list[str] = []
    for ln in lines:
        if "async def handle_message(" in ln:
            in_handle = True
        if in_handle and "async def _reply_with_tool_result(" in ln:
            break
        if in_handle and "scrub_reality_output_text" in ln:
            # Capture surrounding comment lines
            pass
        if in_handle and "capture_turn" in ln and ln.strip().startswith("#"):
            scrub_comment_region.append(ln)

    assert scrub_comment_region, (
        "main.py handle_message: pre-scrub comment does not mention capture_turn — "
        "add a note that capture_turn is the authority"
    )


def test_c10b_main_tool_reply_comment_mentions_capture_turn():
    """
    C10b: The scrub comment in main.py _reply_with_tool_result must mention
    capture_turn.
    """
    lines = _lines("main.py")
    in_func = False
    found = False
    for ln in lines:
        if "async def _reply_with_tool_result(" in ln:
            in_func = True
        if in_func and "async def " in ln and "_reply_with_tool_result" not in ln:
            break
        if in_func and "capture_turn" in ln and ln.strip().startswith("#"):
            found = True

    assert found, (
        "main.py _reply_with_tool_result: pre-scrub comment does not mention "
        "capture_turn — add a note that capture_turn is the authority"
    )


def test_c10c_turn_sink_comment_mentions_capture_turn():
    """
    C10c: The scrub comment in turn_sink.record_assistant_turn must mention
    capture_turn.
    """
    lines = _lines("core/turn_sink.py")
    in_func = False
    found = False
    for ln in lines:
        if "async def record_assistant_turn(" in ln:
            in_func = True
        if in_func and "async def " in ln and "record_assistant_turn" not in ln:
            break
        if in_func and "capture_turn" in ln and ln.strip().startswith("#"):
            found = True

    assert found, (
        "core/turn_sink.py record_assistant_turn: pre-scrub comment does not mention "
        "capture_turn — add a note that capture_turn is the authority"
    )
