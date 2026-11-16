"""AR-02: domain code must not locate its outward facade, by any loader syntax.

This is a static architectural contract, not a substitute for ingress tests.
Dynamic loading/service location is prohibited in these narrow modules; domain
registration is explicit. AST walks include local imports and TYPE_CHECKING.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def forbidden_target(module):
    if module.startswith("core.tool_runtime.") or module.startswith("core.tools.") and module.endswith("_definitions"):
        return "core.tool_dispatcher"
    if module.startswith("core.owner_chat."):
        return "admin.routers.chat"
    if module.startswith("admin.routers.dream_"):
        return "admin.routers.dream"
    if module.startswith("core.dream."):
        return "admin.routers"
    raise ValueError(module)


def dependency_violations(source, module):
    forbidden = forbidden_target(module)
    tree = ast.parse(source)
    errors = []
    aliases = {}

    def is_forbidden(target):
        return target == forbidden or target.startswith(forbidden + ".")

    def resolve(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return resolve(node.value) + "." + node.attr
        return ""

    # Bind imports first, including imports inside conditionals/functions.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
                if is_forbidden(item.name):
                    errors.append((node.lineno, item.name))
                if item.name.split(".")[0] in {"importlib", "builtins"}:
                    errors.append((node.lineno, "dynamic loader import"))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".")[:-node.level]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            if is_forbidden(target):
                errors.append((node.lineno, target))
            for item in node.names:
                full = target + "." + item.name
                aliases[item.asname or item.name] = full
                if is_forbidden(full):
                    errors.append((node.lineno, full))
            if target.split(".")[0] in {"importlib", "builtins"}:
                errors.append((node.lineno, "dynamic loader import"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and resolve(node) == "sys.modules":
            errors.append((node.lineno, "module service locator"))
        if isinstance(node, ast.Call):
            called = resolve(node.func)
            if called in {"__import__", "eval", "exec"} or called.endswith((".import_module", ".__import__")):
                errors.append((node.lineno, "dynamic loading/evaluation"))
            if called == "getattr" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                member = node.args[1].value
                receiver = resolve(node.args[0])
                if member in {"__import__", "import_module"} or receiver == "sys" and member == "modules" or isinstance(member, str) and is_forbidden(receiver + "." + member):
                    errors.append((node.lineno, "reflective module lookup"))
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call) and resolve(node.func.value.func) in {"globals", "locals", "vars"}:
                errors.append((node.lineno, "namespace service locator"))
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Call) and resolve(node.value.func) in {"globals", "locals", "vars"}:
            errors.append((node.lineno, "namespace service locator"))
    return sorted(set(errors))


def target_files():
    return sorted({
        *(ROOT / "core/tool_runtime").rglob("*.py"),
        *ROOT.glob("core/tools/*_definitions.py"),
        *(ROOT / "core/owner_chat").rglob("*.py"),
        *ROOT.glob("admin/routers/dream_*.py"),
        *(ROOT / "core/dream").rglob("*.py"),
    })


def test_domain_dependency_directions():
    violations = {}
    for path in target_files():
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        errors = dependency_violations(path.read_text(encoding="utf-8"), module)
        if errors:
            violations[str(path.relative_to(ROOT))] = errors
    assert violations == {}


@pytest.mark.parametrize("module,forbidden,parent,leaf", [
    ("core.tool_runtime.execution", "core.tool_dispatcher", "core", "tool_dispatcher"),
    ("core.owner_chat.service", "admin.routers.chat", "admin.routers", "chat"),
    ("admin.routers.dream_sessions", "admin.routers.dream", "admin.routers", "dream"),
])
@pytest.mark.parametrize("template", [
    "import {forbidden} as back",
    "from {parent} import {leaf} as back",
    "def invoke():\n    from {forbidden} import router",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import {forbidden}",
    "from importlib import import_module as load\nback = load('{forbidden}')",
    "back = __import__('{parent}.' + '{leaf}')",
    "import sys as system\nback = system.modules['{forbidden}']",
    "import sys as system\nback = getattr(system, 'modules')['{forbidden}']",
    "from {parent} import __name__\nback = globals().get('{leaf}')",
])
def test_fault_injected_reverse_dependencies_are_rejected(module, forbidden, parent, leaf, template):
    source = template.format(forbidden=forbidden, parent=parent, leaf=leaf)
    assert dependency_violations(source, module), source


def test_legitimate_domain_imports_and_state_ownership_are_allowed():
    source = "from core.memory.scope import MemoryScope\nif '_TOOL_REGISTRY' not in globals():\n    _TOOL_REGISTRY = {}"
    assert dependency_violations(source, "core.tool_runtime.state") == []
