"""
R8-E2: character_growth write path retirement tests.

Coverage:
1.  character_growth.load() 仍可用，返回字符串（get_growth 工具读路径不回归）
2.  character_growth.update 不再存在（函数已删除）
3.  character_growth.should_update 不再存在（函数已删除）
4.  trait_tracker_update 是 trait_state 当前写入路径（不依赖 character_growth.update）
5.  author_note_rotator 不导入 character_growth
6.  R3-CI: character_growth.py 不再含 char_id="yexuan" 默认参数
7.  production code 不再调用 character_growth.update / should_update
8.  mid_term_append / episodic_compress handler 仍保留（LEGACY_COMPAT，不在本包删除）
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
CORE_ROOT = PROJECT_ROOT / "core"


def _source(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


def _core_py_files():
    for p in CORE_ROOT.rglob("*.py"):
        if not any(seg in ("test", "tests") for seg in p.parts):
            yield p


# ---------------------------------------------------------------------------
# 1. load() 仍可用
# ---------------------------------------------------------------------------

def test_load_returns_empty_string_when_file_missing(tmp_path, monkeypatch):
    """character_growth.load() must return '' when no growth file exists."""
    from core import memory as _mem
    import core.memory.character_growth as cg

    # Patch get_paths to return a sandbox-like object pointing to tmp_path
    class _FakePaths:
        def character_growth(self):
            p = tmp_path / "character_growth"
            p.mkdir(parents=True, exist_ok=True)
            return p

    monkeypatch.setattr("core.memory.character_growth.get_paths", _FakePaths)
    result = cg.load("叶瑄", "test_uid_999")
    assert result == "", f"Expected '' for missing file, got {result!r}"


def test_load_returns_content_when_file_exists(tmp_path, monkeypatch):
    """character_growth.load() must return file content when growth file exists."""
    import core.memory.character_growth as cg

    growth_dir = tmp_path / "character_growth"
    growth_dir.mkdir(parents=True, exist_ok=True)
    growth_file = growth_dir / "叶瑄_test_uid_888.md"
    growth_file.write_text("## 用户特点\n- 夜猫子", encoding="utf-8")

    class _FakePaths:
        def character_growth(self):
            return growth_dir

    monkeypatch.setattr("core.memory.character_growth.get_paths", _FakePaths)
    result = cg.load("叶瑄", "test_uid_888")
    assert "夜猫子" in result, f"Expected file content, got {result!r}"


# ---------------------------------------------------------------------------
# 2. update 不再存在
# ---------------------------------------------------------------------------

def test_character_growth_update_function_does_not_exist():
    """character_growth.update must not exist as a defined function (R8-E2 deleted it)."""
    import core.memory.character_growth as cg

    assert not hasattr(cg, "update"), (
        "character_growth.update() must not exist after R8-E2 retirement. "
        "If it was re-added, audit trait_state() char_id plumbing."
    )


def test_character_growth_update_not_in_ast():
    """AST check: update must not appear as a FunctionDef in character_growth.py."""
    src = _source("core/memory/character_growth.py")
    tree = ast.parse(src)
    func_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "update" not in func_names, (
        "character_growth.update() must not be defined (R8-E2)."
    )


# ---------------------------------------------------------------------------
# 3. should_update 不再存在
# ---------------------------------------------------------------------------

def test_character_growth_should_update_function_does_not_exist():
    """character_growth.should_update must not exist as a defined function (R8-E2 deleted it)."""
    import core.memory.character_growth as cg

    assert not hasattr(cg, "should_update"), (
        "character_growth.should_update() must not exist after R8-E2 retirement."
    )


def test_character_growth_should_update_not_in_ast():
    """AST check: should_update must not appear as a FunctionDef in character_growth.py."""
    src = _source("core/memory/character_growth.py")
    tree = ast.parse(src)
    func_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "should_update" not in func_names, (
        "character_growth.should_update() must not be defined (R8-E2)."
    )


# ---------------------------------------------------------------------------
# 4. trait_tracker_update 是 trait_state 当前写入路径
# ---------------------------------------------------------------------------

def test_trait_tracker_update_handler_registered(monkeypatch):
    """register_slow_handlers() must register trait_tracker_update (R8-B wiring still present)."""
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

    assert "trait_tracker_update" in sq._handlers, (
        "trait_tracker_update must be registered via register_slow_handlers() (R8-B)"
    )


def test_trait_tracker_update_handler_does_not_call_character_growth():
    """_handler_trait_tracker_update must not import or call character_growth (comments excluded)."""
    import inspect
    import core.pipeline as _p
    src = inspect.getsource(_p._handler_trait_tracker_update)
    # Exclude comment lines — comments referencing old detachment note are fine
    code_lines = [
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    assert "character_growth" not in code_only, (
        "_handler_trait_tracker_update must not import or call character_growth in non-comment code (R8-E2)"
    )


# ---------------------------------------------------------------------------
# 5. author_note_rotator 不导入 character_growth
# ---------------------------------------------------------------------------

def test_author_note_rotator_does_not_reference_character_growth():
    """author_note_rotator must not import or reference character_growth."""
    src = _source("core/author_note_rotator.py")
    assert "character_growth" not in src, (
        "author_note_rotator must not reference character_growth (R8-C / R8-E2)"
    )


# ---------------------------------------------------------------------------
# 6. R3-CI: character_growth.py 不再含 char_id="yexuan" 默认参数
# ---------------------------------------------------------------------------

def test_character_growth_has_no_yexuan_char_id_default():
    """After R8-E2, character_growth.py must have no char_id='yexuan' parameter defaults."""
    src = _source("core/memory/character_growth.py")
    tree = ast.parse(src)

    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        all_pos = args.posonlyargs + args.args
        offset = len(all_pos) - len(args.defaults)
        for i, default in enumerate(args.defaults):
            arg = all_pos[offset + i]
            if (
                arg.arg in ("char_id", "character_id")
                and isinstance(default, ast.Constant)
                and default.value == "yexuan"
            ):
                violations.append(default.lineno)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is None:
                continue
            if (
                arg.arg in ("char_id", "character_id")
                and isinstance(default, ast.Constant)
                and default.value == "yexuan"
            ):
                violations.append(default.lineno)

    assert not violations, (
        f"character_growth.py still has char_id='yexuan' defaults at lines {violations}. "
        "R8-E2 should have removed all such defaults (update() deleted)."
    )


# ---------------------------------------------------------------------------
# 7. production code 不再调用 character_growth.update / should_update
# ---------------------------------------------------------------------------

def test_no_production_caller_of_character_growth_update():
    """No core/ file may call character_growth.update() (R8-E2: function deleted)."""
    SELF = "core/memory/character_growth.py"
    call_pattern = re.compile(r"character_growth\s*\.\s*update\s*\(")

    violating: list[str] = []
    for path in _core_py_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel == SELF:
            continue
        src = path.read_text(encoding="utf-8")
        code_lines = [
            line for line in src.splitlines()
            if not line.lstrip().startswith("#")
            and not line.lstrip().startswith('"""')
            and not line.lstrip().startswith("'''")
        ]
        if call_pattern.search("\n".join(code_lines)):
            violating.append(rel)

    assert not violating, (
        f"character_growth.update() callers found in production code: {violating}. "
        "The function was deleted in R8-E2."
    )


def test_no_production_caller_of_character_growth_should_update():
    """No core/ file may call character_growth.should_update() (R8-E2: function deleted)."""
    SELF = "core/memory/character_growth.py"
    call_pattern = re.compile(r"character_growth\s*\.\s*should_update\s*\(")

    violating: list[str] = []
    for path in _core_py_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel == SELF:
            continue
        src = path.read_text(encoding="utf-8")
        code_lines = [
            line for line in src.splitlines()
            if not line.lstrip().startswith("#")
            and not line.lstrip().startswith('"""')
            and not line.lstrip().startswith("'''")
        ]
        if call_pattern.search("\n".join(code_lines)):
            violating.append(rel)

    assert not violating, (
        f"character_growth.should_update() callers found in production code: {violating}. "
        "The function was deleted in R8-E2."
    )


# ---------------------------------------------------------------------------
# 8. mid_term_append / episodic_compress handler 仍保留（LEGACY_COMPAT）
# ---------------------------------------------------------------------------

def test_legacy_compat_handlers_still_registered(monkeypatch):
    """
    mid_term_append and episodic_compress handlers must remain registered
    (LEGACY_COMPAT DLQ protection — not removed in R8-E2).
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

    assert "mid_term_append" in sq._handlers, (
        "mid_term_append handler must NOT be removed in R8-E2 (DLQ LEGACY_COMPAT)"
    )
    assert "episodic_compress" in sq._handlers, (
        "episodic_compress handler must NOT be removed in R8-E2 (DLQ LEGACY_COMPAT)"
    )
