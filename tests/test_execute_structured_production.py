"""H4: production tool routing uses structured outcomes only; tuple execute is gone."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (ROOT / "core", ROOT / "admin", ROOT / "channels")
PRODUCTION_CALLERS = (
    ROOT / "core" / "pipeline.py",
    ROOT / "core" / "autonomy" / "runner.py",
    ROOT / "admin" / "routers" / "settings_mcp.py",
    ROOT / "core" / "character_permissions.py",
    ROOT / "core" / "pretool_router.py",
)
DISPATCHER = ROOT / "core" / "tool_dispatcher.py"


def _imported_dispatcher_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "core.tool_dispatcher":
            continue
        for alias in node.names:
            names.add(alias.name)
    return names


def _uses_dispatcher_attr(path: Path, attr: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == attr:
            return True
        if isinstance(node, ast.Name) and node.id == attr:
            return True
    return False


def _has_async_execute_def(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute":
            return True
    return False


def test_production_callers_use_structured_not_tuple_execute():
    for path in PRODUCTION_CALLERS:
        imported = _imported_dispatcher_names(path)
        assert _uses_dispatcher_attr(path, "execute_structured"), (
            f"{path.name} must call execute_structured"
        )
        assert "execute" not in imported, f"{path.name} still imports tuple execute"
        if path.name != "tool_dispatcher.py":
            source = path.read_text(encoding="utf-8")
            assert "tool_dispatcher.execute(" not in source, path.name


def test_no_new_production_tuple_execute_importers():
    leftovers: list[str] = []
    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            if path.name == "tool_dispatcher.py":
                continue
            if "execute" in _imported_dispatcher_names(path):
                leftovers.append(str(path.relative_to(ROOT)))
    assert leftovers == [], leftovers


def test_tuple_execute_wrapper_is_gone():
    assert not _has_async_execute_def(DISPATCHER)
    source = DISPATCHER.read_text(encoding="utf-8")
    assert "Compatibility tuple API" not in source
    assert "return outcome.result, outcome.confirmation_request" not in source


@pytest.mark.asyncio
async def test_origin_reject_returns_structured_outcome_not_tuple():
    from core.tool_dispatcher import ToolExecutionOutcome, execute_structured
    import core.tool_dispatcher as td

    assert "execute" not in td.__dict__

    class _State:
        WAITING_CONFIRM = "waiting_confirm"
        status = "idle"

    outcome = await execute_structured(
        "get_time", {}, "u1", "u1", False, _State(),
        origin="not_a_real_origin", char_id="c1",
    )
    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "tool_failed"
    assert outcome.result is None
    assert outcome.confirmation_request is None
    assert not isinstance(outcome, tuple)
