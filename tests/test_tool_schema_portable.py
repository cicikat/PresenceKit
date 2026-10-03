"""B4 守门：所有工具 schema 经 portable_tool_spec 出口后必须对严格中转站安全。

新增工具（含 self_tool / MCP / discovery）会被自动遍历；不要在工具侧为单个 provider 打补丁。
"""
import json
import re

import pytest

from core import llm_protocol as lp
from core import tool_dispatcher as td

BANNED = {"additionalProperties", "$ref", "format", "default", "examples", "$schema"}
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _registry_tools():
    tools = []
    for name, spec in td._TOOL_REGISTRY.items():
        tools.append({"type": "function", "function": {
            "name": name, "description": spec.get("description", ""),
            "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
        }})
    return tools


def _extra_tools():
    from core.tool_discovery import CATEGORIES, PREFIX
    tools = [{"type": "function", "function": {
        "name": PREFIX + c, "description": "",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }} for c in list(CATEGORIES)[:3]]
    tools.append({"type": "function", "function": {  # MCP 模拟
        "name": "mcp.server/some tool:v1" + "x" * 80, "description": "",
        "parameters": {
            "type": "object", "$schema": "http://json-schema.org/draft-07/schema#",
            "properties": {
                "q": {"type": ["string", "null"], "format": "uri", "default": "a"},
                "opts": {"type": "object", "additionalProperties": True},
                "ref": {"$ref": "#/$defs/Foo"},
            },
            "$defs": {"Foo": {"type": "object", "properties": {"a": {"type": "integer"}}}},
            "additionalProperties": False,
        },
    }})
    tools.append({"type": "function", "function": {  # self_tool 模拟（名称由角色自定义）
        "name": "self_tool_查电影", "description": "x",
        "parameters": {"type": "object", "properties": {"args": {"type": "object"}}},
    }})
    return tools


def _walk(schema, path="$", top=True):
    if isinstance(schema, dict):
        for key in schema:
            assert key not in BANNED, f"{path}.{key}"
        if schema.get("type") == "object" and not top:
            assert schema.get("properties"), f"bare object at {path}"
        for key, value in schema.items():
            if key == "properties":
                for n, v in value.items():
                    _walk(v, f"{path}.{n}", False)
            else:
                _walk(value, f"{path}.{key}", False)
    elif isinstance(schema, list):
        for i, v in enumerate(schema):
            _walk(v, f"{path}[{i}]", False)


def _outputs(tools):
    yield "chat", [(t["function"]["name"], t["function"]["description"], t["function"]["parameters"])
                   for t in lp.chat_completions_tools(tools)]
    yield "responses", [(t["name"], t["description"], t["parameters"]) for t in lp.responses_tools(tools)]
    yield "anthropic", [(t["name"], t["description"], t["input_schema"]) for t in lp.anthropic_messages_tools(tools)]


def test_all_tools_portable_on_every_protocol():
    tools = _registry_tools() + _extra_tools()
    assert len(tools) > 30
    for proto, items in _outputs(tools):
        assert len(items) == len(tools)
        for name, desc, params in items:
            assert NAME_RE.match(name), (proto, name)
            assert desc.strip(), (proto, name)
            assert params["type"] == "object" and isinstance(params["properties"], dict), (proto, name)
            _walk(params, f"{proto}:{name}")


def test_listening_tools_included():
    from core.listening_tools import register_tools
    reg = {}
    register_tools(reg)
    assert reg
    for name, spec in reg.items():
        _, desc, params = lp.portable_tool_spec(name, spec.get("description"), spec.get("parameters"))
        assert desc and "additionalProperties" not in json.dumps(params)


def test_empty_placeholder_switch():
    tool = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
    assert lp.chat_completions_tools(tool)[0]["function"]["parameters"]["properties"] == {}
    assert "_noop" in lp.chat_completions_tools(tool, empty_placeholder=True)[0]["function"]["parameters"]["properties"]


def test_wire_name_maps_back_to_internal():
    internal = "mcp.server/some tool"
    wire, _, _ = lp.portable_tool_spec(internal, "d", {"type": "object", "properties": {}})
    assert wire != internal and NAME_RE.match(wire)
    assert lp.internal_tool_name(wire) == internal
    assert lp.wire_tool_name("ok_name-1") == "ok_name-1"


def test_bare_object_downgraded_and_args_coerced():
    schema = td._TOOL_REGISTRY["self_db_query"]["parameters"]
    _, _, params = lp.portable_tool_spec("self_db_query", "d", schema)
    assert params["properties"]["where"]["type"] == "string"
    out = lp.coerce_tool_args(schema, {"table": "t", "where": '{"a": 1}'})
    assert out["where"] == {"a": 1}
    assert lp.coerce_tool_args(schema, {"where": {"a": 1}})["where"] == {"a": 1}
    ins = td._TOOL_REGISTRY["self_db_insert"]["parameters"]
    assert lp.coerce_tool_args(ins, {"rows": ['{"x": 1}']})["rows"] == [{"x": 1}]
    assert lp.coerce_tool_args(ins, {"rows": '[{"x": 1}]'})["rows"] == [{"x": 1}]


@pytest.mark.asyncio
async def test_string_where_executes_via_dispatcher(monkeypatch):
    seen = {}

    async def fake(**kwargs):
        seen.update(kwargs)
        return "ok"

    spec = dict(td._TOOL_REGISTRY["self_db_query"])
    spec["func"] = fake
    monkeypatch.setitem(td._TOOL_REGISTRY, "self_db_query", spec)
    await td._execute_structured_impl(
        "self_db_query", {"table": "t", "where": '{"a": 1}'}, "u", "u", False, None,
        origin="assistant_loop", char_id="c",
    )
    assert seen.get("where") == {"a": 1}
