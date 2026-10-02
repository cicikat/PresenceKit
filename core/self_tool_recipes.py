"""Character-made tools as declarative recipes (work order T3).

A recipe is a JSON file ``self/tools/<name>.json`` inside the character's own
self space, so it reuses the self writer (revision, trash, quota, audit) and the
character can read or rewrite it with ``self_read`` / ``self_update``.

This is deliberately *not* code execution: a recipe is an ordered list of calls
to tools the character already has this turn.  Every step goes through
``execute_structured(origin="assistant_loop")`` so all existing gates, audit and
danger checks still apply; the restrictions below only stop a recipe from
reaching further than its author could reach directly.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core import character_self as self_mod
from core.character_self import SelfError, dumps
from core.sandbox import get_paths

RECIPE_DIR = "tools"
MAX_STEPS = 5
MAX_RECIPES = 20
MAX_PARAMS = 8
MAX_RECIPE_CHARS = 6000
MAX_PARAM_VALUE_CHARS = 2000
MAX_STEP_RESULT_CHARS = 1500
MAX_DESCRIPTION_CHARS = 200
LISTED_RECIPE_NAMES = 10
NAME_RE = re.compile(r"^[A-Za-z_一-鿿][\w一-鿿]{0,31}$")
PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
PARAM_TYPES = ("string", "number", "bool")
FORBIDDEN_CATEGORIES = frozenset({"system", "browser", "phone_control", "self_management"})
PRINCIPAL_KEYS = ("user_id", "uid", "char_id", "owner", "realm")
RUN_ORIGINS = frozenset({"user_live", "assistant_loop", "assistant_loop_relay"})


class RecipeError(Exception):
    def __init__(self, code: str, **extra: Any):
        super().__init__(code)
        self.code = code
        self.extra = extra


def _fail(code: str, **extra: Any) -> str:
    return dumps({"ok": False, "result": "denied", "code": code, **extra})


def _rel(name: str) -> str:
    return f"{RECIPE_DIR}/{name}.json"


def exposed_tool_names(uid: str, char_id: str, allowed_tool_names=None) -> set[str]:
    """Tools this character can actually call right now (Path C exposure)."""
    from core.tool_dispatcher import get_tools_schema
    from core.tool_exposure import filter_schemas, resolve

    exposure = resolve("path_c", char_id=char_id)
    schemas = filter_schemas(
        get_tools_schema(categories=list(exposure.categories), char_id=char_id, uid=uid), exposure,
    )
    names = {str((s.get("function") or s).get("name") or "") for s in schemas}
    names.discard("")
    if allowed_tool_names is not None:
        names &= set(allowed_tool_names)
    return names


def check_step_tool(tool: Any, exposed: set[str]) -> None:
    """Raise RecipeError unless a recipe step may call ``tool``."""
    from core.tool_dispatcher import _TOOL_REGISTRY

    if not isinstance(tool, str) or tool not in _TOOL_REGISTRY:
        raise RecipeError("step_tool_unknown", tool=str(tool)[:64])
    info = _TOOL_REGISTRY[tool]
    if tool.startswith("self_tool_"):
        raise RecipeError("step_recursion_forbidden", tool=tool)
    if (
        info.get("self_management") or info.get("self_management_read")
        or info.get("category") in FORBIDDEN_CATEGORIES
        or tool == "manage_self_capability"
    ):
        raise RecipeError("step_tool_forbidden", tool=tool, category=info.get("category"))
    if info.get("dangerous") or info.get("require_confirm"):
        # The dispatcher asks the user to confirm these; a recipe cannot pause mid-run.
        raise RecipeError("step_tool_forbidden", tool=tool, reason="needs_confirmation")
    if tool not in exposed:
        raise RecipeError("step_tool_not_exposed", tool=tool)


def _validate_recipe(recipe: dict, exposed: set[str]) -> None:
    params = recipe["params"]
    steps = recipe["steps"]
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        raise RecipeError("invalid_steps", max_steps=MAX_STEPS)
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("args", {}), dict):
            raise RecipeError("invalid_steps", step=index + 1)
        if any(key in step["args"] for key in PRINCIPAL_KEYS):
            raise RecipeError("step_principal_forbidden", step=index + 1)
        try:
            check_step_tool(step.get("tool"), exposed)
        except RecipeError as exc:
            exc.extra["step"] = index + 1
            raise
    if not isinstance(params, dict) or len(params) > MAX_PARAMS:
        raise RecipeError("invalid_params", max_params=MAX_PARAMS)
    for key, ptype in params.items():
        if not isinstance(key, str) or not NAME_RE.fullmatch(key) or ptype not in PARAM_TYPES:
            raise RecipeError("invalid_params", param=str(key)[:32], allowed_types=list(PARAM_TYPES))


def _normalize(name: Any, description: Any, params: Any, steps: Any) -> dict:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise RecipeError("invalid_identifier", field="name")
    if description is not None and not isinstance(description, str):
        raise RecipeError("invalid_value", field="description")
    clean_steps = []
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                clean_steps.append({"tool": step.get("tool"), "args": step.get("args") or {}})
            else:
                clean_steps.append(step)
    else:
        clean_steps = steps
    return {
        "name": name,
        "description": (description or "")[:MAX_DESCRIPTION_CHARS],
        "params": params if params is not None else {},
        "steps": clean_steps,
    }


def _load(name: str, user_id: str, char_id: str) -> dict:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise RecipeError("invalid_identifier", field="name")
    read = self_mod.read_self(_rel(name), user_id=user_id, char_id=char_id, origin="tool")
    if not read.get("ok"):
        code = read.get("code") or "recipe_not_readable"
        raise RecipeError("tool_not_found" if code in {"path_not_found", "not_a_file"} else code)
    try:
        recipe = json.loads(read.get("content") or "")
        if not isinstance(recipe, dict) or not isinstance(recipe.get("steps"), list):
            raise ValueError
        recipe.setdefault("params", {})
        recipe["_revision"] = read.get("revision")
        return recipe
    except ValueError as exc:
        raise RecipeError("recipe_invalid") from exc


async def self_tool_define(name, description, params, steps, *, user_id=None, char_id=None,
                           allowed_tool_names=None, **_ignored) -> str:
    try:
        recipe = _normalize(name, description, params, steps)
        exposed = exposed_tool_names(user_id, char_id, allowed_tool_names)
        _validate_recipe(recipe, exposed)
        content = json.dumps(recipe, ensure_ascii=False, indent=2)
        if len(content) > MAX_RECIPE_CHARS:
            raise RecipeError("recipe_too_large", max_chars=MAX_RECIPE_CHARS)
        rel = _rel(recipe["name"])
        existing = self_mod.read_self(rel, user_id=user_id, char_id=char_id, origin="tool")
        if existing.get("ok"):
            written = self_mod.update_self(
                rel, content, expected_revision=int(existing.get("revision") or 0),
                user_id=user_id, char_id=char_id, origin="tool",
            )
        else:
            if existing.get("code") not in {"path_not_found", "not_a_file"}:
                return dumps(existing)
            if len(recipe_names(user_id, char_id, limit=MAX_RECIPES + 1)) >= MAX_RECIPES:
                raise RecipeError("quota_exhausted", quota="recipes", max_recipes=MAX_RECIPES)
            written = self_mod.create_self(rel, content, user_id=user_id, char_id=char_id, origin="tool")
    except RecipeError as exc:
        return _fail(exc.code, **exc.extra)
    except SelfError as exc:
        return _fail(exc.code, **exc.extra)
    if not written.get("ok"):
        return dumps(written)
    return dumps({
        "ok": True, "result": "defined", "name": recipe["name"], "path": rel,
        "revision": written.get("revision"), "steps": len(recipe["steps"]),
        "note": "已存好。以后用 self_tool_run 调用；也可以用 self_read 看、self_update 改这份配方。",
    })


async def self_tool_list(*, user_id=None, char_id=None, **_ignored) -> str:
    names = recipe_names(user_id, char_id, limit=MAX_RECIPES)
    tools = []
    for name in names:
        try:
            recipe = _load(name, user_id, char_id)
        except RecipeError as exc:
            tools.append({"name": name, "error": exc.code})
            continue
        tools.append({
            "name": name, "description": recipe.get("description", ""),
            "params": recipe.get("params", {}),
            "steps": [s.get("tool") for s in recipe["steps"] if isinstance(s, dict)],
        })
    return dumps({"ok": True, "result": "listed", "tools": tools})


def _substitute(value: Any, declared: dict, values: dict) -> Any:
    if isinstance(value, str):
        whole = PLACEHOLDER_RE.fullmatch(value)
        if whole and whole.group(1) in declared:
            return values[whole.group(1)]

        def repl(match):
            key = match.group(1)
            if key not in declared:
                return match.group(0)
            item = values[key]
            return ("true" if item else "false") if isinstance(item, bool) else str(item)

        return PLACEHOLDER_RE.sub(repl, value)
    if isinstance(value, dict):
        return {key: _substitute(item, declared, values) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, declared, values) for item in value]
    return value


def _bind_args(declared: dict, args: Any) -> dict:
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise RecipeError("invalid_args")
    unknown = [key for key in args if key not in declared]
    missing = [key for key in declared if key not in args]
    if unknown or missing:
        raise RecipeError("invalid_args", unknown=[str(k)[:32] for k in unknown], missing=missing)
    bound = {}
    for key, ptype in declared.items():
        value = args[key]
        if ptype == "string":
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise RecipeError("invalid_args", param=key)
            value = str(value)
            if len(value) > MAX_PARAM_VALUE_CHARS:
                raise RecipeError("invalid_args", param=key, reason="too_long")
        elif ptype == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RecipeError("invalid_args", param=key)
        elif not isinstance(value, bool):
            raise RecipeError("invalid_args", param=key)
        bound[key] = value
    return bound


async def self_tool_run(name, args=None, *, user_id=None, char_id=None, origin="assistant_loop",
                        target_id=None, is_group=False, session_state=None,
                        allowed_tool_names=None, **_ignored) -> str:
    from core.tool_dispatcher import execute_structured

    if is_group or origin not in RUN_ORIGINS:
        return _fail("self_tool_run_unavailable")
    try:
        recipe = _load(name, user_id, char_id)
        exposed = exposed_tool_names(user_id, char_id, allowed_tool_names)
        declared = recipe.get("params") or {}
        # Re-check on every run: exposure and grants may have changed since define,
        # and the file may have been rewritten with self_update.
        _validate_recipe({"params": declared, "steps": recipe["steps"]}, exposed)
        values = _bind_args(declared, args)
    except RecipeError as exc:
        return _fail(exc.code, **exc.extra)
    except SelfError as exc:
        return _fail(exc.code, **exc.extra)

    done: list[dict[str, Any]] = []
    for index, step in enumerate(recipe["steps"], start=1):
        step_args = _substitute(step.get("args") or {}, declared, values)
        outcome = await execute_structured(
            step["tool"], step_args, user_id, target_id or user_id, False, session_state,
            origin="assistant_loop", char_id=char_id, allowed_tool_names=frozenset(exposed),
        )
        entry = {
            "step": index, "tool": step["tool"], "status": outcome.status,
            "result": str(outcome.result or outcome.confirmation_request or "")[:MAX_STEP_RESULT_CHARS],
        }
        done.append(entry)
        if outcome.status != "tool_executed":
            return dumps({
                "ok": False, "result": "stopped", "code": "step_failed", "name": name,
                "failed_step": index, "reason": outcome.status, "completed": done[:-1], "failed": entry,
            })
    return dumps({"ok": True, "result": "ran", "name": name, "steps": done})


def recipe_names(uid: str | None, char_id: str | None, *, limit: int = LISTED_RECIPE_NAMES) -> list[str]:
    """Recipe names from the ``self/tools`` directory listing; fail-soft, never creates content."""
    try:
        if not uid or not char_id:
            return []
        if not self_mod.load_grant(str(uid), str(char_id)).get("allowed"):
            return []
        root = get_paths().character_self_root(uid, char_id=char_id) / RECIPE_DIR
        if not root.is_dir():
            return []
        names = sorted(
            p.stem for p in root.iterdir()
            if p.is_file() and not p.is_symlink() and p.suffix == ".json" and NAME_RE.fullmatch(p.stem)
        )
        return names[:limit]
    except Exception:
        return []
