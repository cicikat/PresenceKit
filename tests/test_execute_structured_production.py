"""H4: production tool routing uses structured outcomes; tuple execute stays compatibility-only."""
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


def test_production_callers_use_structured_not_tuple_execute():
    for path in PRODUCTION_CALLERS:
        imported = _imported_dispatcher_names(path)
        assert _uses_dispatcher_attr(path, "execute_structured"), (
            f"{path.name} must call execute_structured"
        )
        assert "execute" not in imported, f"{path.name} still imports tuple execute"
        if path.name != "tool_dispatcher.py":
            # Attribute execute on the dispatcher module would still be the tuple API.
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


@pytest.mark.asyncio
async def test_tuple_wrapper_keeps_confirmation_and_unknown_shape(monkeypatch):
    from core import tool_dispatcher
    from core.tool_dispatcher import ToolExecutionOutcome, execute, execute_structured

    class _State:
        WAITING_CONFIRM = "waiting_confirm"
        status = "idle"

        def set_waiting_confirm(self, *_args):
            self.status = self.WAITING_CONFIRM

    async def _confirm(*_args, **_kwargs):
        return ToolExecutionOutcome(
            status="confirmation_required",
            confirmation_request="请确认",
        )

    monkeypatch.setattr(tool_dispatcher, "execute_structured", _confirm)
    result, ask = await execute(
        "device_shutdown", {}, "u1", "u1", False, _State(),
        origin="assistant_loop", char_id="c1",
    )
    assert result is None
    assert ask == "请确认"

    async def _unknown(*_args, **_kwargs):
        return ToolExecutionOutcome(status="outcome_unknown", result="动作可能已经送达")

    monkeypatch.setattr(tool_dispatcher, "execute_structured", _unknown)
    result, ask = await execute(
        "mcp__demo__call", {}, "u1", "u1", False, _State(),
        origin="assistant_loop", char_id="c1",
    )
    assert result == "动作可能已经送达"
    assert ask is None
    assert callable(execute_structured)
