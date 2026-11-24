"""AR-02: domain code must not locate its outward facade, by any loader syntax.

This is a static architectural contract, not a substitute for ingress tests.
Dynamic loading/service location is prohibited in these narrow modules; domain
registration is explicit. AST walks include local imports and TYPE_CHECKING.
"""

import ast
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
T31_COMPONENT_MODULES = {
    "core.memory.reality_context", "core.owner_chat.tool_loop",
    "core.post_process.turn_effects", "core.post_process.handlers",
    "core.owner_chat.reply_generation", "core.character_session", "core.turn_sink",
}


def forbidden_targets(module):
    if module in T31_COMPONENT_MODULES:
        existing = ("admin", "core.tool_dispatcher") if module in {"core.memory.reality_context", "core.post_process.turn_effects"} else ("admin.routers",)
        return (*existing, "core.pipeline", "core.pipeline_registry")
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
        *(ROOT / Path(*module.split(".")).with_suffix(".py") for module in T31_COMPONENT_MODULES),
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
MOVED_IDENTITY_IMPLEMENTATIONS = {"_owner_id", "_active_char_id_or_none"}
ACTIVITY_PRIVATE_MEMBERS = (
    "core.activity.store._session_is_stale",
    "core.activity.transcript._path",
)
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


def cutover_dependencies(source, module, *, registry_sites=None):
    """Return (rule, enclosing owner, target) per import/access, including repeats.

    Imports inside functions, aliases, relative imports, TYPE_CHECKING, literal
    dynamic imports, sys.modules.get, builtins.__import__, simple returned-module
    aliases, star imports and reflective private access are checked. General
    runtime strings/control flow are not evaluated or fully data-flow analyzed.
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
            if called in {"getattr", "importlib.import_module", "__import__", "builtins.__import__", "sys.modules.get"} and node.args:
                if called == "getattr" and len(node.args) > 1:
                    return resolve(node.args[0]) + "." + resolve(node.args[1])
                return resolve(node.args[0])
        if isinstance(node, ast.Subscript) and resolve(node.value) == "sys.modules":
            return resolve(node.slice)
        return ""

    def record(target, owner, kind):
        if module.startswith("admin.") and any(target == member or target.startswith(member + ".") for member in ACTIVITY_PRIVATE_MEMBERS):
            rows.append(("admin-activity-private", owner, kind + ":" + target))
        if module.startswith("core.") and (target == "admin.routers" or target.startswith("admin.routers.")):
            rows.append(("core-router", owner, kind + ":" + target))
        if module.startswith("admin.routers.") and target.startswith("admin.routers."):
            parts = target.split(".")
            # _common/dream_common are HTTP helpers, not private members of a
            # different router. Importing their public functions stays legal.
            if len(parts) >= 4 and (parts[3].startswith("_") or parts[3] == "*") and ".".join(parts[:3]) != module and ".".join(parts[:3]) not in HTTP_HELPERS:
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
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                # Track simple module aliases returned by the supported lookup
                # syntax. This does not execute code or infer arbitrary strings.
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                previous_aliases = dict(aliases)
                aliases.update(bindings)
                value = resolve(node.value)
                aliases.clear()
                aliases.update(previous_aliases)
                if value.startswith(("admin.routers.", "core.scheduler.loop", "core.activity.store", "core.activity.transcript", "core.pipeline_registry")):
                    for target in targets:
                        if isinstance(target, ast.Name):
                            bindings[target.id] = value
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
            if registry_sites is not None:
                called = resolve(node.func)
                if called in {"core.pipeline_registry.get", "core.pipeline_registry.register"}:
                    registry_sites.append({"owner": owner, "operation": called.rsplit(".", 1)[1], "line": node.lineno})
                elif (called in {"importlib.import_module", "__import__", "builtins.__import__", "sys.modules.get"}
                      and resolve(node) == "core.pipeline_registry"):
                    registry_sites.append({"owner": owner, "operation": "dynamic-load", "line": node.lineno})
                elif called == "getattr" and node.args and resolve(node.args[0]) == "core.pipeline_registry" and (
                    len(node.args) < 2 or not isinstance(node.args[1], ast.Constant)
                ):
                    registry_sites.append({"owner": owner, "operation": "dynamic-member", "line": node.lineno})
            if resolve(node.func) in {"getattr", "importlib.import_module", "__import__", "builtins.__import__", "sys.modules.get"}:
                record(resolve(node), owner, "dynamic")
        elif isinstance(node, ast.Subscript) and resolve(node.value) == "sys.modules":
            if registry_sites is not None and resolve(node) == "core.pipeline_registry":
                registry_sites.append({"owner": owner, "operation": "dynamic-load", "line": node.lineno})
            record(resolve(node), owner, "dynamic")
        if module.startswith("core.scheduler."):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ACTIVITY_API | MOVED_IDENTITY_IMPLEMENTATIONS:
                rows.append(("scheduler-owner", owner, "definition:" + node.name))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in ACTIVITY_API | MOVED_IDENTITY_IMPLEMENTATIONS:
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


def registry_scan(root=ROOT):
    """Scan every Git-visible production Python file, including new files and entry scripts."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py"],
        cwd=root, capture_output=True, check=True,
    ).stdout.decode("utf-8").split("\0")
    rows = []
    for name in sorted(set(listed) - {""}):
        if name.startswith("tests/") or Path(name).name.startswith("test_"):
            continue
        path = root / name
        if not path.is_file():
            continue
        sites = []
        module = ".".join(Path(name).with_suffix("").parts)
        cutover_dependencies(path.read_text(encoding="utf-8"), module, registry_sites=sites)
        rows.extend({"source": name, **site} for site in sites)
    return sorted(rows, key=lambda row: (row["source"], row["owner"], row["operation"], row["line"]))


T31_CONSTRUCTOR_CAPABILITIES = {
    "ReplyGeneration": {"resolve_active_character_id"},
    "RealityContextReader": {"resolve_scope", "read_snapshot"},
    "ToolTurnRunner": {"run_llm", "run_llm_stream", "guard_tool_meta_leak", "guard_unverified_claim", "anti_collapse_prefix_retry"},
    "TurnEffects": {"resolve_scope", "is_active_character"},
}
REGISTRY_REVIEW = ROOT / "docs/refactors/t31-pipeline-registry-sites.json"


def narrow_component_violations(source, module):
    """Structural contracts in the identified narrow domain, not general Python data-flow proof."""
    tree = ast.parse(source)
    errors = []
    if module not in T31_COMPONENT_MODULES:
        return errors
    for node in ast.walk(tree):
        if module == "core.owner_chat.reply_generation":
            forbidden = {"_active_character_id", "DEFAULT_CHAR_ID"}
            if (isinstance(node, ast.Name) and node.id in forbidden
                or isinstance(node, ast.alias) and node.name in forbidden
                or isinstance(node, ast.Attribute) and node.attr in forbidden
                or isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in forbidden):
                errors.append((node.lineno, "generation cannot read or guess character state"))
        if isinstance(node, ast.Attribute) and node.attr in {"pipeline", "runtime", "generator", "__self__", "__globals__", "__closure__"}:
            errors.append((node.lineno, "whole runtime/back-reference"))
        if isinstance(node, ast.Name) and node.id in {"pipeline", "runtime", "_pipeline"}:
            errors.append((node.lineno, "whole runtime binding"))
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if name in {"globals", "locals", "vars", "eval", "exec", "__import__"}:
                errors.append((node.lineno, "namespace/dynamic access"))
            if name == "getattr" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and node.args[1].value in {
                "pipeline", "runtime", "generator", "__self__", "__globals__", "__closure__",
            }:
                errors.append((node.lineno, "reflective runtime access"))
        if isinstance(node, ast.ClassDef) and node.name in T31_CONSTRUCTOR_CAPABILITIES:
            init = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
            if init is None:
                errors.append((node.lineno, "constructor must require only its narrow capabilities"))
                continue
            if ([arg.arg for arg in init.args.args] != ["self"] or init.args.posonlyargs
                or init.args.vararg or init.args.kwarg
                or {arg.arg for arg in init.args.kwonlyargs} != T31_CONSTRUCTOR_CAPABILITIES[node.name]
                or any(default is not None for default in init.args.kw_defaults)):
                errors.append((init.lineno, "constructor must require only its narrow capabilities"))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"register_slow_handlers", "_handler_consistency_check"}:
            dependencies = {arg.arg for arg in node.args.kwonlyargs}
            if dependencies != {"read_character", "write_author_note"} or any(default is not None for default in node.args.kw_defaults):
                errors.append((node.lineno, "consistency dependencies must be required"))
            for branch in ast.walk(node):
                if isinstance(branch, ast.If) and any(isinstance(n, ast.Name) and n.id in dependencies for n in ast.walk(branch.test)):
                    if any(isinstance(n, ast.Return) and (n.value is None or isinstance(n.value, ast.Constant) and n.value.value is None) for n in branch.body):
                        errors.append((branch.lineno, "missing consistency dependency cannot silently return"))
    return sorted(set(errors))


def component_composition_violations(source):
    tree = ast.parse(source)
    aliases = {}
    whole = {"self", "pipeline", "runtime", "_pipeline", "pl"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for item in node.names:
                aliases[item.asname or item.name] = (node.module or "") + "." + item.name
    def resolve(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return resolve(node.value) + "." + node.attr
        return ""
    changed = True
    while changed:
        before = set(whole)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if isinstance(value, ast.Name) and value.id in whole or isinstance(value, ast.Call) and resolve(value.func).endswith("Pipeline"):
                    whole.update(target.id for target in targets if isinstance(target, ast.Name))
        changed = whole != before
    captured = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local = {arg.arg for arg in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]}
            if any(isinstance(child, ast.Name) and child.id in whole - local for statement in node.body for child in ast.walk(statement)):
                captured.add(node.name)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id in whole:
                captured.update(target.id for target in targets if isinstance(target, ast.Name))
            if isinstance(value, ast.Lambda) and any(isinstance(child, ast.Name) and child.id in whole for child in ast.walk(value.body)):
                captured.update(target.id for target in targets if isinstance(target, ast.Name))
    errors = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or resolve(node.func).rsplit(".", 1)[-1] not in T31_CONSTRUCTOR_CAPABILITIES:
            continue
        if node.args:
            errors.append((node.lineno, "positional whole-object dependency"))
        if resolve(node.func).rsplit(".", 1)[-1] == "ReplyGeneration":
            expected = "self.character_state.current_character_id"
            if (len(node.keywords) != 1 or node.keywords[0].arg != "resolve_active_character_id"
                or resolve(node.keywords[0].value) != expected):
                errors.append((node.lineno, "generation requires CharacterSession.current_character_id bound method"))
        for keyword in node.keywords:
            value = keyword.value
            if isinstance(value, ast.Name) and value.id in captured:
                errors.append((node.lineno, "named capability captures whole Pipeline"))
            if isinstance(value, ast.Name) and value.id in whole:
                errors.append((node.lineno, "whole object disguised as capability"))
            if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id in whole:
                errors.append((node.lineno, "capability bound to whole Pipeline"))
            for closure in ast.walk(value):
                if isinstance(closure, ast.Lambda):
                    local = {arg.arg for arg in [*closure.args.args, *closure.args.kwonlyargs]}
                    if any(isinstance(n, ast.Name) and n.id in whole - local for n in ast.walk(closure.body)):
                        errors.append((node.lineno, "closure captures whole Pipeline"))
                if isinstance(closure, ast.Attribute) and closure.attr in {"pipeline", "runtime", "__self__", "__globals__", "__closure__"}:
                    errors.append((node.lineno, "capability back-reference"))
    return sorted(set(errors))


def test_t31_domains_use_narrow_capabilities():
    errors = {}
    for module in T31_COMPONENT_MODULES:
        path = ROOT / Path(*module.split(".")).with_suffix(".py")
        violations = narrow_component_violations(path.read_text(encoding="utf-8"), module)
        if violations:
            errors[module] = violations
    assert errors == {}, errors
    pipeline = (ROOT / "core/pipeline.py").read_text(encoding="utf-8")
    composition_errors = component_composition_violations(pipeline)
    assert composition_errors == [], composition_errors


@pytest.mark.parametrize("source", [
    "class RealityContextReader:\n def __init__(self, runtime): self.runtime = runtime",
    "class TurnEffects:\n def __init__(self, *, resolve_scope=None, is_active_character=None): pass",
    "def read(): return holder.pipeline.character",
    "def read(): return getattr(holder, '__self__')",
    "def read(): return globals()['runtime']",
    "def register_slow_handlers(*, read_character=None, write_author_note=None): pass",
    "def _handler_consistency_check(payload, *, read_character, write_author_note):\n if read_character is None: return",
])
def test_t31_rejects_whole_runtime_and_optional_handler_dependencies(source):
    assert narrow_component_violations(source, "core.memory.reality_context"), source


@pytest.mark.parametrize("source", [
    "from core.memory.reality_context import RealityContextReader as Reader\nx = Reader(runtime)",
    "from core.memory.reality_context import RealityContextReader as Reader\nrenamed = pipeline\nx = Reader(resolve_scope=renamed)",
    "x = TurnEffects(resolve_scope=self.scope, is_active_character=self.active)",
    "x = TurnEffects(resolve_scope=lambda uid: pipeline.scope(uid))",
    "x = TurnEffects(resolve_scope=holder.__self__)",
    "def scope(uid): return pipeline.scope(uid)\nx = TurnEffects(resolve_scope=scope)",
    "lookup = pipeline.scope\nx = TurnEffects(resolve_scope=lookup)",
])
def test_t31_rejects_disguised_runtime_composition(source):
    assert component_composition_violations(source), source


def test_t31_allows_required_narrow_capabilities():
    source = "class RealityContextReader:\n def __init__(self, *, resolve_scope, read_snapshot):\n  self._resolve_scope = resolve_scope\n  self._read_snapshot = read_snapshot"
    assert narrow_component_violations(source, "core.memory.reality_context") == []
    assert component_composition_violations(
        "x = RealityContextReader(resolve_scope=self.character_state.reality_scope, read_snapshot=self.character_state.context_snapshot)"
    ) == []


@pytest.mark.parametrize("source", [
    "class ReplyGeneration:\n def __init__(self): pass",
    "class ReplyGeneration:\n def __init__(self, *, resolve_active_character_id=None): pass",
    "from core.data_paths import DEFAULT_CHAR_ID as implicit\nvalue = implicit",
    "value = self._active_character_id",
    "value = getattr(self, '_active_character_id')",
])
def test_t311_generation_rejects_implicit_character_state(source):
    assert narrow_component_violations(source, "core.owner_chat.reply_generation")


@pytest.mark.parametrize("capability", ["self.current_character_id", "self.character_state", "pipeline.current_character_id", "lambda: self.character_state.current_character_id()"])
def test_t311_generation_rejects_other_composition(capability):
    assert component_composition_violations("x = ReplyGeneration(resolve_active_character_id=" + capability + ")")


def test_t311_generation_accepts_only_required_loaded_id_capability():
    source = "class ReplyGeneration:\n def __init__(self, *, resolve_active_character_id): self._resolve_active_character_id = resolve_active_character_id"
    assert narrow_component_violations(source, "core.owner_chat.reply_generation") == []
    assert component_composition_violations(
        "from core.owner_chat.reply_generation import ReplyGeneration as Generator\nx = Generator(resolve_active_character_id=self.character_state.current_character_id)"
    ) == []


def test_t311_generation_cannot_restore_registry_dependency():
    assert dependency_violations("from core.pipeline_registry import get as lookup\nlookup()",
                                 "core.owner_chat.reply_generation")


@pytest.mark.parametrize("source,operation", [
    ("from core.pipeline_registry import get as lookup\ndef run(): return lookup()", "get"),
    ("from core import pipeline_registry as registry\nlookup=registry.get\nlookup()", "get"),
    ("def run():\n from core.pipeline_registry import register as bind\n bind(object())", "register"),
    ("import core.pipeline_registry as r\ngetattr(r, 'get')()", "get"),
    ("from importlib import import_module as load\nr=load('core.' + 'pipeline_registry')\nr.get()", "dynamic-load"),
    ("import sys as s\nr=s.modules['core.pipeline_registry']\nr.get()", "dynamic-load"),
    ("import core.pipeline_registry as r\ngetattr(r, name)()", "dynamic-member"),
])
def test_t31_registry_scan_tracks_local_aliases_and_recognizable_dynamic_access(source, operation):
    sites = []
    cutover_dependencies(source, "integrations.probe", registry_sites=sites)
    assert any(row["operation"] == operation for row in sites), sites


def test_t31_unrelated_get_and_register_are_not_registry_sites():
    sites = []
    cutover_dependencies("from channels import registry\nregistry.register(object())\nvalue = {}.get('key')", "channels.probe", registry_sites=sites)
    assert sites == []


def test_t31_registry_sites_match_individual_review():
    document = json.loads(REGISTRY_REVIEW.read_text(encoding="utf-8"))
    fields = ("source", "owner", "operation", "line")
    approved = []
    for row in document["retained"]:
        assert row["decision"] == "retain"
        assert all(isinstance(row[name], str) and row[name].strip() for name in (
            "classification", "chain", "capability", "reason", "exit_condition",
        ))
        assert "*" not in row["source"] and "*" not in row["owner"]
        approved.append({field: row[field] for field in fields})
    sites = registry_scan()
    assert sites == approved, "Unreviewed or stale registry call sites: " + repr(sites)
    forbidden_scopes = {
        ("core/scheduler/loop.py", "_char_name"),
        ("core/dream/dream_pipeline.py", "enter_dream"),
    }
    forbidden_modules = T31_COMPONENT_MODULES | {"core.autonomy.runner"}
    assert not [row for row in sites if row["source"].startswith(("admin/", "channels/"))
                or ".".join(Path(row["source"]).with_suffix("").parts) in forbidden_modules
                or (row["source"], row["owner"]) in forbidden_scopes], "Retired registry lookup cannot be restored"


def test_migration_cutover_has_no_new_or_stale_dependency_debt():
    ledger = json.loads(DEBT_PATH.read_text(encoding="utf-8"))
    assert ledger["status"] == "pending_independent_review"
    assert ledger["removal_task"] == {"core-router": "T1", "router-private": "T1", "scheduler-state": "T2", "scheduler-owner": "T2"}
    assert cutover_scan() == ledger["dependencies"], (
        "New dependency or stale debt: migrate the caller and remove its exact ledger row; "
        "do not broaden the T0 baseline. See migration-cutover-inventory.md."
    )


def test_t1_http_dependency_boundaries_are_zero():
    """T1 is retired, even while older target commits still list its debt."""
    retired = {"core-router", "router-private"}
    assert not [row for row in cutover_scan() if row["rule"] in retired], "T1 must remain zero"
    ledger = json.loads(DEBT_PATH.read_text(encoding="utf-8"))
    assert not [row for row in ledger["dependencies"] if row["rule"] in retired], "T1 debt may not be restored"


def test_admin_activity_storage_uses_public_operations():
    assert not [row for row in cutover_scan() if row["rule"] == "admin-activity-private"], (
        "Admin must use public activity lifetime and transcript operations"
    )


def test_t3_pipeline_compatibility_operations_are_retired():
    """Transferred operations must be called on their domain owners."""
    retired = {"fetch_context", "run_agentic_loop", "post_process_critical", "post_process_slow", "post_process"}
    pipeline = ast.parse((ROOT / "core/pipeline.py").read_text(encoding="utf-8"))
    cls = next(node for node in pipeline.body if isinstance(node, ast.ClassDef) and node.name == "Pipeline")
    assert not [node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in retired]
    references = []
    for path in [*ROOT.glob("core/**/*.py"), *ROOT.glob("admin/**/*.py"), *ROOT.glob("channels/**/*.py"), ROOT / "main.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        references.extend((str(path.relative_to(ROOT)), node.lineno, node.attr)
                          for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in retired)
    assert not references, references


def test_t2_conversation_activity_boundaries_are_zero():
    """All T2 state/identity dependencies are retired, regardless of old ledgers."""
    retired = {"scheduler-owner", "scheduler-state"}
    assert not [row for row in cutover_scan() if row["rule"] in retired], "T2 activity must remain zero"
    ledger = json.loads(DEBT_PATH.read_text(encoding="utf-8"))
    assert not [row for row in ledger["dependencies"] if row["rule"] in retired], "T2 activity debt may not be restored"


@pytest.mark.parametrize("member", ACTIVITY_PRIVATE_MEMBERS)
@pytest.mark.parametrize("template", [
    "from {module} import {name} as hidden",
    "import {module} as storage\nstorage.{name}",
    "from core.activity import {leaf} as storage\ngetattr(storage, '{name}')",
    "def handle():\n from {module} import {name}",
    "from importlib import import_module as load\nstorage = load('{module}')\nstorage.{name}",
    "import sys as system\nstorage = system.modules.get('{module}')\nstorage.{name}",
])
def test_admin_private_activity_access_is_rejected(member, template):
    module, name = member.rsplit(".", 1)
    source = template.format(module=module, name=name, leaf=module.rsplit(".", 1)[1])
    assert any(row[0] == "admin-activity-private" for row in cutover_dependencies(source, "admin.example"))


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


@pytest.mark.parametrize("module", ["core.scheduler.loop", "core.scheduler.triggers.example"])
@pytest.mark.parametrize("name", ["_owner_id", "_active_char_id_or_none"])
@pytest.mark.parametrize("template,kind", [
    ("def {name}(): return 'local'", "definition"),
    ("async def {name}(): return 'local'", "definition"),
    ("{name} = lambda: 'local'", "state"),
    ("{name}: object = lambda: 'local'", "state"),
    ("{name} = local_reader", "state"),
])
def test_scheduler_cannot_recreate_moved_identity(module, name, template, kind):
    """Reject independent implementations regardless of scheduler submodule."""
    rows = cutover_dependencies(template.format(name=name), module)
    assert any(rule == "scheduler-owner" and target == kind + ":" + name
               for rule, _, target in rows), rows


def test_scheduler_can_import_identity_owners_and_define_internal_helpers():
    source = (
        "from core.owner_config import configured_owner_id as _owner_id\n"
        "from core.character_context import selected_character_id_or_none as _active_char_id_or_none\n"
        "def _cfg(): return {}\n"
        "def _char_name(): return 'Companion'\n"
    )
    assert cutover_dependencies(source, "core.scheduler.loop") == []


@pytest.mark.parametrize("name", ["_owner_id", "_active_char_id_or_none"])
def test_ci_rejects_and_recovers_moved_scheduler_identity(cutover_git_repository, name):
    """Inject into a real file; run CI's repository assertion before/after removal."""
    root, _, _, _ = cutover_git_repository
    directory = root / "core/scheduler"
    directory.mkdir()
    path = directory / "loop.py"
    path.write_bytes(("def " + name + "(): return 'local'\n").encode())
    command = [sys.executable, "-m", "pytest", "--noconftest", "-c", "pytest.ini", "-q", "-vv",
               "tests/test_ar02_dependency_directions.py::test_migration_cutover_has_no_new_or_stale_dependency_debt"]
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    failed = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert failed.returncode == 1 and "scheduler-owner" in failed.stdout, failed.stdout + failed.stderr
    path.write_bytes(b"from core.owner_config import configured_owner_id as _owner_id\n"
                     b"from core.character_context import selected_character_id_or_none as _active_char_id_or_none\n")
    recovered = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr


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


@pytest.mark.parametrize("loader", [
    "import sys\nmodule = sys.modules.get('admin.routers.chat')",
    "import sys as system\nmodule = system.modules.get('admin.routers.chat')",
    "from sys import modules as loaded\nmodule = loaded.get('admin.routers.chat')",
    "import builtins\nmodule = builtins.__import__('admin.routers.chat')",
    "import builtins as bi\nmodule = bi.__import__('admin.routers.chat')",
    "from builtins import __import__ as load\nmodule = load('admin.routers.chat')",
    "from importlib import import_module as load\nmodule = load('admin.routers.' + 'chat')",
    "import sys as system\nmodule: object = system.modules.get('admin.routers.chat')",
])
@pytest.mark.parametrize("module,rule", [
    ("core.cutover_probe", "core-router"),
    ("admin.routers.cutover_probe", "router-private"),
])
def test_cutover_dynamic_module_aliases_cannot_hide_reverse_access(loader, module, rule):
    source = loader + "\ngrant = module._session_grant\n"
    assert any(row[0] == rule for row in cutover_dependencies(source, module)), source


@pytest.mark.parametrize("module,source,rule", [
    ("admin.routers.cutover_probe", "from admin.routers.chat import *", "router-private"),
    ("admin.routers.cutover_probe", "from .chat import *", "router-private"),
    ("core.cutover_probe", "from admin.routers import *", "core-router"),
])
def test_cutover_cross_router_star_imports_are_rejected(module, source, rule):
    assert any(row[0] == rule for row in cutover_dependencies(source, module))


@pytest.fixture
def cutover_git_repository(tmp_path):
    """A tiny real Git history; both CI entry commands run in child processes."""
    for directory in ("tests", "scripts", "core", "docs/refactors"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__), tmp_path / "tests/test_ar02_dependency_directions.py")
    shutil.copyfile(ROOT / "scripts/check_architecture_debt.py", tmp_path / "scripts/check_architecture_debt.py")
    (tmp_path / "main.py").write_bytes(b"")
    (tmp_path / "pytest.ini").write_bytes(b"[pytest]\n")
    source = "from admin.routers.chat import run_owner_chat_turn\n"
    (tmp_path / "core/debt.py").write_bytes((source * 2).encode())
    ledger = {"status": "pending_independent_review",
              "removal_task": {"core-router": "T1", "router-private": "T1", "scheduler-state": "T2", "scheduler-owner": "T2"},
              "dependencies": [{"source": "core/debt.py", "rule": "core-router", "owner": "<module>",
                                "target": "import:admin.routers.chat.run_owner_chat_turn", "count": 2}]}
    ledger["dependencies"].append(dict(ledger["dependencies"][0], source="core/other_debt.py", count=1))
    (tmp_path / "core/other_debt.py").write_bytes(source.encode())
    path = tmp_path / "docs/refactors/migration-cutover-dependencies.json"
    path.write_bytes(json.dumps(ledger).encode())
    for args in [("init", "-b", "main"), ("add", "."),
                 ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                  "-c", "commit.gpgsign=false", "-c", "core.hooksPath=.git/hooks",
                  "commit", "-m", "Synthetic architecture baseline")]:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path, path, ledger, source


@pytest.mark.parametrize("mutation,expected_stage", [
    ("json_only_add", "convergence"),
    ("source_and_json_add", "convergence"),
    ("count_increase", "convergence"),
    ("rename_source", "convergence"),
    ("rename_owner", "convergence"),
    ("rename_target", "convergence"),
    ("rename_rule", "convergence"),
    ("remove_live_row", "scan"),
    ("migrate_and_remove", "passed"),
    ("reduce_count", "passed"),
    ("unchanged", "passed"),
])
def test_ci_entrypoints_enforce_debt_convergence(cutover_git_repository, mutation, expected_stage):
    root, path, ledger, source = cutover_git_repository
    row = ledger["dependencies"][0]
    if mutation in {"json_only_add", "source_and_json_add"}:
        extra = dict(row, source="core/extra.py", count=1)
        ledger["dependencies"].append(extra)
        if mutation == "source_and_json_add":
            (root / "core/extra.py").write_bytes(source.encode())
    elif mutation == "count_increase":
        ledger["dependencies"][1]["count"] = 2
        (root / "core/other_debt.py").write_bytes((source * 2).encode())
    elif mutation.startswith("rename_"):
        field = mutation.removeprefix("rename_")
        row[field] += "_moved"
    elif mutation in {"remove_live_row", "migrate_and_remove"}:
        ledger["dependencies"].pop(0)
        if mutation == "migrate_and_remove":
            (root / "core/debt.py").write_bytes(b"from core.conversation_activity import mark_user_active\n")
    elif mutation == "reduce_count":
        row["count"] = 1
        (root / "core/debt.py").write_bytes(source.encode())
    path.write_bytes(json.dumps(ledger).encode())
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    convergence = subprocess.run(
        [sys.executable, "scripts/check_architecture_debt.py", "--baseline-ref", "HEAD"],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    if expected_stage == "convergence":
        assert convergence.returncode == 1 and "debt expansion" in convergence.stderr, convergence.stdout + convergence.stderr
        return
    assert convergence.returncode == 0, convergence.stdout + convergence.stderr
    scan = subprocess.run(
        [sys.executable, "-m", "pytest", "--noconftest", "-c", "pytest.ini", "-q",
         "tests/test_ar02_dependency_directions.py::test_migration_cutover_has_no_new_or_stale_dependency_debt"],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    if expected_stage == "scan":
        assert scan.returncode == 1 and "New dependency or stale debt" in scan.stdout, scan.stdout + scan.stderr
    else:
        assert scan.returncode == 0, scan.stdout + scan.stderr


def test_retired_t1_debt_cannot_be_restored_with_source_and_json(cutover_git_repository):
    """An old Git baseline is not permission to revive an eliminated T1 edge."""
    root, _, _, _ = cutover_git_repository
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--noconftest", "-c", "pytest.ini", "-q",
         "tests/test_ar02_dependency_directions.py::test_t1_http_dependency_boundaries_are_zero"],
        cwd=root, env=dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1 and "T1 must remain zero" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("source,gate", [
    ("from core.pipeline_registry import get as lookup\ndef run(): return lookup()\n", "test_t31_registry_sites_match_individual_review"),
    ("from core.memory.reality_context import RealityContextReader\nx = RealityContextReader(resolve_scope=lambda uid: pipeline.scope(uid))\n", "test_t31_domains_use_narrow_capabilities"),
    ("def check(self, char_id):\n return char_id or getattr(self, '_active_character_id', DEFAULT_CHAR_ID)\n", "test_t31_domains_use_narrow_capabilities"),
    ("x = ReplyGeneration(resolve_active_character_id=self.current_character_id)\n", "test_t31_domains_use_narrow_capabilities"),
])
def test_t31_actual_ci_assertion_rejects_fault_then_recovers(cutover_git_repository, source, gate):
    """Invoke the same pytest gate CI collects, in a real isolated Git repository."""
    root, _, _, _ = cutover_git_repository
    (root / "docs/refactors/t31-pipeline-registry-sites.json").write_bytes(b'{"retained": []}')
    for module in T31_COMPONENT_MODULES:
        path = root / Path(*module.split(".")).with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    (root / "core/pipeline.py").write_bytes(b"")
    target = root / ("integrations/probe.py" if "registry" in gate else
                     "core/owner_chat/reply_generation.py" if "DEFAULT_CHAR_ID" in source else "core/pipeline.py")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.encode())
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    command = [sys.executable, "-m", "pytest", "--noconftest", "-c", "pytest.ini", "-q",
               "tests/test_ar02_dependency_directions.py::" + gate]
    failed = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert failed.returncode == 1 and "AssertionError" in failed.stdout, failed.stdout + failed.stderr
    if "registry" in gate:
        assert "Unreviewed or stale registry call sites" in failed.stdout
        assert "integrations/probe.py" in failed.stdout
    elif "DEFAULT_CHAR_ID" in source:
        assert "generation cannot read or guess character state" in failed.stdout
    elif "ReplyGeneration" in source:
        assert "generation requires CharacterSession.current_character_id bound method" in failed.stdout
    else:
        assert "closure captures whole Pipeline" in failed.stdout
    target.write_bytes(b"")
    recovered = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr


@pytest.mark.parametrize("restore_source", [True, False])
@pytest.mark.parametrize("member", ["mark_user_active", "_owner_id"])
def test_retired_t2_activity_cannot_be_restored(cutover_git_repository, restore_source, member):
    """Run the CI assertion with an old ledger, with and without restored code."""
    root, path, ledger, _ = cutover_git_repository
    ledger["dependencies"].append({
        "source": "core/debt.py", "rule": "scheduler-state", "owner": "<module>",
        "target": "import:core.scheduler.loop." + member, "count": 1,
    })
    path.write_bytes(json.dumps(ledger).encode())
    if restore_source:
        (root / "core/debt.py").write_bytes(("from core.scheduler.loop import " + member + "\n").encode())
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--noconftest", "-c", "pytest.ini", "-q",
         "tests/test_ar02_dependency_directions.py::test_t2_conversation_activity_boundaries_are_zero"],
        cwd=root, env=dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"),
        capture_output=True, text=True, timeout=30,
    )
    expected = "T2 activity must remain zero" if restore_source else "T2 activity debt may not be restored"
    assert result.returncode == 1 and expected in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("event_name,event_ref", [
    ("pull_request", "refs/pull/1/merge"),
    ("push", "refs/heads/main"),
    ("push", "refs/heads/codex/fixture"),
])
def test_ci_entrypoint_selects_target_not_the_expanded_head(cutover_git_repository, event_name, event_ref):
    """Exercise --ci with real Git refs and deliberately misleading PR base SHA."""
    root, path, ledger, source = cutover_git_repository
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    if event_ref != "refs/heads/main":
        subprocess.run(["git", "checkout", "-b", "codex/fixture"], cwd=root, check=True, capture_output=True)
    ledger["dependencies"][0]["count"] = 3
    path.write_bytes(json.dumps(ledger).encode())
    (root / "core/debt.py").write_bytes((source * 3).encode())
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=.git/hooks",
                    "commit", "-m", "Synthetic expansion"], cwd=root, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "remote", "add", "origin", str(root)], cwd=root, check=True, capture_output=True)
    event = {"ref": event_ref, "before": before, "after": head,
             "pull_request": {"base": {"ref": "main", "sha": head}}}
    event_file = root / "event.json"
    event_file.write_bytes(json.dumps(event).encode())
    result = subprocess.run(
        [sys.executable, "scripts/check_architecture_debt.py", "--ci"], cwd=root,
        env=dict(os.environ, GITHUB_EVENT_NAME=event_name, GITHUB_EVENT_PATH=str(event_file)),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1 and "debt expansion" in result.stderr, result.stdout + result.stderr


@pytest.mark.parametrize("scenario", ["multi_commit", "single_commit", "zero", "unreadable", "malformed", "non_string", "missing"])
def test_main_push_ci_uses_event_before_and_fails_closed(cutover_git_repository, scenario):
    """A batch must compare to pre-push A, even if its last commit has no debt diff."""
    root, path, ledger, source = cutover_git_repository
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    def commit(message):
        subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=.git/hooks",
                        "commit", "-m", message], cwd=root, check=True, capture_output=True)

    if scenario == "multi_commit":
        ledger["dependencies"].append(dict(ledger["dependencies"][0], source="core/extra.py", count=1))
        (root / "core/extra.py").write_bytes(source.encode())
        path.write_bytes(json.dumps(ledger).encode())
        commit("B: introduce a real dependency and register its debt")
    (root / "unrelated.txt").write_bytes(b"Unrelated change; no architecture debt changes.\n")
    commit("C: unrelated change" if scenario == "multi_commit" else "B: unrelated change")
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    event = {"ref": "refs/heads/main", "before": before, "after": after}
    invalid = {"zero": "0" * 40, "unreadable": "1" * 40, "malformed": "not-a-sha", "non_string": None}
    if scenario in invalid:
        event["before"] = invalid[scenario]
    elif scenario == "missing":
        del event["before"]
    event_file = root / "event.json"
    event_file.write_bytes(json.dumps(event).encode())
    result = subprocess.run(
        [sys.executable, "scripts/check_architecture_debt.py", "--ci"], cwd=root,
        env=dict(os.environ, GITHUB_EVENT_NAME="push", GITHUB_EVENT_PATH=str(event_file)),
        capture_output=True, text=True, timeout=30,
    )
    if scenario == "single_commit":
        assert result.returncode == 0 and "baseline " + before in result.stdout, result.stdout + result.stderr
    elif scenario == "multi_commit":
        assert result.returncode == 1 and "debt expansion relative to " + before in result.stderr, result.stdout + result.stderr
    else:
        assert result.returncode == 1 and "Architecture debt check failed" in result.stderr, result.stdout + result.stderr
        assert "Debt convergence passed" not in result.stdout
        if scenario == "unreadable":
            assert invalid[scenario] in result.stderr
        else:
            assert "invalid push before SHA" in result.stderr
