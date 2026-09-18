"""
tests/test_r3_scope_lint.py
===========================
Fable R3-CI: prevent two classes of char_id scope bugs from being
re-introduced into core/:

  Rule 1 — No new char_id=TEST_CHAR_ID function-parameter defaults
  Rule 2 — No new bare data/ path construction

Existing violations are listed in the allowlists below with comments.
The tests pass today; they fail when NEW violating files appear outside
those allowlists.
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
CORE_ROOT = PROJECT_ROOT / "core"


def _rel(p: Path) -> str:
    """Stable POSIX-style relative path from project root (works on Windows)."""
    return p.relative_to(PROJECT_ROOT).as_posix()


def _iter_core_py(root: Path = CORE_ROOT):
    """Yield all .py files under core/ (excludes any path segment named 'test'/'tests')."""
    for p in root.rglob("*.py"):
        if not any(seg in ("test", "tests") for seg in p.parts):
            yield p


# ---------------------------------------------------------------------------
# Rule 1 — char_id / character_id defaults  ===================================
# ---------------------------------------------------------------------------

# core/data_paths.py is the canonical path-authority class; its methods carry
# backward-compat defaults intentionally so call-sites can migrate incrementally.
#
# Brief 25 §3 P1 migrated former violations to
# `char_id: str = DEFAULT_CHAR_ID` (imported from core.data_paths).
CHAR_ID_DEFAULT_ALLOWLIST: frozenset[str] = frozenset({
    "core/data_paths.py",                          # canonical path authority — by design
})

_GUARDED_PARAM_NAMES: frozenset[str] = frozenset({"char_id", "character_id"})
_GUARDED_DEFAULT_VALUE = TEST_CHAR_ID


def _find_yexuan_defaults(source: str) -> list[int]:
    """
    Return line numbers of function-parameter defaults equal to TEST_CHAR_ID
    where the parameter is named char_id or character_id.
    Uses AST so plain string literals and call-site kwargs are not flagged.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args

        # Positional / positional-or-keyword args:
        # defaults apply to the LAST len(defaults) of (posonlyargs + args).
        all_pos = args.posonlyargs + args.args
        offset = len(all_pos) - len(args.defaults)
        for i, default in enumerate(args.defaults):
            arg = all_pos[offset + i]
            if (
                arg.arg in _GUARDED_PARAM_NAMES
                and isinstance(default, ast.Constant)
                and default.value == _GUARDED_DEFAULT_VALUE
            ):
                hits.append(default.lineno)

        # Keyword-only args (after *): kw_defaults is 1:1, None = no default.
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is None:
                continue
            if (
                arg.arg in _GUARDED_PARAM_NAMES
                and isinstance(default, ast.Constant)
                and default.value == _GUARDED_DEFAULT_VALUE
            ):
                hits.append(default.lineno)

    return hits


def test_no_new_char_id_yexuan_defaults():
    """
    No core/ file outside CHAR_ID_DEFAULT_ALLOWLIST may define a function with
    char_id=TEST_CHAR_ID or character_id=TEST_CHAR_ID as a parameter default.
    """
    new_violations: dict[str, list[int]] = {}

    for path in _iter_core_py():
        rel = _rel(path)
        if rel in CHAR_ID_DEFAULT_ALLOWLIST:
            continue
        lines = _find_yexuan_defaults(path.read_text(encoding="utf-8"))
        if lines:
            new_violations[rel] = lines

    assert not new_violations, (
        "New char_id=TEST_CHAR_ID function defaults found outside the allowlist.\n"
        "Remove the default, or add the file to CHAR_ID_DEFAULT_ALLOWLIST "
        "with a comment explaining why.\n"
        f"Violations: {new_violations}"
    )


# ---------------------------------------------------------------------------
# Rule 2 — bare data/ path construction  =====================================
# ---------------------------------------------------------------------------

# core/data_paths.py constructs Path("data") as the sandbox root — this is
# the one place that legitimately does so (it IS the path authority).
DATA_PATH_ALLOWLIST: frozenset[str] = frozenset({
    "core/data_paths.py",                          # canonical path authority — by design

    # existing violations / to migrate
    "core/tool_dispatcher.py",                     # configured external desktop legacy IPC root
})


def _find_bare_data_paths(source: str) -> list[int]:
    """Return real filesystem data-path construction, not archive path values."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    hits: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id != "Path" or not node.args:
                continue
            value = node.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                if value.value == "data" or value.value.startswith("data/"):
                    hits.add(node.lineno)
        elif isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if first.value.startswith("data/"):
                    hits.add(node.lineno)
        elif isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add):
                left = node.left
                if isinstance(left, ast.Constant) and isinstance(left.value, str):
                    if left.value.startswith("data/"):
                        hits.add(node.lineno)
            elif isinstance(node.op, ast.Div):
                right = node.right
                if isinstance(right, ast.Constant) and isinstance(right.value, str):
                    if right.value == "data" or right.value.startswith("data/"):
                        hits.add(node.lineno)
    return sorted(hits)


def test_no_new_bare_data_paths():
    """
    No core/ file outside DATA_PATH_ALLOWLIST may construct bare data/ paths.
    Use get_paths().<method>() from core/data_paths.py instead.
    """
    new_violations: dict[str, list[int]] = {}

    for path in _iter_core_py():
        rel = _rel(path)
        if rel in DATA_PATH_ALLOWLIST:
            continue
        lines = _find_bare_data_paths(path.read_text(encoding="utf-8"))
        if lines:
            new_violations[rel] = lines

    assert not new_violations, (
        "New bare data/ path construction found outside the allowlist.\n"
        "Use get_paths().<method>() from core/data_paths.py instead.\n"
        "If this is a known legacy violation, add the file to DATA_PATH_ALLOWLIST "
        "with a comment explaining why.\n"
        f"Violations: {new_violations}"
    )


# ---------------------------------------------------------------------------
# Allowlist integrity =========================================================
# ---------------------------------------------------------------------------

def test_allowlisted_files_still_exist():
    """Guard against stale allowlist entries: every allowlisted file must exist."""
    missing = [
        rel
        for rel in sorted(CHAR_ID_DEFAULT_ALLOWLIST | DATA_PATH_ALLOWLIST)
        if not (PROJECT_ROOT / rel).exists()
    ]
    assert not missing, (
        "These allowlisted files no longer exist — remove them from the allowlist:\n"
        + "\n".join(f"  {f}" for f in missing)
    )


# ---------------------------------------------------------------------------
# Detector sanity checks (positive / negative unit tests) ====================
# ---------------------------------------------------------------------------

def test_detector_catches_kwonly_default():
    """AST detector catches keyword-only param: def f(*, char_id: str = TEST_CHAR_ID)."""
    src = f'def load(uid: str, *, char_id: str = "{TEST_CHAR_ID}") -> str:\n    pass\n'
    assert _find_yexuan_defaults(src) == [1]


def test_detector_catches_positional_default():
    """AST detector catches positional param: def f(char_id=TEST_CHAR_ID)."""
    src = f'def fn(uid: str, char_id="{TEST_CHAR_ID}"):\n    pass\n'
    assert _find_yexuan_defaults(src) != []


def test_detector_ignores_plain_string_literal():
    """AST detector does NOT fire on a plain assignment CHAR = TEST_CHAR_ID."""
    src = f'CHAR = "{TEST_CHAR_ID}"\n'
    assert _find_yexuan_defaults(src) == []


def test_detector_ignores_callsite_kwarg():
    """AST detector does NOT fire on a call-site kwarg paths.get(char_id=TEST_CHAR_ID)."""
    src = f'p = paths.foo(char_id="{TEST_CHAR_ID}")\n'
    assert _find_yexuan_defaults(src) == []


def test_bare_data_path_detector_catches_path_literal():
    """AST detector catches Path('data/something')."""
    assert _find_bare_data_paths('base = Path("data/dream/scenarios")\n') != []


def test_bare_data_path_detector_catches_path_root():
    """AST detector catches Path('data')."""
    assert _find_bare_data_paths("self._base = Path('data')\n") != []


def test_bare_data_path_detector_catches_fstring():
    """AST detector catches f'data/...' construction."""
    assert _find_bare_data_paths('p = f"data/{char_id}/state.json"\n') != []


def test_bare_data_path_detector_catches_concat():
    """AST detector catches 'data/' + variable."""
    assert _find_bare_data_paths('p = "data/" + char_id\n') != []


def test_bare_data_path_detector_catches_path_join():
    assert _find_bare_data_paths('p = installation / "data" / "runtime"\n') != []


def test_bare_data_path_detector_ignores_manifest_relative_paths():
    assert _find_bare_data_paths('p = PurePosixPath("data/runtime")\n') == []


def test_bare_data_path_detector_skips_comment_lines():
    """AST detector does not fire on pure comment lines."""
    assert _find_bare_data_paths('# Path("data/foo") is the old path\n') == []


def test_bare_data_path_detector_skips_docstring_lines():
    """AST detector does not fire on standalone docstring delimiter lines."""
    assert _find_bare_data_paths('    """data/runtime/... layout\n') == []
