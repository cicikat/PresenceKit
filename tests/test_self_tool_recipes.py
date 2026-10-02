"""Character-made tools as declarative recipes (work order T3)."""

from __future__ import annotations

import json

import pytest

from core import character_self as self_mod
from core import character_self_db as db
from core import self_tool_recipes as recipes
from core import tool_dispatcher
from core.tool_discovery import ToolDiscovery
from tests.fixtures.public_assets import TEST_CHAR_ID

_UID = "owner-one"
_CHAR = TEST_CHAR_ID


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    status = "idle"

    def set_waiting_confirm(self, *a):
        self.status = self.WAITING_CONFIRM


def _j(raw) -> dict:
    return json.loads(raw)


@pytest.fixture
def env(sandbox, monkeypatch):
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "safe")
    calls: list[tuple[str, dict]] = []

    async def echo(*, user_id=None, **args):
        calls.append(("echo", args))
        return f"echo:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"

    async def boom(*, user_id=None, **args):
        calls.append(("boom", args))
        raise RuntimeError("kaboom")

    def spec(func, category="info", **extra):
        return {"func": func, "description": "t", "dangerous": False, "category": category,
                "parameters": {"type": "object", "properties": {}}, "examples": ["x"], "keywords": ["x"], **extra}

    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "t_echo", spec(echo))
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "t_boom", spec(boom))
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "t_system", spec(echo, category="system"))
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "t_danger", spec(echo, dangerous=True))
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "t_desktop", spec(echo, category="desktop"))
    return calls


def _kw(uid=_UID, char=_CHAR, **extra):
    return {"user_id": uid, "char_id": char, **extra}


async def _define(name="找电影", steps=None, params=None, **kw):
    return _j(await recipes.self_tool_define(
        name, "想找电影时用", params if params is not None else {"q": "string"},
        steps if steps is not None else [{"tool": "t_echo", "args": {"text": "{q} 影评"}}], **(kw or _kw()),
    ))


@pytest.mark.asyncio
async def test_define_list_run_with_param_substitution(env):
    defined = await _define()
    assert defined["ok"] and defined["path"] == "tools/找电影.json" and defined["steps"] == 1
    # the recipe is a plain self file the character can read
    raw = self_mod.read_self("tools/找电影.json", user_id=_UID, char_id=_CHAR)
    assert raw["ok"] and json.loads(raw["content"])["steps"][0]["tool"] == "t_echo"

    listed = _j(await recipes.self_tool_list(**_kw()))
    assert listed["tools"][0]["name"] == "找电影" and listed["tools"][0]["steps"] == ["t_echo"]

    ran = _j(await recipes.self_tool_run("找电影", {"q": "海上钢琴师"}, **_kw(origin="assistant_loop", session_state=_Session())))
    assert ran["ok"] and ran["steps"][0]["status"] == "tool_executed"
    assert env == [("echo", {"text": "海上钢琴师 影评"})]


@pytest.mark.asyncio
async def test_only_declared_params_are_substituted_and_values_are_not_reexpanded(env):
    await _define(params={"q": "string"}, steps=[{"tool": "t_echo", "args": {"a": "{q}|{other}", "n": ["{q}"]}}])
    await recipes.self_tool_run("找电影", {"q": "{other}"}, **_kw(origin="assistant_loop", session_state=_Session()))
    assert env[0][1] == {"a": "{other}|{other}", "n": ["{other}"]}


@pytest.mark.asyncio
async def test_typed_params_and_bad_args(env):
    await _define(params={"n": "number", "flag": "bool"}, steps=[{"tool": "t_echo", "args": {"n": "{n}", "f": "{flag}", "s": "n={n}"}}])
    run = lambda args: recipes.self_tool_run("找电影", args, **_kw(origin="assistant_loop", session_state=_Session()))
    ok = _j(await run({"n": 3, "flag": True}))
    assert ok["ok"] and env[-1][1] == {"n": 3, "f": True, "s": "n=3"}
    for bad in ({"n": "x", "flag": True}, {"n": 1}, {"n": 1, "flag": True, "extra": 1}, "nope"):
        assert _j(await run(bad))["code"] == "invalid_args"


@pytest.mark.asyncio
async def test_define_rejects_illegal_steps(env):
    cases = {
        "step_tool_forbidden": [{"tool": "t_system"}, {"tool": "t_danger"}, {"tool": "manage_self_capability"}, {"tool": "process_run"}],
        "step_recursion_forbidden": [{"tool": "self_tool_run", "args": {"name": "x"}}, {"tool": "self_tool_define"}],
        "step_tool_unknown": [{"tool": "no_such_tool"}, {"tool": ""}, {"tool": 5}],
        "step_tool_not_exposed": [{"tool": "t_desktop"}],
        "invalid_steps": [[]],
    }
    from core import tool_exposure
    from types import SimpleNamespace
    # t_desktop is a real category but this character's whitelist excludes it
    import core.character_loader as loader
    orig = loader.load
    loader.load = lambda _c: SimpleNamespace(presence_ext={"tool_categories": ["info", "self"]})
    try:
        for code, step_sets in cases.items():
            for step in step_sets:
                steps = step if isinstance(step, list) else [step]
                result = await _define(steps=steps)
                assert result["ok"] is False and result["code"] == code, (code, step, result)
    finally:
        loader.load = orig
    too_many = await _define(steps=[{"tool": "t_echo"}] * 6)
    assert too_many["code"] == "invalid_steps"
    spoof = await _define(steps=[{"tool": "t_echo", "args": {"user_id": "x"}}])
    assert spoof["code"] == "step_principal_forbidden"
    assert (await _define(params={"a b": "string"}))["code"] == "invalid_params"
    assert (await _define(params={"a": "list"}))["code"] == "invalid_params"
    assert (await _define(name="bad name"))["code"] == "invalid_identifier"
    assert _j(await recipes.self_tool_list(**_kw()))["tools"] == []  # nothing was stored
    assert tool_exposure  # imported for clarity of intent


@pytest.mark.asyncio
async def test_failure_stops_and_reports_partial_results(env):
    await _define(steps=[
        {"tool": "t_echo", "args": {"i": 1}}, {"tool": "t_boom"}, {"tool": "t_echo", "args": {"i": 3}},
    ], params={})
    out = _j(await recipes.self_tool_run("找电影", {}, **_kw(origin="assistant_loop", session_state=_Session())))
    assert out["ok"] is False and out["code"] == "step_failed" and out["failed_step"] == 2
    assert [s["step"] for s in out["completed"]] == [1] and out["completed"][0]["status"] == "tool_executed"
    assert [c[0] for c in env] == ["echo", "boom"]  # step 3 never ran


@pytest.mark.asyncio
async def test_update_via_self_update_takes_effect_and_is_revalidated(env):
    await _define()
    path = "tools/找电影.json"
    read = self_mod.read_self(path, user_id=_UID, char_id=_CHAR)
    recipe = json.loads(read["content"])
    recipe["steps"] = [{"tool": "t_echo", "args": {"text": "新版 {q}"}}]
    upd = self_mod.update_self(path, json.dumps(recipe), expected_revision=read["revision"], user_id=_UID, char_id=_CHAR)
    assert upd["ok"]
    await recipes.self_tool_run("找电影", {"q": "a"}, **_kw(origin="assistant_loop", session_state=_Session()))
    assert env[-1][1] == {"text": "新版 a"}

    # a hand-edited recipe cannot smuggle in a forbidden tool
    recipe["steps"] = [{"tool": "t_system", "args": {}}]
    read = self_mod.read_self(path, user_id=_UID, char_id=_CHAR)
    self_mod.update_self(path, json.dumps(recipe), expected_revision=read["revision"], user_id=_UID, char_id=_CHAR)
    before = len(env)
    bad = _j(await recipes.self_tool_run("找电影", {"q": "a"}, **_kw(origin="assistant_loop", session_state=_Session())))
    assert bad["code"] == "step_tool_forbidden" and len(env) == before

    # redefining the same name goes through update_self and bumps the revision
    again = await _define(steps=[{"tool": "t_echo", "args": {"text": "再定义"}}], params={})
    assert again["ok"] and again["revision"] > 1


@pytest.mark.asyncio
async def test_recipes_are_isolated_per_character_and_owner(env):
    await _define()
    assert _j(await recipes.self_tool_list(**_kw("owner-two")))["tools"] == []
    assert _j(await recipes.self_tool_list(**_kw(_UID, "other_character")))["tools"] == []
    missing = _j(await recipes.self_tool_run("找电影", {"q": "a"}, **_kw("owner-two", origin="assistant_loop")))
    assert missing["code"] == "tool_not_found"


@pytest.mark.asyncio
async def test_grant_revocation_and_context_limits(env):
    await _define()
    assert recipes.recipe_names(_UID, _CHAR) == ["找电影"]
    self_mod.set_grant(_UID, _CHAR, False)
    assert recipes.recipe_names(_UID, _CHAR) == []
    assert _j(await recipes.self_tool_list(**_kw()))["tools"] == []
    revoked = _j(await recipes.self_tool_run("找电影", {"q": "a"}, **_kw(origin="assistant_loop")))
    assert revoked["code"] == "self_revoked"
    self_mod.set_grant(_UID, _CHAR, True)
    for kwargs in ({"origin": "autonomy_loop"}, {"origin": "assistant_loop", "is_group": True}):
        out = _j(await recipes.self_tool_run("找电影", {"q": "a"}, **_kw(**kwargs)))
        assert out["code"] == "self_tool_run_unavailable"
    assert env == []


@pytest.mark.asyncio
async def test_recipe_quota(env):
    for i in range(recipes.MAX_RECIPES):
        assert (await _define(name=f"r{i}", params={}, steps=[{"tool": "t_echo"}]))["ok"]
    over = await _define(name="overflow", params={}, steps=[{"tool": "t_echo"}])
    assert over["code"] == "quota_exhausted"
    assert (await _define(name="r0", params={}, steps=[{"tool": "t_echo", "args": {"a": 1}}]))["ok"]  # redefine is fine


@pytest.mark.asyncio
async def test_dispatcher_integration_runs_steps_through_gates(env):
    s = _Session()
    defined = await tool_dispatcher.execute_structured(
        "self_tool_define",
        {"name": "查表", "description": "d", "params": {"t": "string"},
         "steps": [{"tool": "self_db_tables"}, {"tool": "t_echo", "args": {"t": "{t}"}}]},
        _UID, _UID, False, s, origin="assistant_loop", char_id=_CHAR,
    )
    assert defined.status == "tool_executed" and "defined" in defined.result
    ran = await tool_dispatcher.execute_structured(
        "self_tool_run", {"name": "查表", "args": {"t": "x"}}, _UID, _UID, False, s,
        origin="assistant_loop", char_id=_CHAR,
    )
    assert ran.status == "tool_executed" and "ran" in ran.result and env[-1][1] == {"t": "x"}
    spoof = await tool_dispatcher.execute_structured(
        "self_tool_run", {"name": "查表", "char_id": "other"}, _UID, _UID, False, s,
        origin="assistant_loop", char_id=_CHAR,
    )
    assert "grant_principal_mismatch" in spoof.result
    group = await tool_dispatcher.execute_structured(
        "self_tool_run", {"name": "查表", "args": {"t": "x"}}, _UID, _UID, True, s,
        origin="assistant_loop", char_id=_CHAR,
    )
    assert group.status == "tool_failed"
    # step restriction: caller allow-list narrows what a recipe may reach
    narrow = await tool_dispatcher.execute_structured(
        "self_tool_run", {"name": "查表", "args": {"t": "x"}}, _UID, _UID, False, s,
        origin="assistant_loop", char_id=_CHAR,
        allowed_tool_names=frozenset({"self_tool_run", "self_db_tables"}),
    )
    assert "step_tool_not_exposed" in narrow.result


def test_registry_entries_and_discovery_note(env):
    for name in ("self_tool_define", "self_tool_list", "self_tool_run"):
        spec = tool_dispatcher._TOOL_REGISTRY[name]
        assert spec["category"] == "self" and spec["examples"] and spec["keywords"]
    registry = {"a": {"category": "self"}}
    schemas = [{"type": "function", "function": {"name": "a"}}]
    entry = ToolDiscovery(schemas, registry, notes={"self": "你做过的工具：甲、乙。"}).schemas()[0]
    assert "你做过的工具：甲、乙。" in entry["function"]["description"]


@pytest.mark.asyncio
async def test_discovery_note_and_observability_show_names_only(env):
    await _define()
    assert recipes.recipe_names(_UID, _CHAR) == ["找电影"]
    snap = self_mod.observability_snapshot(_UID, _CHAR)
    assert snap["self_tools"]["names"] == ["找电影"] and snap["self_tools"]["count"] == 1
    assert "影评" not in json.dumps(snap, ensure_ascii=False)
    # keep db import used: recipes can target the structured library
    assert db.IDENT_RE.fullmatch("找电影")
