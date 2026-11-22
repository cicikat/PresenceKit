"""
Static guards for the boundary between Reality loaders and Dream storage.

The scan is AST-based: comments, docstrings, and ordinary explanatory strings
are inert. Imports, names, attributes, and path literals passed to path APIs
remain visible to the guard.
"""

import ast
from pathlib import Path

_ROOT = Path(__file__).parent.parent

_MARKERS = [
    "dreams/",
    "impression_loader",
    "afterglow",
    "dream_summary",
    "dreams/archive",
]

# Only explicitly approved one-way/read-only Reality consumers belong here.
_ALLOWLIST: set[tuple[str, str]] = {
    ("core/pipeline.py", "impression_loader"),  # Echo attribution before capture.
    ("core/memory/reality_context.py", "impression_loader"),
    ("core/memory/user_hidden_state.py", "afterglow"),
    ("core/memory/user_hidden_state_integrator.py", "afterglow"),
    ("core/memory/user_hidden_state_store.py", "afterglow"),
    ("core/prompt_builder.py", "afterglow"),
    ("core/memory/path_resolver.py", "afterglow"),
    ("core/memory/user_facts.py", "afterglow"),
    ("core/memory/reality_context.py", "afterglow"),
}


def _reality_files() -> list[Path]:
    memory_dir = _ROOT / "core" / "memory"
    return [
        *sorted(memory_dir.glob("*.py")),
        _ROOT / "core" / "pipeline.py",
        _ROOT / "core" / "prompt_builder.py",
    ]


def _rel(path: Path) -> str:
    try:
        relative = path.relative_to(_ROOT)
    except ValueError:
        relative = path
    return str(relative).replace("\\", "/")


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _docstring_nodes(tree: ast.AST) -> set[int]:
    nodes: set[int] = set()
    scope_nodes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, scope_nodes) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            nodes.add(id(first.value))
    return nodes


def _path_literal_context(node: ast.Constant, parents: dict[int, ast.AST]) -> bool:
    parent = parents.get(id(node))
    if isinstance(parent, ast.JoinedStr):
        parent = parents.get(id(parent))
    if isinstance(parent, ast.Call):
        call_name = _dotted_name(parent.func).lower()
        return any(
            name in call_name
            for name in (
                "path",
                "open",
                "joinpath",
                "resolve_path",
                "get_paths",
                "user_memory_root",
            )
        )
    if isinstance(parent, (ast.Assign, ast.AnnAssign)):
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        names = [_dotted_name(target).lower() for target in targets]
        return any(
            hint in name
            for name in names
            for hint in ("path", "file", "dir", "root", "archive")
        )
    return False


def _scan_violations(
    files: list[Path],
    markers: list[str],
    allowlist: set[tuple[str, str]],
) -> list[str]:
    violations: list[str] = []
    for fpath in files:
        if not fpath.exists():
            violations.append(f"MISSING FILE: {_rel(fpath)}")
            continue

        rel = _rel(fpath)
        source = fpath.read_text(encoding="utf-8")
        lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=str(fpath))
        except SyntaxError as exc:
            violations.append(f"{rel}:{exc.lineno}: invalid Python source: {exc.msg}")
            continue

        parents: dict[int, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent
        docstrings = _docstring_nodes(tree)

        def record(node: ast.AST, candidate: str) -> None:
            if not candidate:
                return
            lineno = getattr(node, "lineno", 1)
            line = lines[lineno - 1].rstrip() if 0 < lineno <= len(lines) else ""
            for marker in markers:
                if marker in candidate and (rel, marker) not in allowlist:
                    violations.append(
                        f"{rel}:{lineno}: forbidden {marker!r} -> {line}"
                    )

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    candidates = [alias.name for alias in node.names]
                else:
                    module = node.module or ""
                    candidates = [
                        f"{module}.{alias.name}" if module else alias.name
                        for alias in node.names
                    ]
                for candidate in candidates:
                    record(node, candidate)
            elif isinstance(node, (ast.Name, ast.Attribute)):
                record(node, _dotted_name(node))
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and _path_literal_context(node, parents)
            ):
                record(node, node.value)
    return violations


def test_reality_loaders_do_not_reference_dream_paths() -> None:
    violations = _scan_violations(_reality_files(), _MARKERS, _ALLOWLIST)
    assert not violations, (
        f"{len(violations)} violation(s): reality-side code must not reference Dream paths:\n"
        + "\n".join(violations)
    )


def test_positive_sample_impression_loader_references_dream_path() -> None:
    """The Dream loader keeps a documented data path marker for this contract."""
    loader = _ROOT / "core" / "dream" / "impression_loader.py"
    assert loader.exists(), f"positive-sample file not found: {loader}"
    src = loader.read_text(encoding="utf-8")
    assert "dreams/" in src


def test_scan_rejects_real_dream_import_and_path_accessor(tmp_path: Path) -> None:
    """A real import/path reference fails while prose remains inert."""
    fixture = tmp_path / "reality_loader_fixture.py"
    fixture.write_text(
        """\
\"\"\"Documentation may mention afterglow and dreams/archive safely.\"\"\"
from core.dream.impression_loader import load_impression_text
from pathlib import Path

EXPLANATION = \"The dreams/ marker in this explanation is not a path.\"
DREAM_PATH = Path(\"data/dreams/archive/{uid}.json\")
""",
        encoding="utf-8",
    )

    violations = _scan_violations(
        [fixture],
        ["dreams/", "impression_loader", "afterglow", "dreams/archive"],
        allowlist=set(),
    )

    assert any("forbidden 'impression_loader'" in item for item in violations)
    assert any("forbidden 'dreams/'" in item for item in violations)
    assert any("forbidden 'dreams/archive'" in item for item in violations)
    assert not any(":1:" in item or ":5:" in item for item in violations)


# Reverse-direction scan for Dream Stage: group-dream runtime must not acquire
# Reality memory writeback entry points (zero reflow).
_GROUP_DREAM_FILES: list[Path] = [
    _ROOT / "core" / "stage" / "dream_runtime.py",
    _ROOT / "core" / "stage" / "dream_views.py",
]

_GROUP_DREAM_FORBIDDEN_MARKERS = [
    # Do not use the bare word "projection": it is legitimate dream-domain
    # vocabulary in the body projection module.
    "core.stage.projection",
    "enqueue_reality_projection",
    "summarize_to_midterm",
    "impression_loader",
    "afterglow",
    "hidden_state",
]


def test_dream_stage_runtime_does_not_reference_reality_writers() -> None:
    violations = _scan_violations(
        _GROUP_DREAM_FILES,
        _GROUP_DREAM_FORBIDDEN_MARKERS,
        allowlist=set(),
    )
    assert not violations, (
        f"{len(violations)} violation(s): Dream Stage must not reference "
        "Reality memory writeback entry points:\n"
        + "\n".join(violations)
    )


def test_positive_sample_reality_stage_runtime_references_projection() -> None:
    """The Reality stage runtime keeps a projection reference for this guard."""
    runtime = _ROOT / "core" / "stage" / "runtime.py"
    assert runtime.exists(), f"positive-sample file not found: {runtime}"
    src = runtime.read_text(encoding="utf-8")
    assert "projection" in src
