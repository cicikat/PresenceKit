"""Discovery is a protocol step, never execution or additional authority."""
import asyncio
import json

import pytest

from core.llm_client import ChatTurn
from core.tool_discovery import CATEGORIES, MAX_LISTED_TOOLS, ToolDiscovery
from tests.test_tool_loop import _make_pipeline, _patch_tool_loop_config, _patch_tools_schema


def call(name, arguments=None, call_id=None):
    return {"id": call_id or name, "name": name, "arguments": {} if arguments is None else arguments}


def turn(*calls, content=""):
    return ChatTurn(content=content, tool_calls=list(calls), assistant_message={
        "role": "assistant", "content": content or None,
        "tool_calls": [{"id": c["id"], "type": "function", "function": {
            "name": c["name"], "arguments": json.dumps(c["arguments"]),
        }} for c in calls],
    })


@pytest.fixture
def harness(monkeypatch):
    _patch_tool_loop_config(monkeypatch, exclude_tools=[], max_steps=3)
    _patch_tools_schema(monkeypatch, ["web_search", "get_episodic"])
    monkeypatch.setattr("core.character_loader.load", lambda _: type("Char", (), {"presence_ext": {}})())
    monkeypatch.setattr("core.character_name_provider.get_char_name", lambda *a, **_: "测试角色")
    monkeypatch.setattr("core.self_management.policy.feature_enabled", lambda: False)
    requests, executions = [], []
    script = []

    async def chat_turn(messages, tools, **kwargs):
        requests.append({"messages": list(messages), "names": [t["function"]["name"] for t in tools], "tools": tools})
        item = script.pop(0)
        if callable(item):
            return await item()
        return item

    async def execute(name, args, *a, **kw):
        from core.tool_dispatcher import ToolExecutionOutcome
        executions.append((name, args, kw))
        return ToolExecutionOutcome(status="tool_executed", result="工具已执行：测试结果")

    async def final(*a, **kw):
        return "自然回复"

    monkeypatch.setattr("core.llm_client.chat_turn", chat_turn)
    monkeypatch.setattr("core.tool_dispatcher.execute_structured", execute)
    monkeypatch.setattr("core.llm_client.chat", final)
    pipeline = _make_pipeline()
    monkeypatch.setattr(pipeline, "_anti_collapse_prefix_retry", lambda msgs, text, **kw: final())

    async def run(**kwargs):
        return await pipeline.run_agentic_loop([{"role": "user", "content": "查询"}],
            uid="u1", char_id="c1", session_state=object(), **kwargs)

    return script, requests, executions, run


@pytest.mark.asyncio
async def test_first_request_only_categories_then_selected_schema(harness):
    script, requests, executions, run = harness
    script.extend([turn(call("load_tools_info")), turn(call("web_search", {"query": "天气"})), turn(content="好了")])
    await run()
    assert requests[0]["names"] == ["load_tools_info", "load_tools_memory"]
    assert requests[1]["names"] == ["web_search", "load_tools_memory"]
    assert [e[0] for e in executions] == ["web_search"]
    # 折叠隐藏定义，但入口描述要点出 get_episodic 的存在，参数 schema 仍不下发。
    entry = next(t for t in requests[0]["tools"] if t["function"]["name"] == "load_tools_memory")
    assert "get_episodic" in entry["function"]["description"]
    assert entry["function"]["parameters"]["properties"] == {}


@pytest.mark.asyncio
async def test_used_tool_stream_does_not_publish_loading_announcement(harness, monkeypatch):
    script, requests, executions, run = harness
    script.extend([turn(call("load_tools_info")), turn(call("web_search", {"query": "天气"})), turn(content="好了")])

    async def leaking_stream(*args, **kwargs):
        yield "加载辅助"
        yield "工具中，请稍后..."

    monkeypatch.setattr("core.pipeline.Pipeline.run_llm_stream", leaking_stream)
    result = await run(stream=True)
    visible = "".join([piece async for piece in result])
    assert executions
    assert visible == "自然回复"


@pytest.mark.asyncio
async def test_unloaded_and_same_response_guesses_never_execute(harness):
    script, requests, executions, run = harness
    script.extend([turn(call("load_tools_info"), call("web_search"), call("get_episodic")),
                   turn(call("web_search")), turn(content="好了")])
    await run()
    assert len(executions) == 1
    assert "本轮未提供" in str(requests[1]["messages"])


@pytest.mark.asyncio
async def test_discovery_preserves_single_business_step(harness, monkeypatch):
    script, requests, executions, run = harness
    _patch_tool_loop_config(monkeypatch, max_steps=1, exclude_tools=[])
    script.extend([turn(call("load_tools_info")), turn(call("web_search"))])
    await run()
    assert len(requests) == 2
    assert len(executions) == 1


@pytest.mark.asyncio
async def test_discovery_does_not_grant_completion_evidence(harness, monkeypatch):
    script, requests, executions, run = harness
    flags = []
    async def _spy(self, messages, reply, *, successful_tool_call=None, **kw):
        flags.append(successful_tool_call)
        return reply

    monkeypatch.setattr("core.pipeline.Pipeline._guard_unverified_claim", _spy)
    script.extend([turn(call("load_tools_info")), turn(content="已完成")])
    await run(tool_call_required=True, required_tool_names=["web_search"])
    assert executions == []
    assert flags == [False]


@pytest.mark.asyncio
async def test_bad_repeated_discovery_is_bounded(harness, monkeypatch):
    script, requests, executions, run = harness
    _patch_tool_loop_config(monkeypatch, max_steps=1, exclude_tools=[])
    script.extend([turn(call("load_tools_info", {"execute": True})) for _ in range(3)])
    await run()
    assert len(requests) == 3  # two discovery slots plus one business slot
    assert executions == []


@pytest.mark.asyncio
async def test_discovery_timeout_uses_same_budget(harness, monkeypatch):
    script, requests, executions, run = harness
    _patch_tool_loop_config(monkeypatch, total_timeout_s=0.02, exclude_tools=[])

    async def slow():
        await asyncio.sleep(1)
        return turn(call("load_tools_info"))

    script.append(slow)
    await run()
    assert executions == []
    from core.runtime_signal_observability import snapshot
    assert any(s["category"] == "tool_loop_discovery" and s["code"] == "timeout" for s in snapshot()["signals"])


@pytest.mark.asyncio
async def test_empty_caller_allowlist_exposes_no_categories(harness):
    script, requests, executions, run = harness
    script.append(turn(content="你好"))
    await run(allowed_tool_names=frozenset())
    assert requests[0]["names"] == []
    assert executions == []


@pytest.mark.asyncio
async def test_discovery_state_does_not_cross_turns(harness):
    script, requests, executions, run = harness
    for _ in range(2):
        script.extend([turn(call("load_tools_info")), turn(content="你好")])
        await run()
    assert requests[0]["names"] == requests[2]["names"]


@pytest.mark.asyncio
async def test_relay_discovers_before_execution(harness, monkeypatch):
    script, requests, executions, run = harness
    probes = []

    async def chat(messages, tools=None, call_category="chat", **kwargs):
        if call_category == "probe":
            probes.append(tools)
            name = "load_tools_info" if len(probes) == 1 else "web_search"
            return "__TOOL_CALL__:" + json.dumps([call(name)])
        return "自然回复"

    monkeypatch.setattr("core.llm_client.chat", chat)
    script.extend([turn(content="{true: 查询天气}"), turn(content="{true: 查询天气}"), turn(content="好了")])
    await run()
    assert [t["function"]["name"] for t in probes[0]] == ["load_tools_info", "load_tools_memory"]
    assert executions[0][2]["origin"] == "assistant_loop_relay"
    assert len(executions) == 1


def test_all_categories_no_truncation_and_schema_integrity():
    registry, schemas = {}, []
    for category in CATEGORIES:
        for index in range(25):
            name = f"test_{category}_{index}"
            registry[name] = {"category": category}
            schemas.append({"type": "function", "function": {"name": name, "parameters": {
                "type": "object", "properties": {"value": {"type": ["string", "null"]}},
            }}})
    discovery = ToolDiscovery(schemas, registry)
    assert len(discovery.schemas()) == len(CATEGORIES)
    # 入口描述必须列出分类内的工具名（有界），否则模型无法得知折叠后面有什么。
    for entry in discovery.schemas():
        description = entry["function"]["description"]
        assert "test_" in description and "等 25 个" in description
    for category in CATEGORIES:
        assert discovery.load("load_tools_" + category, {})[1]
    assert len(discovery.schemas()) == 25 * len(CATEGORIES)
    assert discovery.schemas() == schemas
    assert not discovery.load("load_tools_info", {})[1]
    assert not discovery.load("load_tools_unknown", {})[1]


@pytest.mark.asyncio
async def test_runtime_observation_endpoint_includes_discovery(harness):
    script, requests, executions, run = harness
    script.append(turn(content="你好"))
    await run()
    from admin.routers.observability import runtime_signals
    result = await runtime_signals(_auth=object())
    assert any(s["code"] == "initial_surface" for s in result["signals"])


@pytest.mark.asyncio
async def test_responses_discovery_preserves_call_ids(harness):
    script, requests, executions, run = harness
    discovery_turn = turn(call("load_tools_info", call_id="discover_response"))
    discovery_turn.continuation_items = [{"type": "function_call", "call_id": "discover_response",
                                          "name": "load_tools_info", "arguments": "{}"}]
    script.extend([discovery_turn, turn(content="你好")])
    await run()
    from core.llm_protocol import responses_input
    wire = responses_input(requests[1]["messages"])
    assert any(item.get("type") == "function_call_output" and item.get("call_id") == "discover_response" for item in wire)
    assert executions == []


@pytest.mark.asyncio
async def test_self_management_grant_is_discovered_then_native_only(harness, monkeypatch):
    script, requests, executions, run = harness
    monkeypatch.setattr("core.self_management.policy.feature_enabled", lambda: True)
    monkeypatch.setattr("core.self_management.service.view", lambda *a: {"revision": 1, "capabilities": [{
        "capability_id": "test", "system_available": True,
        "grant": {"allowed": True, "mutable_by_agent": True},
    }]})
    script.extend([turn(call("load_tools_self_management")), turn(call("manage_self_capability")), turn(content="你好")])
    await run()
    assert "load_tools_self_management" in requests[0]["names"]
    assert "manage_self_capability" not in requests[0]["names"]
    assert executions[0][2]["origin"] == "assistant_self_management"


def test_self_entry_lists_all_self_tools_and_never_folds():
    from core.tool_dispatcher import _TOOL_REGISTRY
    self_names = sorted(n for n, i in _TOOL_REGISTRY.items() if i.get("category") == "self")
    assert {"self_list", "self_read", "self_create", "self_update", "self_move",
            "self_delete", "self_restore"} <= set(self_names)
    schemas = [{"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
               for n in _TOOL_REGISTRY]
    discovery = ToolDiscovery(schemas, _TOOL_REGISTRY)
    entries = {e["function"]["name"]: e["function"]["description"] for e in discovery.schemas()}
    assert "load_tools_self" in entries
    for name in self_names:
        assert name in entries["load_tools_self"]
    # Splitting ``info`` must keep every entry under the listing cap (no "等 N 个" folding).
    for category in ("self", "schedule", "life", "info"):
        count = len(discovery.groups.get(category, []))
        assert count <= MAX_LISTED_TOOLS, (category, count)


def test_routing_hint_only_names_exposed_categories():
    registry = {"a": {"category": "self"}, "b": {"category": "memory"}}
    schemas = [{"type": "function", "function": {"name": n}} for n in registry]
    hint = ToolDiscovery(schemas, registry).routing_hint()
    assert "→ self" in hint and "→ memory" in hint
    assert "→ schedule" not in hint
