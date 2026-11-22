"""AR-02: domain code must not locate its outward facade, by any loader syntax.

This is a static architectural contract, not a substitute for ingress tests.
Dynamic loading/service location is prohibited in these narrow modules; domain
registration is explicit. AST walks include local imports and TYPE_CHECKING.
"""

import ast
from collections import Counter
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def forbidden_targets(module):
    if module.startswith("core.tool_runtime.") or module.startswith("core.tools.") and module.endswith("_definitions"):
        return ("core.tool_dispatcher",)
    if module.startswith("core.owner_chat."):
        return ("admin.routers",)
    if module.startswith("admin.routers.dream_"):
        return ("admin.routers.dream",)
    if module.startswith("core.dream."):
        return ("admin.routers",)
    if module == "core.scheduler.loop":
        return ("admin.routers",)
    if module == "core.autonomy.runner":
        return ("admin.routers", "core.scheduler.loop")
    if module in {"core.prompt_sections", "core.prompt_budget", "core.memory.prompt_sources",
                  "core.memory.reality_context", "core.post_process.turn_effects"}:
        return ("admin", "core.pipeline", "core.tool_dispatcher")
    raise ValueError(module)


def dependency_violations(source, module):
    forbidden = forbidden_targets(module)
    tree = ast.parse(source)
    errors = []
    aliases = {}

    def is_forbidden(target):
        return any(target == prefix or target.startswith(prefix + ".") for prefix in forbidden)

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
        *(ROOT / "core").glob("prompt_budget.py"),
        ROOT / "core/prompt_sections.py",
        ROOT / "core/memory/prompt_sources.py",
        ROOT / "core/memory/reality_context.py",
        ROOT / "core/post_process/turn_effects.py",
        ROOT / "core/scheduler/loop.py",
        ROOT / "core/autonomy/runner.py",
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
    ("core.prompt_budget", "core.pipeline", "core", "pipeline"),
    ("core.prompt_sections", "core.tool_dispatcher", "core", "tool_dispatcher"),
    ("core.autonomy.runner", "core.scheduler.loop", "core.scheduler", "loop"),
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


# T0 freezes individual existing debt sites, never whole modules. AR-02's
# already-clean boundaries above remain zero-tolerance and cannot use this ledger.
DEBT_PATH = ROOT / "docs/refactors/migration-cutover-dependencies.json"
ACTIVITY_API = {"mark_user_active", "user_active_recently", "_user_active_recently",
                "last_user_message_time", "_last_user_message_time"}
IDENTITY_API = {"_owner_id", "_active_char_id_or_none", "_cfg", "_char_name"}
HTTP_HELPERS = {
    "admin.routers." + path.stem
    for path in (ROOT / "admin/routers").glob("*.py")
    if not any(
        isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in {"get", "post", "put", "patch", "delete", "head", "options", "websocket", "api_route"}
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
    ) and not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "include_router"
                  for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))))
}


def cutover_dependencies(source, module):
    """Return (rule, enclosing owner, target) per import/access, including repeats.

    Imports inside functions, aliases, relative imports, TYPE_CHECKING, literal
    dynamic imports and reflective private access are checked. General dynamic
    expressions cannot prove an architectural dependency and are not evaluated.
    """
    tree = ast.parse(source)
    aliases = {}
    rows = []

    def resolve(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return resolve(node.value) + "." + node.attr
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return resolve(node.left) + resolve(node.right)
        if isinstance(node, ast.Call):
            called = resolve(node.func)
            if called in {"getattr", "importlib.import_module", "__import__"} and node.args:
                if called == "getattr" and len(node.args) > 1:
                    return resolve(node.args[0]) + "." + resolve(node.args[1])
                return resolve(node.args[0])
        if isinstance(node, ast.Subscript) and resolve(node.value) == "sys.modules":
            return resolve(node.slice)
        return ""

    def record(target, owner, kind):
        if module.startswith("core.") and (target == "admin.routers" or target.startswith("admin.routers.")):
            rows.append(("core-router", owner, kind + ":" + target))
        if module.startswith("admin.routers.") and target.startswith("admin.routers."):
            parts = target.split(".")
            # _common/dream_common are HTTP helpers, not private members of a
            # different router. Importing their public functions stays legal.
            if len(parts) >= 4 and parts[3].startswith("_") and ".".join(parts[:3]) != module and ".".join(parts[:3]) not in HTTP_HELPERS:
                rows.append(("router-private", owner, kind + ":" + target))
        prefix = "core.scheduler.loop."
        if target.startswith(prefix):
            name = target[len(prefix):].split(".")[0]
            if name in ACTIVITY_API or (name in IDENTITY_API and not module.startswith("core.scheduler.")):
                rows.append(("scheduler-state", owner, kind + ":" + target))

    def scope_bindings(scope):
        bindings = dict(aliases)

        def collect(node):
            if node is not scope and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return
            if isinstance(node, ast.Import):
                for item in node.names:
                    bindings[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                base = ".".join(module.split(".")[:-node.level] + ([node.module] if node.module else [])) if node.level else node.module or ""
                for item in node.names:
                    bindings[item.asname or item.name] = base + "." + item.name
            for child in ast.iter_child_nodes(node):
                collect(child)

        collect(scope)
        return bindings

    def visit(node, owner="<module>"):
        nonlocal aliases
        previous = aliases
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            aliases = scope_bindings(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owner = node.name if owner == "<module>" else owner + "." + node.name
        if isinstance(node, ast.Import):
            for item in node.names:
                record(item.name, owner, "import")
        elif isinstance(node, ast.ImportFrom):
            base = ".".join(module.split(".")[:-node.level] + ([node.module] if node.module else [])) if node.level else node.module or ""
            for item in node.names:
                record(base + "." + item.name, owner, "import")
        elif isinstance(node, ast.Attribute):
            record(resolve(node), owner, "access")
        elif isinstance(node, ast.Call):
            if resolve(node.func) in {"getattr", "importlib.import_module", "__import__"}:
                record(resolve(node), owner, "dynamic")
        elif isinstance(node, ast.Subscript) and resolve(node.value) == "sys.modules":
            record(resolve(node), owner, "dynamic")
        if module.startswith("core.scheduler."):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ACTIVITY_API:
                rows.append(("scheduler-owner", owner, "definition:" + node.name))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in ACTIVITY_API:
                        rows.append(("scheduler-owner", owner, "state:" + target.id))
        for child in ast.iter_child_nodes(node):
            visit(child, owner)
        aliases = previous

    visit(tree)
    return rows


def cutover_scan(root=ROOT):
    rows = []
    paths = [*root.glob("core/**/*.py"), *root.glob("admin/**/*.py"),
             *root.glob("channels/**/*.py"), root / "main.py"]
    for path in sorted(paths):
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        for (rule, owner, target), count in sorted(Counter(cutover_dependencies(path.read_text(encoding="utf-8"), module)).items()):
            rows.append({"source": path.relative_to(root).as_posix(), "rule": rule,
                         "owner": owner, "target": target, "count": count})
    return rows


def test_migration_cutover_has_no_new_or_stale_dependency_debt():
    ledger = json.loads(DEBT_PATH.read_text(encoding="utf-8"))
    assert ledger["status"] == "pending_independent_review"
    assert ledger["removal_task"] == {"core-router": "T1", "router-private": "T1", "scheduler-state": "T2", "scheduler-owner": "T2"}
    assert cutover_scan() == ledger["dependencies"], (
        "New dependency or stale debt: migrate the caller and remove its exact ledger row; "
        "do not broaden the T0 baseline. See migration-cutover-inventory.md."
    )


@pytest.mark.parametrize("module,source,rule", [
    ("core.new_service", "from admin.routers.chat import run_owner_chat_turn", "core-router"),
    ("core.new_service", "def call():\n from admin.routers import chat", "core-router"),
    ("core.new_service", "from ..admin.routers.chat import _session_grant", "core-router"),
    ("core.new_service", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n import admin.routers.chat", "core-router"),
    ("core.new_service", "from importlib import import_module as load\nload('admin.routers.' + 'chat')", "core-router"),
    ("core.new_service", "import sys as s\ns.modules['admin.routers.chat']", "core-router"),
    ("admin.routers.new", "from .chat import _session_grant as grant", "router-private"),
    ("admin.routers.new", "import admin.routers.chat as chat\nchat._session_grant(None, '')", "router-private"),
    ("admin.routers.new", "from admin.routers import chat as c\ngetattr(c, '_session_grant')", "router-private"),
    ("admin.routers.new", "def one():\n import admin.routers.chat as c\n c._session_grant()\ndef two():\n import core.session_scope as c", "router-private"),
    ("core.new_service", "from core.scheduler.loop import mark_user_active", "scheduler-state"),
    ("core.new_service", "from core.scheduler import loop as clock\nclock._owner_id()", "scheduler-state"),
    ("core.scheduler.new", "_last_user_message_time = 0", "scheduler-owner"),
    ("core.scheduler.new", "def mark_user_active(): pass", "scheduler-owner"),
])
def test_cutover_injected_edges_are_detected(module, source, rule):
    assert any(row[0] == rule for row in cutover_dependencies(source, module))


def test_cutover_keeps_legitimate_http_composition_and_activity_consumption():
    assert cutover_dependencies("from admin.routers.chat import router", "admin.server") == []
    assert cutover_dependencies("from admin.routers._common import active_char_id", "admin.routers.new") == []
    assert cutover_dependencies("from core.conversation_activity import mark_user_active", "core.owner_chat.service") == []
    assert cutover_dependencies("from core.scheduler.loop import start", "main") == []


def test_cutover_repository_scan_detects_new_sites_and_repeated_debt(tmp_path):
    """The same file walker used by CI must see new modules and extra old edges."""
    (tmp_path / "core").mkdir()
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    path = tmp_path / "core/new.py"
    path.write_text("def run():\n from admin.routers.chat import _session_grant\n", encoding="utf-8")
    opening = cutover_scan(tmp_path)
    assert len(opening) == 1 and opening[0]["count"] == 1
    path.write_text(path.read_text(encoding="utf-8") + " from admin.routers.chat import _session_grant\n", encoding="utf-8")
    assert cutover_scan(tmp_path)[0]["count"] == 2
    path.unlink()
    assert cutover_scan(tmp_path) == []  # stale baseline rows must also fail equality
