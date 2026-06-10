"""
R8-D: character_growth / legacy slow_queue handler 退役审计。

Coverage:
1.  post_process 主链入队 trait_tracker_update（生产路径存在）
2.  post_process 主链不入队 consolidate_to_growth
3.  character_growth.update() 在 core/ 内无生产调用方（DEAD_CANDIDATE 确认）
4.  legacy handler mid_term_append / episodic_compress 仍注册（DLQ 保护）
5.  consolidate_to_growth 未注册为 handler（DEAD 确认）
6.  LEGACY_TASK_TYPES 包含两个 LEGACY_COMPAT 类型（R8-E1 移除 consolidate_to_growth 后）
7.  LEGACY_TASK_TYPES 不包含任何活跃任务类型
8.  author_note_rotator 不导入 character_growth
9.  character_growth.update() 内的 trait_state 写路径存在 char_id 缺省问题（死代码残留记录）
10. DLQ 目录无 legacy task 文件（或目录不存在，输出统计）
11. fixation_pipeline 不调用 character_growth.update()
12. 唯一使用 character_growth 的生产代码是 tool_dispatcher 的 .load() 调用

审计结论（快速参考）：
  character_growth.update()  → DEAD_CANDIDATE （无生产调用方）
  consolidate_to_growth      → REMOVED R8-E1  （已从 LEGACY_TASK_TYPES 删除；无 handler，无 enqueue，无 DLQ 存量）
  mid_term_append            → LEGACY_COMPAT  （handler 注册，DLQ 保护，无新 enqueue）
  episodic_compress          → LEGACY_COMPAT  （handler 注册，DLQ 保护，无新 enqueue）
  _handler_mid_term_append   → LEGACY_COMPAT  （同上）
  _handler_episodic_compress → LEGACY_COMPAT  （同上）
  LEGACY_TASK_TYPES          → ACTIVE         （time_based DLQ monitor 使用）
  character_growth.load()    → ACTIVE         （tool_dispatcher get_growth 工具调用）
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
CORE_ROOT = PROJECT_ROOT / "core"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _core_py_files():
    """Yield all .py files under core/ (not in test subdirs)."""
    for p in CORE_ROOT.rglob("*.py"):
        if not any(seg in ("test", "tests") for seg in p.parts):
            yield p


def _source(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. post_process 主链入队 trait_tracker_update
# ---------------------------------------------------------------------------

def test_post_process_enqueues_trait_tracker_update():
    """
    The production post_process source must contain an enqueue call for
    'trait_tracker_update' — confirming R8-B wiring is still present.
    """
    src = _source("core/pipeline.py")
    import core.pipeline as _p
    post_src = inspect.getsource(_p.Pipeline.post_process)
    assert '"trait_tracker_update"' in post_src or "'trait_tracker_update'" in post_src, (
        "post_process must enqueue 'trait_tracker_update'"
    )


# ---------------------------------------------------------------------------
# 2. post_process 主链不入队 consolidate_to_growth
# ---------------------------------------------------------------------------

def test_post_process_does_not_enqueue_consolidate_to_growth():
    """
    post_process source must NOT contain any enqueue call for
    'consolidate_to_growth' — it must remain a dead/legacy-DLQ-only name.
    """
    import core.pipeline as _p
    post_src = inspect.getsource(_p.Pipeline.post_process)
    assert "consolidate_to_growth" not in post_src, (
        "post_process must never enqueue 'consolidate_to_growth'"
    )


# ---------------------------------------------------------------------------
# 3. character_growth.update() 在 core/ 内无生产调用方
# ---------------------------------------------------------------------------

def test_character_growth_update_has_no_production_callers():
    """
    No core/ file other than character_growth.py itself may call
    character_growth.update() or CharacterGrowth().update().
    Confirms DEAD_CANDIDATE status: the trait_state write inside update()
    is dead code and cannot trigger the char_id=None bug in production.

    Comment lines (starting with #) and docstring lines are excluded from
    the scan so that audit comments like "detached from character_growth.update()"
    don't produce false positives.
    """
    SELF = "core/memory/character_growth.py"
    call_pattern = re.compile(r"character_growth\s*\.\s*update\s*\(")

    violating: list[str] = []
    for path in _core_py_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel == SELF:
            continue
        src = path.read_text(encoding="utf-8")
        # Only check non-comment, non-docstring lines
        code_lines = [
            line for line in src.splitlines()
            if not line.lstrip().startswith("#")
            and not line.lstrip().startswith('"""')
            and not line.lstrip().startswith("'''")
        ]
        if call_pattern.search("\n".join(code_lines)):
            violating.append(rel)

    assert not violating, (
        "character_growth.update() must have zero production callers outside "
        f"its own module. Found callers: {violating}"
    )


# ---------------------------------------------------------------------------
# 4. legacy handler mid_term_append / episodic_compress 仍注册
# ---------------------------------------------------------------------------

def test_legacy_handlers_mid_term_append_and_episodic_compress_registered(monkeypatch):
    """
    After register_slow_handlers(), both mid_term_append and episodic_compress
    must be registered — they protect against DLQ residual task retry.
    (LEGACY_COMPAT: do NOT delete until 30-day TTL observation completes.)
    """
    import core.post_process.slow_queue as sq
    sq._handlers = {}

    async def _stub(_): ...
    monkeypatch.setattr(
        "core.memory.fixation_pipeline.handler_capture_turn_retry", _stub, raising=False
    )
    monkeypatch.setattr(
        "core.memory.fixation_pipeline.handler_summarize_to_midterm", _stub, raising=False
    )
    monkeypatch.setattr(
        "core.memory.fixation_pipeline.handler_reflect_to_episodic", _stub, raising=False
    )
    monkeypatch.setattr(
        "core.memory.fixation_pipeline.handler_consolidate_to_identity", _stub, raising=False
    )

    from core.pipeline import register_slow_handlers
    register_slow_handlers()

    assert "mid_term_append" in sq._handlers, (
        "mid_term_append handler must stay registered (DLQ LEGACY_COMPAT)"
    )
    assert "episodic_compress" in sq._handlers, (
        "episodic_compress handler must stay registered (DLQ LEGACY_COMPAT)"
    )


# ---------------------------------------------------------------------------
# 5. consolidate_to_growth 未注册为 handler（DEAD 确认）
# ---------------------------------------------------------------------------

def test_consolidate_to_growth_not_registered_as_handler(monkeypatch):
    """
    After register_slow_handlers(), 'consolidate_to_growth' must NOT be
    in the handler registry. It was never a live handler (pre-S5 code artifact).
    R8-E1: also removed from LEGACY_TASK_TYPES.
    """
    import core.post_process.slow_queue as sq
    sq._handlers = {}

    async def _stub(_): ...
    for name in (
        "handler_capture_turn_retry",
        "handler_summarize_to_midterm",
        "handler_reflect_to_episodic",
        "handler_consolidate_to_identity",
    ):
        monkeypatch.setattr(f"core.memory.fixation_pipeline.{name}", _stub, raising=False)

    from core.pipeline import register_slow_handlers
    register_slow_handlers()

    assert "consolidate_to_growth" not in sq._handlers, (
        "'consolidate_to_growth' must never be registered as a slow_queue handler "
        "(REMOVED R8-E1: pre-S5 code artifact; no handler, no enqueue, no DLQ files)"
    )


# ---------------------------------------------------------------------------
# 6. LEGACY_TASK_TYPES 包含两个 LEGACY_COMPAT 类型；不含 consolidate_to_growth（R8-E1 移除）
# ---------------------------------------------------------------------------

def test_legacy_task_types_contains_two_compat_names():
    """R8-E1: LEGACY_TASK_TYPES contains mid_term_append + episodic_compress only.
    consolidate_to_growth is removed (DEAD name-only residue, never registered,
    no enqueue, no DLQ files).
    """
    from core.post_process.slow_queue import LEGACY_TASK_TYPES
    assert "mid_term_append" in LEGACY_TASK_TYPES
    assert "episodic_compress" in LEGACY_TASK_TYPES
    assert "consolidate_to_growth" not in LEGACY_TASK_TYPES
    assert isinstance(LEGACY_TASK_TYPES, frozenset)


# ---------------------------------------------------------------------------
# 7. LEGACY_TASK_TYPES 不包含任何活跃任务类型
# ---------------------------------------------------------------------------

def test_legacy_task_types_excludes_live_task_types():
    """
    Active task types must NOT be in LEGACY_TASK_TYPES.
    If a live type ended up in the legacy set, the DLQ monitor would
    expire valid in-flight tasks.
    """
    from core.post_process.slow_queue import LEGACY_TASK_TYPES

    live_types = {
        "capture_turn_retry",
        "summarize_to_midterm",
        "reflect_to_episodic",
        "consolidate_to_identity",
        "consistency_check",
        "user_profile_update",
        "trait_tracker_update",
    }

    overlap = live_types & LEGACY_TASK_TYPES
    assert not overlap, (
        f"Live task types found in LEGACY_TASK_TYPES — would cause DLQ expiry of "
        f"valid tasks: {overlap}"
    )


# ---------------------------------------------------------------------------
# 8. author_note_rotator 不导入 character_growth
# ---------------------------------------------------------------------------

def test_author_note_rotator_does_not_import_character_growth():
    """
    author_note_rotator must not import character_growth — its trait_state
    read now goes directly through paths.trait_state(char_id=char_id) (R8-C).
    """
    src = _source("core/author_note_rotator.py")
    assert "character_growth" not in src, (
        "author_note_rotator must not reference character_growth; "
        "it now reads trait_state via paths.trait_state(char_id=...) directly"
    )


# ---------------------------------------------------------------------------
# 9. character_growth.update() 已于 R8-E2 删除（trait_state 死代码缺陷已随之消除）
# ---------------------------------------------------------------------------

def test_character_growth_update_has_no_char_id_in_trait_state_call_dead_code_note():
    """
    R8-E2: character_growth.update() has been deleted.
    The latent trait_state() bug (no char_id kwarg) is gone with it.
    This test verifies update() no longer appears as a defined function
    in the module (neither sync nor async).
    """
    src = _source("core/memory/character_growth.py")
    tree = ast.parse(src)
    func_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "update" not in func_names, (
        "character_growth.update() was supposed to be deleted in R8-E2. "
        "If it was re-added, it must include char_id= in the trait_state() call."
    )
    assert "should_update" not in func_names, (
        "character_growth.should_update() was supposed to be deleted in R8-E2."
    )


# ---------------------------------------------------------------------------
# 10. DLQ 目录无 legacy task 文件（统计输出）
# ---------------------------------------------------------------------------

def test_dlq_contains_no_legacy_task_files():
    """
    Scan data/logs/dead_letter_queue/ for legacy task type files.
    If the directory does not exist, pass (no DLQ samples).
    If legacy files are found, the test fails with the list so the operator
    can decide whether 30-day TTL has elapsed for those files.
    """
    from core.post_process.slow_queue import LEGACY_TASK_TYPES

    dlq_dir = PROJECT_ROOT / "data" / "logs" / "dead_letter_queue"
    if not dlq_dir.exists():
        # No production DLQ samples — clean state
        return

    legacy_found: list[str] = []
    for f in dlq_dir.rglob("*.json"):
        # DLQ filenames: {ms_ts}_{task_type}.json
        stem = f.stem
        parts = stem.split("_", 1)
        if len(parts) == 2:
            task_type = parts[1]
            if task_type in LEGACY_TASK_TYPES:
                legacy_found.append(f.name)

    assert not legacy_found, (
        f"Legacy DLQ files found — check 30-day TTL status before deletion:\n"
        + "\n".join(f"  {n}" for n in sorted(legacy_found))
    )


# ---------------------------------------------------------------------------
# 11. fixation_pipeline 不调用 character_growth.update()
# ---------------------------------------------------------------------------

def test_fixation_pipeline_does_not_call_character_growth_update():
    """
    fixation_pipeline.py must not call character_growth.update() or
    import character_growth at all — it now writes via consolidate_to_identity
    → user_identity, not via the legacy growth file path.
    """
    src = _source("core/memory/fixation_pipeline.py")
    assert "character_growth" not in src, (
        "fixation_pipeline.py must not reference character_growth; "
        "consolidate path now goes to user_identity, not character_growth"
    )


# ---------------------------------------------------------------------------
# 12. 唯一使用 character_growth 的生产代码是 tool_dispatcher 的 .load() 调用
# ---------------------------------------------------------------------------

def test_only_tool_dispatcher_calls_character_growth_load():
    """
    Among core/ files, only tool_dispatcher.py may call character_growth.load().
    Other files may reference "character_growth" as a string, path-method, or
    docstring — those are path/registry/documentation references and are fine.
    The live data-read call (.load()) must be isolated to the tool dispatch layer.
    """
    EXPECTED_FILE = "core/tool_dispatcher.py"
    SELF = "core/memory/character_growth.py"

    load_pattern = re.compile(r"character_growth\s*\.\s*load\s*\(")

    callers: list[str] = []
    for path in _core_py_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel == SELF:
            continue
        src = path.read_text(encoding="utf-8")
        # Only scan non-comment lines
        code_lines = "\n".join(
            line for line in src.splitlines()
            if not line.lstrip().startswith("#")
        )
        if load_pattern.search(code_lines):
            callers.append(rel)

    assert callers == [EXPECTED_FILE], (
        f"character_growth.load() must only be called from tool_dispatcher.py; "
        f"found callers: {callers}"
    )
