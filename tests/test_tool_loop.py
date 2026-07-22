"""
tests/test_tool_loop.py — Brief 28 · tool loop 多步工具执行器

覆盖 cc-tasks/28-tool-loop多步工具执行器.md §4 的 12 项测试。
LLM 全 mock：core.llm_client.chat_turn / chat / chat_stream 按脚本吐结果，
不发真实网络请求。tool_dispatcher.execute 多数场景也 mock（脚本化 result/ask_confirm），
只有「action_trace 落痕」一项使用真实 execute() 走真实落盘（sandbox 隔离）。
"""

from __future__ import annotations

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
    return cfg


def _patch_tools_schema(monkeypatch, names):
    schema = [
        {"type": "function", "function": {"name": n, "description": "", "parameters": {"type": "object", "properties": {}}}}
        for n in names
    ]
    monkeypatch.setattr("core.tool_dispatcher.get_tools_schema", lambda categories=None: schema)


def _script_chat_turn(monkeypatch, turns: list[ChatTurn]):
    calls: list[dict] = []
    it = iter(turns)

    async def _fake(messages, tools, **kw):
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
        [{"role": "user", "content": "你好"}], uid="u1", char_id="yexuan", session_state=object(),
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
        [{"role": "user", "content": "你好"}], uid="u1", char_id="yexuan", session_state=object(),
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
        [{"role": "user", "content": "今天天气"}], uid="u1", char_id="yexuan", session_state=object(),
    )

    assert result == "今天挺晴朗的～"
    assert len(chat_turn_calls) == 2
    assert len(execute_calls) == 1
    assert execute_calls[0]["tool_name"] == "web_search"
    assert execute_calls[0]["origin"] == "assistant_loop"

    final_messages = final_calls[-1]
    tool_msgs = [m for m in final_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert tool_msgs[0]["content"] == "晴，25度"

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
        [{"role": "user", "content": "查两次"}], uid="u1", char_id="yexuan", session_state=object(),
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
        [{"role": "user", "content": "hi"}], uid="u1", char_id="yexuan", session_state=object(),
    )

    tool_names = {t["function"]["name"] for t in chat_turn_calls[0]["tools"]}
    assert tool_names == {"web_search"}


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
        [{"role": "user", "content": "查一下"}], uid="u1", char_id="yexuan", session_state=object(),
    )

    assert result == "收尾"
    assert len(chat_turn_calls) == 2
    tool_msg = next(m for m in final_calls[-1] if m.get("role") == "tool")
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
        [{"role": "user", "content": "关机"}], uid="u1", char_id="yexuan", session_state=object(),
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


# ── 9. 路径 B 跳过：loop_executed=True 时 Path B 直接 return ────────────────

@pytest.mark.asyncio
async def test_loop_executed_skips_path_b(monkeypatch):
    # Brief 35: intent_reflex 默认关；本测试验证的是 loop_executed 守卫 (d)
    # 本身的短路顺序，需强制 enabled=true 才能越过入口的 intent_reflex 闸真正触及守卫 (d)。
    from core import config_loader
    monkeypatch.setattr(config_loader, "get_config", lambda: {"intent_reflex": {"enabled": True}})

    async def _fail_if_called(*a, **kw):
        raise AssertionError("loop_executed=True 时不应再调用 LLM 解析 Path B 意图")

    monkeypatch.setattr("core.llm_client.chat", _fail_if_called)

    pipeline = _make_pipeline()
    # 不抛出 AssertionError 即说明守卫 (d) 在最前面短路了，未触及后续任何 LLM 调用
    await pipeline._parse_and_execute_intent(
        "我现在去帮你把窗口关掉",
        trigger_name="",
        user_content="帮我关一下",
        user_id="u1",
        char_id="yexuan",
        loop_executed=True,
    )


# ── 10. stream：工具步非流式，最终答案经 chat_stream 出口逐 token yield ─────

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
        [{"role": "user", "content": "查一下"}], uid="u1", char_id="yexuan",
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
        [{"role": "user", "content": "你好"}], uid="u1", char_id="yexuan",
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
        [{"role": "user", "content": "帮我做点事"}], uid="u1", char_id="yexuan",
        session_state=SessionState(),
    )

    assert result == "搞定啦"
    assert len(chat_turn_calls) == 2

    from core.memory import action_trace
    entries = action_trace.recent("u1", "yexuan")
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
