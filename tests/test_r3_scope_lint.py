"""
tests/test_r3_scope_lint.py
===========================
Fable R3-CI: prevent two classes of char_id scope bugs from being
re-introduced into core/:

  Rule 1 — No new char_id="yexuan" function-parameter defaults
  Rule 2 — No new bare data/ path construction

Existing violations are listed in the allowlists below with comments.
The tests pass today; they fail when NEW violating files appear outside
those allowlists.
"""
from __future__ import annotations

import ast
import re
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
_GUARDED_DEFAULT_VALUE = "yexuan"


def _find_yexuan_defaults(source: str) -> list[int]:
    """
    Return line numbers of function-parameter defaults equal to "yexuan"
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
    char_id="yexuan" or character_id="yexuan" as a parameter default.
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
        "New char_id='yexuan' function defaults found outside the allowlist.\n"
        "Remove the default, or add the file to CHAR_ID_DEFAULT_ALLOWLIST "
        "with a comment explaining why.\n"
        f"Violations: {new_violations}"
    )


# ---------------------------------------------------------------------------
# Rule 2 — bare data/ path construction  =====================================
# ---------------------------------------------------------------------------

# core/data_paths.py constructs Path("data") as the sandbox root — this is
# the one place that legitimately does so (it IS the path authority).
# core/dream/scenario_loader.py uses Path("data/dream/scenarios") for static
# authored-content (not per-user data) — EXISTING VIOLATION, to migrate.
DATA_PATH_ALLOWLIST: frozenset[str] = frozenset({
    "core/data_paths.py",                          # canonical path authority — by design

    # existing violations / to migrate
    "core/dream/scenario_loader.py",               # static authored-content path, not per-user
})

# Matches bare data/ path construction patterns:
#   Path("data")  Path("data/...")  Path('data/...')
#   f"data/..."   f'data/...'
#   "data/" + ... 'data/' + ...
_BARE_DATA_PATH_RE = re.compile(
    r"""(?x)
    Path\(\s*["']data(?:/[^"']*)?["']\s*\)   # Path("data") or Path("data/...")
    | f["']data/                              # f-string starting with data/
    | ["']data/["']\s*\+                      # string concatenation "data/" + ...
    """
)


def _find_bare_data_paths(source: str) -> list[int]:
    """Return line numbers where bare data/ paths are constructed."""
    hits: list[int] = []
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        # Skip comment-only lines and standalone docstring delimiters.
        if stripped.startswith("#"):
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if _BARE_DATA_PATH_RE.search(line):
            hits.append(lineno)
    return hits


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
    """AST detector catches keyword-only param: def f(*, char_id: str = 'yexuan')."""
    src = 'def load(uid: str, *, char_id: str = "yexuan") -> str:\n    pass\n'
    assert _find_yexuan_defaults(src) == [1]


def test_detector_catches_positional_default():
    """AST detector catches positional param: def f(char_id='yexuan')."""
    src = 'def fn(uid: str, char_id="yexuan"):\n    pass\n'
    assert _find_yexuan_defaults(src) != []


def test_detector_ignores_plain_string_literal():
    """AST detector does NOT fire on a plain assignment CHAR = 'yexuan'."""
    src = 'CHAR = "yexuan"\n'
    assert _find_yexuan_defaults(src) == []


def test_detector_ignores_callsite_kwarg():
    """AST detector does NOT fire on a call-site kwarg paths.get(char_id='yexuan')."""
    src = 'p = paths.foo(char_id="yexuan")\n'
    assert _find_yexuan_defaults(src) == []


def test_bare_data_path_detector_catches_path_literal():
    """Regex catches Path('data/something')."""
    assert _find_bare_data_paths('base = Path("data/dream/scenarios")\n') != []


def test_bare_data_path_detector_catches_path_root():
    """Regex catches Path('data')."""
    assert _find_bare_data_paths("self._base = Path('data')\n") != []


def test_bare_data_path_detector_catches_fstring():
    """Regex catches f'data/...' construction."""
    assert _find_bare_data_paths('p = f"data/{char_id}/state.json"\n') != []


def test_bare_data_path_detector_catches_concat():
    """Regex catches 'data/' + variable."""
    assert _find_bare_data_paths('p = "data/" + char_id\n') != []


def test_bare_data_path_detector_skips_comment_lines():
    """Regex does not fire on pure comment lines."""
    assert _find_bare_data_paths('# Path("data/foo") is the old path\n') == []


def test_bare_data_path_detector_skips_docstring_lines():
    """Regex does not fire on standalone docstring delimiter lines."""
    assert _find_bare_data_paths('    """data/runtime/... layout\n') == []
