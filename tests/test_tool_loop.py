"""
tests/test_tool_loop.py — Brief 28 · tool loop 多步工具执行器

覆盖 cc-tasks/28-tool-loop多步工具执行器.md §4 的 12 项测试。
LLM 全 mock：core.llm_client.chat_turn / chat / chat_stream 按脚本吐结果，
不发真实网络请求。tool_dispatcher.execute 多数场景也 mock（脚本化 result/ask_confirm），
只有「action_trace 落痕」一项使用真实 execute() 走真实落盘（sandbox 隔离）。
"""

from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import json

import pytest

from core.llm_client import ChatTurn


# ── 公共 helper ──────────────────────────────────────────────────────────────

def _make_pipeline():
    from core.pipeline import Pipeline
    return Pipeline.__new__(Pipeline)


@pytest.fixture(autouse=True)
def _patch_char_name(monkeypatch):
    """_voice_reanchor() 需要 get_char_name()；测试不关心真实角色资产，固定返回一个名字。"""
    monkeypatch.setattr("core.character_name_provider.get_char_name", lambda char_id=None: "小星")


def _patch_tool_loop_config(monkeypatch, **overrides):
    cfg = {
        "tool_loop": {
            "max_steps": 5,
            "total_timeout_s": 90,
            "categories": ["info", "desktop", "memory"],
            "exclude_tools": ["toy_vibrate", "toy_stop", "toy_pattern", "write_toy_file"],
        }
    }
    cfg["tool_loop"].update(overrides)
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    # model_registry imports get_config at module scope.  Isolate these loop
    # tests from any local model-specific tool preset in ignored config.yaml;
    # tests that exercise a binding replace these two fakes explicitly.
    monkeypatch.setattr("core.model_registry._get_preset_config", lambda: {"presets": {}})
    monkeypatch.setattr("core.model_registry._resolve_preset_name", lambda *args, **kwargs: "legacy")
    return cfg


def _patch_tools_schema(monkeypatch, names):
    from core.tool_dispatcher import _TOOL_REGISTRY
    for name in names:
        if name not in _TOOL_REGISTRY:
            monkeypatch.setitem(_TOOL_REGISTRY, name, {"category": "info"})
    schema = [
        {"type": "function", "function": {"name": n, "description": "", "parameters": {"type": "object", "properties": {}}}}
        for n in names
    ]
    monkeypatch.setattr("core.tool_dispatcher.get_tools_schema", lambda categories=None, **kwargs: schema)


def _script_chat_turn(monkeypatch, turns: list[ChatTurn]):
    calls: list[dict] = []
    it = iter(turns)

    async def _fake(messages, tools, **kw):
        # Simulate the model's discovery round before the business script.
        entries = [t["function"]["name"] for t in tools if t["function"]["name"].startswith("load_tools_")]
        if entries:
            tc = [{"id": name, "name": name, "arguments": {}} for name in entries]
            return ChatTurn(content="", tool_calls=tc, assistant_message={"role": "assistant", "content": None,
                "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": "{}"}} for c in tc]})
        calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return next(it)

    monkeypatch.setattr("core.llm_client.chat_turn", _fake)
    return calls


def _script_execute(monkeypatch, results: list[tuple]):
    calls: list[dict] = []
    it = iter(results)

    async def _fake(tool_name, tool_args, user_id, target_id, is_group, session_state, *, origin, char_id,
                     bypass_read_log=False):
        calls.append({
            "tool_name": tool_name, "tool_args": tool_args,
            "user_id": user_id, "target_id": target_id,
            "is_group": is_group, "origin": origin, "char_id": char_id,
            "bypass_read_log": bypass_read_log,
        })
        return next(it)

    monkeypatch.setattr("core.tool_dispatcher.execute", _fake)
    return calls


def _patch_final_chat(monkeypatch, text: str):
    calls: list[list[dict]] = []

    async def _fake(messages, tools=None, max_tokens_override=None, use_vision=False, call_category="chat",
                     char_id=None, is_proactive=False):
        calls.append([dict(m) for m in messages])
        return text

    monkeypatch.setattr("core.llm_client.chat", _fake)
    return calls


@pytest.mark.asyncio
async def test_per_turn_exclude_tools_hides_only_completed_fast_path_tool(monkeypatch):
    _patch_tool_loop_config(monkeypatch, exclude_tools=[])
    _patch_tools_schema(monkeypatch, ["get_time", "web_search"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(content="direct response", tool_calls=[], assistant_message={"role": "assistant", "content": "direct response"}),
    ])

    result = await _make_pipeline().run_agentic_loop(
        [{"role": "user", "content": "what time is it"}],
        uid="u1",
        char_id=TEST_CHAR_ID,
        session_state=object(),
        exclude_tools={"get_time"},
    )

    assert result == "direct response"
    exposed_names = [
        (schema.get("function") or schema).get("name")
        for schema in chat_turn_calls[0]["tools"]
    ]
    assert "get_time" not in exposed_names
    assert "web_search" in exposed_names


@pytest.mark.asyncio
async def test_without_per_turn_exclusion_native_fc_can_call_get_time(monkeypatch):
    _patch_tool_loop_config(monkeypatch, exclude_tools=[])
    _patch_tools_schema(monkeypatch, ["get_time", "web_search"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(
            content="",
            tool_calls=[{"id": "time_1", "name": "get_time", "arguments": {}}],
            assistant_message={"role": "assistant", "content": None},
        ),
        ChatTurn(content="已查到时间", tool_calls=[], assistant_message={"role": "assistant", "content": "已查到时间"}),
    ])
    execute_calls = _script_execute(monkeypatch, [("工具已执行：get_time，结果：当前时间 10:00", None)])
    _patch_final_chat(monkeypatch, text="现在十点。")

    result = await _make_pipeline().run_agentic_loop(
        [{"role": "user", "content": "what time is it"}],
        uid="u1",
        char_id=TEST_CHAR_ID,
        session_state=object(),
        exclude_tools=set(),
    )

    assert result == "现在十点。"
    assert execute_calls[0]["tool_name"] == "get_time"
    exposed_names = [
        (schema.get("function") or schema).get("name")
        for schema in chat_turn_calls[0]["tools"]
    ]
    assert "get_time" in exposed_names


# ── 1. 自然终止（从未调用工具）───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_natural_termination_no_tool(monkeypatch):
    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["web_search"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(content="直接回答，不用工具", tool_calls=[],
                 assistant_message={"role": "assistant", "content": "直接回答，不用工具"}),
    ])
    execute_calls = _script_execute(monkeypatch, [])

    pipeline = _make_pipeline()
    result = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "你好"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    assert result == "直接回答，不用工具"
    assert len(chat_turn_calls) == 1
    assert execute_calls == []


# ── 1b. 网关返回空自然终止 → 不带 tools 强制收尾 ───────────────────────────

@pytest.mark.asyncio
async def test_empty_natural_termination_falls_back_to_tool_free_final(monkeypatch):
    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["web_search"])
    _script_chat_turn(monkeypatch, [
        ChatTurn(content="", tool_calls=[], assistant_message={"role": "assistant", "content": ""}),
    ])
    execute_calls = _script_execute(monkeypatch, [])
    final_calls = _patch_final_chat(monkeypatch, text="降级后的正常回复")

    pipeline = _make_pipeline()
    result = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "你好"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    assert result == "降级后的正常回复"
    assert execute_calls == []
    assert len(final_calls) == 1
    assert any(
        m.get("role") == "system" and "工具用完了" in m.get("content", "")
        for m in final_calls[0]
    )


# ── 2 + 4. 两步循环自然终止（用过工具），含 voice_reanchor 收尾 ──────────────

@pytest.mark.asyncio
async def test_two_step_natural_termination_with_tool_includes_reanchor(monkeypatch):
    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["web_search"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(
            content="",
            tool_calls=[{"id": "call_1", "name": "web_search", "arguments": {"query": "天气"}}],
            assistant_message={"role": "assistant", "content": None},
        ),
        ChatTurn(content="查到了", tool_calls=[], assistant_message={"role": "assistant", "content": "查到了"}),
    ])
    execute_calls = _script_execute(monkeypatch, [("晴，25度", None)])
    final_calls = _patch_final_chat(monkeypatch, text="今天挺晴朗的～")

    pipeline = _make_pipeline()
    result = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "今天天气"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    assert result == "今天挺晴朗的～"
    assert len(chat_turn_calls) == 2
    assert len(execute_calls) == 1
    assert execute_calls[0]["tool_name"] == "web_search"
    assert execute_calls[0]["origin"] == "assistant_loop"

    final_messages = final_calls[-1]
    tool_msgs = [m for m in final_messages if m.get("role") == "tool" and not m.get("tool_call_id", "").startswith("load_tools_")]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert "<<<TOOL_DATA_START>>>" in tool_msgs[0]["content"]
    assert "<<<TOOL_DATA_END>>>" in tool_msgs[0]["content"]
    assert "晴，25度" in tool_msgs[0]["content"]
    assert "请用" not in tool_msgs[0]["content"]

    reanchor_msgs = [
        m for m in final_messages
        if m.get("role") == "system" and "工具用完了" in m.get("content", "")
    ]
    assert len(reanchor_msgs) == 1


# ── 3. 步数耗尽 → 强制收尾（不带 tools），含 voice_reanchor ─────────────────

@pytest.mark.asyncio
async def test_steps_exhausted_forces_closing(monkeypatch):
    _patch_tool_loop_config(monkeypatch, max_steps=2)
    _patch_tools_schema(monkeypatch, ["web_search"])

    def _turn(i):
        return ChatTurn(
            content="",
            tool_calls=[{"id": f"call_{i}", "name": "web_search", "arguments": {"query": "x"}}],
            assistant_message={"role": "assistant", "content": None},
        )

    chat_turn_calls = _script_chat_turn(monkeypatch, [_turn(1), _turn(2)])
    execute_calls = _script_execute(monkeypatch, [("r1", None), ("r2", None)])
    final_calls = _patch_final_chat(monkeypatch, text="收尾回复")

    pipeline = _make_pipeline()
    result = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "查两次"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    assert result == "收尾回复"
    assert len(chat_turn_calls) == 2
    assert len(execute_calls) == 2
    final_messages = final_calls[-1]
    assert any(
        m.get("role") == "system" and "工具用完了" in m.get("content", "")
        for m in final_messages
    )


# ── 5. exclude_tools：排除工具不出现在 schema 里 ────────────────────────────

@pytest.mark.asyncio
async def test_exclude_tools_filtered_from_schema(monkeypatch):
    _patch_tool_loop_config(monkeypatch, exclude_tools=["toy_vibrate"])
    _patch_tools_schema(monkeypatch, ["web_search", "toy_vibrate"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(content="ok", tool_calls=[], assistant_message={"role": "assistant", "content": "ok"}),
    ])
    _script_execute(monkeypatch, [])

    pipeline = _make_pipeline()
    await pipeline.run_agentic_loop(
        [{"role": "user", "content": "hi"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    tool_names = {t["function"]["name"] for t in chat_turn_calls[0]["tools"]}
    assert "web_search" in tool_names
    assert "toy_vibrate" not in tool_names


# ── 6. 单步工具异常 → 不中断循环，失败文案回填 ──────────────────────────────

@pytest.mark.asyncio
async def test_single_tool_exception_does_not_abort_loop(monkeypatch):
    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["web_search"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(
            content="",
            tool_calls=[{"id": "call_1", "name": "web_search", "arguments": {"query": "x"}}],
            assistant_message={"role": "assistant", "content": None},
        ),
        ChatTurn(content="虽然出错了但还是回答你", tool_calls=[],
                 assistant_message={"role": "assistant", "content": "虽然出错了但还是回答你"}),
    ])

    async def _raise(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("core.tool_dispatcher.execute", _raise)
    final_calls = _patch_final_chat(monkeypatch, text="收尾")

    pipeline = _make_pipeline()
    result = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "查一下"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    assert result == "收尾"
    assert len(chat_turn_calls) == 2
    tool_msg = next(m for m in final_calls[-1] if m.get("tool_call_id") == "call_1")
    assert tool_msg["content"] == "（工具无结果或执行失败）"


# ── 7. ask_confirm → 立即强制收尾，询问文字在回填里 ─────────────────────────

@pytest.mark.asyncio
async def test_ask_confirm_forces_immediate_stop(monkeypatch):
    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["device_shutdown"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(
            content="",
            tool_calls=[{"id": "call_1", "name": "device_shutdown", "arguments": {}}],
            assistant_message={"role": "assistant", "content": None},
        ),
        ChatTurn(content="不应该走到这一步", tool_calls=[],
                 assistant_message={"role": "assistant", "content": "x"}),
    ])
    ask_text = "你确定要关机（60秒后）吗？回复\"确认\"来执行，回复其他内容取消。"
    execute_calls = _script_execute(monkeypatch, [(None, ask_text)])

    pipeline = _make_pipeline()
    result = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "关机"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    assert result == ask_text
    assert len(chat_turn_calls) == 1  # 立即停止，不会有第二次决策
    assert len(execute_calls) == 1


# ── 8. tool_loop_active 总闸：开关/owner/preset 三项门控 ────────────────────

def test_tool_loop_active_gating(monkeypatch):
    from core.tool_dispatcher import tool_loop_active

    def _cfg(enabled=True, owner="u1"):
        return {"tool_loop": {"enabled": enabled}, "scheduler": {"owner_id": owner}}

    class _FakeMcFc:
        tool_call_mode = "function_calling"

    class _FakeMcXml:
        tool_call_mode = "xml_fallback"

    # tool_dispatcher.py 顶层 `from core.config_loader import get_config`——
    # 要打到 tool_loop_active() 实际用的那个名字，得 patch 模块内绑定的引用，
    # 而不是 core.config_loader.get_config 本体。
    monkeypatch.setattr("core.model_registry.get_model_client", lambda cat: _FakeMcFc())

    monkeypatch.setattr("core.tool_dispatcher.get_config", lambda: _cfg(enabled=False))
    assert tool_loop_active("u1") is False  # 总开关关

    monkeypatch.setattr("core.tool_dispatcher.get_config", lambda: _cfg(enabled=True, owner="u2"))
    assert tool_loop_active("u1") is False  # 非 owner

    monkeypatch.setattr("core.tool_dispatcher.get_config", lambda: _cfg(enabled=True, owner="u1"))
    assert tool_loop_active("u1") is True  # 三项全满足

    monkeypatch.setattr("core.model_registry.get_model_client", lambda cat: _FakeMcXml())
    assert tool_loop_active("u1") is False  # 小模型 xml_fallback 路径不激活


def test_tool_loop_active_character_override(monkeypatch):
    """109-a：角色卡 on/off 覆盖全局；缺失/非法值仍回落全局。"""
    from dataclasses import dataclass
    from core import pipeline_registry
    from core.tool_dispatcher import tool_loop_active

    @dataclass
    class _Char:
        presence_ext: dict

    @dataclass
    class _Pipeline:
        character: object

    class _FakeMcFc:
        tool_call_mode = "function_calling"

    monkeypatch.setattr(
        "core.tool_dispatcher.get_config",
        lambda: {"tool_loop": {"enabled": False}, "scheduler": {"owner_id": "u1"}},
    )
    monkeypatch.setattr("core.model_registry.get_model_client", lambda cat: _FakeMcFc())

    pipeline_registry.register(_Pipeline(_Char({"tool_loop": "on"})))
    assert tool_loop_active("u1") is True

    pipeline_registry.register(_Pipeline(_Char({"tool_loop": "off"})))
    assert tool_loop_active("u1") is False

    monkeypatch.setattr(
        "core.tool_dispatcher.get_config",
        lambda: {"tool_loop": {"enabled": True}, "scheduler": {"owner_id": "u1"}},
    )
    pipeline_registry.register(_Pipeline(_Char({})))
    assert tool_loop_active("u1") is True
    pipeline_registry.register(_Pipeline(_Char({"tool_loop": "invalid"})))
    assert tool_loop_active("u1") is True
    pipeline_registry.register(None)


# ── 9. stream：工具步非流式，最终答案经 chat_stream 出口逐 token yield ─────

@pytest.mark.asyncio
async def test_stream_tool_step_nonstream_final_streamed(monkeypatch):
    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["web_search"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(
            content="",
            tool_calls=[{"id": "call_1", "name": "web_search", "arguments": {"query": "x"}}],
            assistant_message={"role": "assistant", "content": None},
        ),
        ChatTurn(content="终止", tool_calls=[], assistant_message={"role": "assistant", "content": "终止"}),
    ])
    _script_execute(monkeypatch, [("搜索结果", None)])

    stream_calls: list[list[dict]] = []

    async def _fake_chat_stream(messages, max_tokens_override=None, call_category="chat", char_id=None,
                                 is_proactive=False):
        stream_calls.append([dict(m) for m in messages])
        for piece in ["你", "好", "呀"]:
            yield piece

    monkeypatch.setattr("core.llm_client.chat_stream", _fake_chat_stream)

    pipeline = _make_pipeline()
    gen = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "查一下"}], uid="u1", char_id=TEST_CHAR_ID,
        session_state=object(), stream=True,
    )
    chunks = [piece async for piece in gen]

    assert "".join(chunks) == "你好呀"
    assert len(chat_turn_calls) == 2  # 工具决策步全程非流式
    assert len(stream_calls) == 1     # 最终答案走 chat_stream 出口


# ── 10b. stream：空自然终止同样降级到无 tools 的流式出口 ───────────────────

@pytest.mark.asyncio
async def test_stream_empty_natural_termination_falls_back_to_tool_free_stream(monkeypatch):
    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["web_search"])
    _script_chat_turn(monkeypatch, [
        ChatTurn(content="", tool_calls=[], assistant_message={"role": "assistant", "content": ""}),
    ])
    _script_execute(monkeypatch, [])

    async def _fake_chat_stream(messages, max_tokens_override=None, call_category="chat", char_id=None,
                                 is_proactive=False):
        for piece in ["降级", "成功"]:
            yield piece

    monkeypatch.setattr("core.llm_client.chat_stream", _fake_chat_stream)

    pipeline = _make_pipeline()
    gen = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "你好"}], uid="u1", char_id=TEST_CHAR_ID,
        session_state=object(), stream=True,
    )

    assert "".join([piece async for piece in gen]) == "降级成功"


# ── 11. action_trace：loop 每步 execute 落痕（origin=assistant_loop）───────

@pytest.mark.asyncio
async def test_action_trace_recorded_per_step(monkeypatch, sandbox):
    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["loop_probe_tool"])

    from core import tool_dispatcher as td

    async def _fake_tool():
        return "工具结果ok"

    monkeypatch.setitem(td._TOOL_REGISTRY, "loop_probe_tool", {
        "func": _fake_tool,
        "description": "测试用工具",
        "dangerous": False,
        "category": "info",
        "parameters": {"type": "object", "properties": {}, "required": []},
    })

    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(
            content="",
            tool_calls=[{"id": "call_1", "name": "loop_probe_tool", "arguments": {}}],
            assistant_message={"role": "assistant", "content": None},
        ),
        ChatTurn(content="做完了", tool_calls=[], assistant_message={"role": "assistant", "content": "做完了"}),
    ])
    _patch_final_chat(monkeypatch, text="搞定啦")

    from core.session_state import SessionState
    pipeline = _make_pipeline()
    result = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "帮我做点事"}], uid="u1", char_id=TEST_CHAR_ID,
        session_state=SessionState(),
    )

    assert result == "搞定啦"
    assert len(chat_turn_calls) == 2

    from core.memory import action_trace
    entries = action_trace.recent("u1", TEST_CHAR_ID)
    assert any(
        e.get("tool") == "loop_probe_tool" and e.get("origin") == "assistant_loop"
        for e in entries
    )


# ── 12. prompt_style 直通：narrative/xml 两种 style 下 role=tool 消息不被改写

def test_prompt_style_passthrough_for_tool_messages():
    from core.prompt_style import apply_prompt_style

    tool_calls = [{"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]
    messages = [
        {"role": "system", "content": "系统提示", "_layer": "1_core"},
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
        {"role": "tool", "tool_call_id": "call_1", "content": "工具结果原文"},
    ]

    for style in ("narrative", "xml"):
        out = apply_prompt_style(messages, style)
        assistant_msg = next(m for m in out if m["role"] == "assistant")
        tool_msg = next(m for m in out if m["role"] == "tool")
        assert assistant_msg["tool_calls"] == tool_calls
        assert tool_msg["content"] == "工具结果原文"
        assert tool_msg["tool_call_id"] == "call_1"


# ── 12b. nudge_hint 必须教会模型尾部花括号约定（Brief 120）──────────────────
#
# 光实现后端解析而不告诉模型这个语法存在，模型就永远不会主动用它——这条约定必须
# 出现在 loop 注入的 nudge_hint 里（_layer 11.5_tool_nudge，具备 recency 优先级，
# 能压过 build_prompt() 在 tool_result=None 时注入的"禁止声称调用了任何工具"）。

@pytest.mark.asyncio
async def test_nudge_hint_teaches_tail_brace_convention(monkeypatch):
    from core.llm_client import ChatTurn

    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["web_search"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(content="ok", tool_calls=[], assistant_message={"role": "assistant", "content": "ok"}),
    ])
    _script_execute(monkeypatch, [])

    pipeline = _make_pipeline()
    await pipeline.run_agentic_loop(
        [{"role": "user", "content": "hi"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    nudge = next(m for m in chat_turn_calls[0]["messages"] if m.get("_layer") == "11.5_tool_nudge")
    assert "{true" in nudge["content"]
    assert "{false" in nudge["content"]


# ── 12c. nudge_hint 必须明确"调用工具不等于把调用过程念出来"（Brief 122）────
#
# 用户说"去调用工具玩一下"这类话时，模型容易把"调用工具"当成可以叙述的动作
# （roleplay 风格本来就鼓励把动作写进（）），从而把工具名/参数/调用语法当台词
# 或动作描写输出，而不是走真正的结构化 tool_calls。必须显式划清。

@pytest.mark.asyncio
async def test_nudge_hint_forbids_narrating_tool_call_as_dialogue(monkeypatch):
    from core.llm_client import ChatTurn

    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["web_search"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(content="ok", tool_calls=[], assistant_message={"role": "assistant", "content": "ok"}),
    ])
    _script_execute(monkeypatch, [])

    pipeline = _make_pipeline()
    await pipeline.run_agentic_loop(
        [{"role": "user", "content": "去调用工具玩一下"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    nudge = next(m for m in chat_turn_calls[0]["messages"] if m.get("_layer") == "11.5_tool_nudge")
    assert "系统内部静默完成" in nudge["content"]
    assert "不是说给对方听" in nudge["content"]


@pytest.mark.asyncio
async def test_nudge_hint_derives_opaque_mcp_parameter_guidance_from_current_registry(monkeypatch):
    import core.tool_dispatcher as td

    _patch_tool_loop_config(monkeypatch, categories=["mcp"])
    schema = [{"type": "function", "function": {
        "name": "mcp__arcade__play",
        "description": "play",
        "parameters": {"type": "object", "properties": {
            "params": {"type": "object", "additionalProperties": True},
        }},
    }}]
    monkeypatch.setattr(td, "get_tools_schema", lambda categories=None, **kwargs: schema)
    monkeypatch.setattr(td, "_TOOL_REGISTRY", {
        "mcp__arcade__play": {"category": "mcp", "mcp_server": "arcade", "description": "play"},
        "mcp__arcade__inspect_action": {
            "category": "mcp", "mcp_server": "arcade", "mcp_read_only": True,
            "description": "Describes parameters for an action",
        },
    })
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(content="ok", tool_calls=[], assistant_message={"role": "assistant", "content": "ok"}),
    ])
    _script_execute(monkeypatch, [])

    await _make_pipeline().run_agentic_loop(
        [{"role": "user", "content": "play"}], uid="u1", char_id="c1", session_state=object(),
    )

    nudge = next(m for m in chat_turn_calls[0]["messages"] if m.get("_layer") == "11.5_tool_nudge")
    assert "mcp__arcade__inspect_action" not in str(chat_turn_calls[0]["messages"])
    assert "不要根据工具名猜测" in str(chat_turn_calls[0]["messages"])


# ── 13. Brief 120·工具循环二次调用兜底（尾部花括号方案）────────────────────
#
# 覆盖 cc-tasks/120-工具循环二次调用兜底-尾部花括号方案.md §4 的验收要求：
#   1) {true: ...} 触发额外一次 _execute()，循环不提前 return；
#   2) 宽容解析（parse_tail_brace）边界样本；
#   3) {false}/无花括号时行为与现状完全一致（回归）。

def _fake_chat_with_relay(monkeypatch, *, relay_tool_calls, final_text):
    """core.llm_client.chat 的分支 mock：call_category=="probe" 时模拟 relay 解析
    结果（Path A 探针范式的 __TOOL_CALL__: 哨兵串），否则模拟 voice_reanchor 之后
    不带 tools 的收尾生成（与 run_llm 内部调用一致）。
    """
    calls: list[dict] = []

    async def _fake(messages, tools=None, max_tokens_override=None, use_vision=False,
                     call_category="chat", char_id=None, is_proactive=False):
        calls.append({
            "call_category": call_category,
            "messages": [dict(m) for m in messages],
            "tools": tools,
        })
        if call_category == "probe":
            return "__TOOL_CALL__:" + json.dumps(relay_tool_calls, ensure_ascii=False)
        return final_text

    monkeypatch.setattr("core.llm_client.chat", _fake)
    return calls


@pytest.mark.asyncio
async def test_tail_brace_relay_triggers_second_tool_call(monkeypatch):
    """模型第二步不带 tool_calls，只在自然语言末尾标注 {true: ...}：应触发一次
    额外的 _execute()，循环继续进入第三步，而不是把这段文字直接丢弃收尾。"""
    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["web_search", "fish_cast"])
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(
            content="",
            tool_calls=[{"id": "call_1", "name": "web_search", "arguments": {"query": "钓鱼"}}],
            assistant_message={"role": "assistant", "content": None},
        ),
        ChatTurn(
            content="我抛竿等一会～{true: 我需要钓鱼动作，等待10秒}",
            tool_calls=[],
            assistant_message={"role": "assistant", "content": "我抛竿等一会～{true: 我需要钓鱼动作，等待10秒}"},
        ),
        ChatTurn(content="钓到了一条大鱼！", tool_calls=[],
                 assistant_message={"role": "assistant", "content": "钓到了一条大鱼！"}),
    ])
    execute_calls = _script_execute(monkeypatch, [("晴，25度", None), ("鱼上钩了", None)])
    relay_calls = _fake_chat_with_relay(
        monkeypatch,
        relay_tool_calls=[{"name": "fish_cast", "arguments": {"wait": 10}}],
        final_text="今天挺晴朗的～",
    )

    pipeline = _make_pipeline()
    result = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "陪我钓鱼"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    assert result == "今天挺晴朗的～"
    # 3 步 chat_turn：正常工具 → 尾部花括号 → 最终自然收尾
    assert len(chat_turn_calls) == 3
    # execute 只脚本化了 2 次结果：第一步 web_search + relay 解析出的 fish_cast
    assert len(execute_calls) == 2
    assert execute_calls[0]["tool_name"] == "web_search"
    assert execute_calls[1]["tool_name"] == "fish_cast"
    assert execute_calls[1]["tool_args"] == {"wait": 10}
    # relay 分支用独立 origin，和原生 tool_calls 分支区分开，方便日后从 action_trace/
    # error.log 反推故障来自哪条路径（Brief 120 事后排查时吃过分不清的亏）。
    assert execute_calls[1]["origin"] == "assistant_loop_relay"

    probe_calls = [c for c in relay_calls if c["call_category"] == "probe"]
    assert len(probe_calls) == 1
    # 送去解析的是剥离花括号后的干净意图文本，不含标记语法本身
    assert probe_calls[0]["messages"][-1]["content"] == "我需要钓鱼动作，等待10秒"

    # loop_msgs 里应能看到 relay 合成的 assistant tool_calls + tool 结果回填
    final_messages = [c for c in relay_calls if c["call_category"] != "probe"][-1]["messages"]
    relay_assistant = next(
        m for m in final_messages
        if m.get("role") == "assistant" and m.get("tool_calls")
        and m["tool_calls"][0]["function"]["name"] == "fish_cast"
    )
    assert relay_assistant["content"] == "我抛竿等一会～"
    tool_result_msg = next(
        m for m in final_messages
        if m.get("role") == "tool" and m.get("tool_call_id") == relay_assistant["tool_calls"][0]["id"]
    )
    assert "<<<TOOL_DATA_START>>>" in tool_result_msg["content"]
    assert "<<<TOOL_DATA_END>>>" in tool_result_msg["content"]
    assert "鱼上钩了" in tool_result_msg["content"]


@pytest.mark.asyncio
async def test_tail_brace_relay_unresolved_falls_back_to_natural_text(monkeypatch):
    """{true: ...} 命中但 relay 解析不出任何工具：不应静默吞掉文字或抛异常，
    退回自然结束，展示文本已剥离花括号标记。"""
    _patch_tool_loop_config(monkeypatch)
    _patch_tools_schema(monkeypatch, ["web_search"])
    _script_chat_turn(monkeypatch, [
        ChatTurn(
            content="这个我不太确定{true: 帮我处理一个不存在的功能}",
            tool_calls=[],
            assistant_message={"role": "assistant", "content": "这个我不太确定{true: 帮我处理一个不存在的功能}"},
        ),
    ])
    _script_execute(monkeypatch, [])
    _fake_chat_with_relay(monkeypatch, relay_tool_calls=[], final_text="不会被用到")

    pipeline = _make_pipeline()
    result = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "帮我个忙"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    assert result == "这个我不太确定"
    assert "{true" not in result and "}" not in result


@pytest.mark.parametrize("tag, expected_display, expected_intent", [
    # 正常闭合 + 半角冒号
    ("我去看看{true: 需要打开浏览器}", "我去看看", "需要打开浏览器"),
    # 全角冒号
    ("我去看看{true：需要打开浏览器}", "我去看看", "需要打开浏览器"),
    # 缺失闭合括号：截到字符串结尾
    ("我去看看{true: 需要打开浏览器", "我去看看", "需要打开浏览器"),
    # 大小写混用
    ("我去看看{True: 需要打开浏览器}", "我去看看", "需要打开浏览器"),
    # 前面还有正常聊天内容，多行
    ("今天天气不错\n我们出去走走吧{true:查一下天气}", "今天天气不错\n我们出去走走吧", "查一下天气"),
])
def test_parse_tail_brace_lenient_true_cases(tag, expected_display, expected_intent):
    from core.tool_dispatcher import parse_tail_brace

    display, intent = parse_tail_brace(tag)
    assert display == expected_display
    assert intent == expected_intent
    assert "{" not in display and "}" not in display


@pytest.mark.parametrize("tag, expected_display", [
    ("我随口说说{false}", "我随口说说"),
    ("我随口说说{false", "我随口说说"),
])
def test_parse_tail_brace_false_tag_stripped_no_intent(tag, expected_display):
    from core.tool_dispatcher import parse_tail_brace

    display, intent = parse_tail_brace(tag)
    assert display == expected_display
    assert intent is None


def test_parse_tail_brace_no_tag_passthrough():
    """回归：没有花括号标记时原样返回，不影响现有默认路径。"""
    from core.tool_dispatcher import parse_tail_brace

    text = "完全正常的一段回复，没有任何标记。"
    display, intent = parse_tail_brace(text)
    assert display == text
    assert intent is None


@pytest.mark.asyncio
async def test_relay_prompt_covers_current_loop_categories_including_mcp(monkeypatch):
    """relay 解析用的类目应覆盖本轮 run_agentic_loop 实际暴露的 categories（含 mcp），
    而不是探针默认的 info/desktop 两类——否则钓鱼/海龟汤这类 mcp 工具还是够不着。"""
    from core import tool_dispatcher as td

    _patch_tool_loop_config(monkeypatch, categories=["info", "desktop", "memory", "mcp"])
    _patch_tools_schema(monkeypatch, ["web_search", "mcp__turtle_soup__ask"])
    monkeypatch.setitem(td._TOOL_REGISTRY, "mcp__turtle_soup__ask", {
        "func": lambda: None,
        "description": "海龟汤提问",
        "dangerous": False,
        "category": "mcp",
        "parameters": {"type": "object", "properties": {}},
    })

    _script_chat_turn(monkeypatch, [
        ChatTurn(
            content="让我猜猜看{true: 我要问海龟汤线索}",
            tool_calls=[],
            assistant_message={"role": "assistant", "content": "让我猜猜看{true: 我要问海龟汤线索}"},
        ),
        ChatTurn(content="猜对了！", tool_calls=[], assistant_message={"role": "assistant", "content": "猜对了！"}),
    ])
    execute_calls = _script_execute(monkeypatch, [("线索：夜晚", None)])
    relay_calls = _fake_chat_with_relay(
        monkeypatch,
        relay_tool_calls=[{"name": "mcp__turtle_soup__ask", "arguments": {}}],
        final_text="猜对了！",
    )

    pipeline = _make_pipeline()
    await pipeline.run_agentic_loop(
        [{"role": "user", "content": "陪我玩海龟汤"}], uid="u1", char_id=TEST_CHAR_ID, session_state=object(),
    )

    assert execute_calls[0]["tool_name"] == "mcp__turtle_soup__ask"
    probe_system = next(c for c in relay_calls if c["call_category"] == "probe")["messages"][0]["content"]
    assert "mcp__turtle_soup__ask" in probe_system


@pytest.mark.asyncio
async def test_model_tool_preset_narrows_schema_for_its_chat_preset(monkeypatch):
    _patch_tool_loop_config(monkeypatch, exclude_tools=[], tool_presets=[{
        "name": "claude-minimal", "tools": ["web_search"],
    }])
    _patch_tools_schema(monkeypatch, ["get_time", "web_search"])
    monkeypatch.setattr("core.model_registry._get_preset_config", lambda: {
        "presets": {"claude-pig": {"tool_preset": "claude-minimal"}},
    })
    monkeypatch.setattr("core.model_registry._resolve_preset_name", lambda *args, **kwargs: "claude-pig")
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(content="done", tool_calls=[], assistant_message={"role": "assistant", "content": "done"}),
    ])
    _script_execute(monkeypatch, [])

    result = await _make_pipeline().run_agentic_loop(
        [{"role": "user", "content": "查一下"}], uid="u1", char_id="c1", session_state=None,
    )

    assert result == "done"
    assert [tool["function"]["name"] for tool in chat_turn_calls[0]["tools"]] == ["web_search"]


@pytest.mark.asyncio
async def test_model_tool_preset_does_not_recatalogue_dynamic_mcp_tools(monkeypatch):
    from core import tool_dispatcher as td

    _patch_tool_loop_config(monkeypatch, exclude_tools=[], tool_presets=[{
        "name": "builtin-only", "tools": ["web_search"],
    }])
    _patch_tools_schema(monkeypatch, ["web_search", "dynamic_mcp_tool"])
    monkeypatch.setitem(td._TOOL_REGISTRY, "dynamic_mcp_tool", {"category": "mcp"})
    monkeypatch.setattr("core.model_registry._get_preset_config", lambda: {
        "presets": {"claude-pig": {"tool_preset": "builtin-only"}},
    })
    monkeypatch.setattr("core.model_registry._resolve_preset_name", lambda *args, **kwargs: "claude-pig")
    chat_turn_calls = _script_chat_turn(monkeypatch, [
        ChatTurn(content="done", tool_calls=[], assistant_message={"role": "assistant", "content": "done"}),
    ])
    _script_execute(monkeypatch, [])

    await _make_pipeline().run_agentic_loop(
        [{"role": "user", "content": "查一下"}], uid="u1", char_id="c1", session_state=None,
    )

    assert [tool["function"]["name"] for tool in chat_turn_calls[0]["tools"]] == ["web_search", "dynamic_mcp_tool"]
