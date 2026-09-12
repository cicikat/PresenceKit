"""
tests/test_llm_client_chat_turn.py — chat_turn()/chat_stream() 对"泄漏的模型内部
工具调用 token"的防御

背景（cc-tasks/122）：DeepSeek 等模型的原生工具调用格式用带内部 special token
（如 `<｜tool▁calls▁begin｜>`）的文本包裹；正常情况下网关应该把这段解析成结构化
的 `tool_calls` 字段，`content` 里不该出现这些 token。观察到的真实故障：网关这一步
没解析干净，`finish_reason` 没标 `tool_calls`，`message.content` 却混进了这类内部
token，原样展示给用户就是一堆乱码指令。

`chat_turn()` 探测到这种情况会丢弃 content、按空内容处理，复用 run_agentic_loop
里已有的"空回复→不带 tools 强制重新生成"兜底。第一版修复只覆盖了这一个出口，
后来发现同一类泄漏也会出现在无 tools 的最终生成里，且那条路径是流式
（chat_stream()）逐 chunk 到达，不能直接照搬"整段判完再决定"的做法，于是补了一个
尾部滚动缓冲区在流里做同样的探测。
"""

from __future__ import annotations

import types

import pytest

from core.model_registry import ModelClient


def _fake_message(content: str, *, tool_calls=None, model_dump_extra: dict | None = None):
    dump = {"role": "assistant"}
    if content:
        dump["content"] = content
    if model_dump_extra:
        dump.update(model_dump_extra)

    return types.SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        model_dump=lambda exclude_none=True: dict(dump),
    )


def _make_fake_model_client(message, *, finish_reason="stop"):
    async def fake_create(**kwargs):
        choice = types.SimpleNamespace(message=message, finish_reason=finish_reason)
        return types.SimpleNamespace(choices=[choice])

    completions = types.SimpleNamespace(create=fake_create)
    chat_obj = types.SimpleNamespace(completions=completions)
    fake_client = types.SimpleNamespace(chat=chat_obj)

    return ModelClient(
        name="test",
        provider_kind="deepseek",
        model="test-model",
        tool_call_mode="function_calling",
        prompt_style="narrative",
        params={"temperature": 0.0, "max_tokens": 10},
        client=fake_client,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("category,expected_timeout", [("probe", 15.0), ("chat", 90.0), ("intent", 10.0)])
async def test_chat_request_category_timeout(monkeypatch, category, expected_timeout):
    from core import llm_client

    fake_mc = _make_fake_model_client(_fake_message("ok"))
    create = fake_mc.client.chat.completions.create
    requests = []

    async def capture_create(**kwargs):
        requests.append(kwargs)
        return await create(**kwargs)

    fake_mc.client.chat.completions.create = capture_create
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: fake_mc)
    await llm_client.chat([{"role": "user", "content": "hi"}], call_category=category)
    assert requests[0]["timeout"] == expected_timeout


@pytest.mark.asyncio
async def test_leaked_tool_call_markup_discarded_as_empty(monkeypatch):
    from core import llm_client

    leaked = "<｜｜DSML｜｜tool_calls>疑似残留的内部调用指令"
    message = _fake_message(leaked, tool_calls=None)
    fake_mc = _make_fake_model_client(message, finish_reason="stop")
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: fake_mc)

    turn = await llm_client.chat_turn(
        [{"role": "user", "content": "去调用工具玩一下"}], tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
    )

    assert turn.content == ""
    assert turn.tool_calls == []
    assert turn.assistant_message.get("content") is None


@pytest.mark.asyncio
async def test_clean_natural_content_passes_through_unchanged(monkeypatch):
    """回归：不含泄漏特征字符的正常回复不受影响。"""
    from core import llm_client

    message = _fake_message("今天天气不错，我们出去走走吧", tool_calls=None)
    fake_mc = _make_fake_model_client(message, finish_reason="stop")
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: fake_mc)

    turn = await llm_client.chat_turn(
        [{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
    )

    assert turn.content == "今天天气不错，我们出去走走吧"
    assert turn.tool_calls == []


@pytest.mark.asyncio
async def test_real_tool_calls_bypass_leak_check_even_with_suspicious_content(monkeypatch):
    """回归：finish_reason 真是 tool_calls 时，即使 content 恰好也带这些字符
    （现实中不会发生，但确认判断逻辑只在 not tool_calls 时才生效），照常解析。"""
    from core import llm_client

    tc = types.SimpleNamespace(
        id="call_1",
        function=types.SimpleNamespace(name="web_search", arguments='{"query": "x"}'),
    )
    message = _fake_message("｜无关噪音｜", tool_calls=[tc])
    fake_mc = _make_fake_model_client(message, finish_reason="tool_calls")
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: fake_mc)

    turn = await llm_client.chat_turn(
        [{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "web_search", "parameters": {}}}],
    )

    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0]["name"] == "web_search"
    assert turn.content == "｜无关噪音｜"


@pytest.mark.asyncio
async def test_chat_turn_records_the_actual_tool_request_when_opt_in_debug_is_enabled(monkeypatch):
    from core import llm_client

    message = _fake_message("ok", tool_calls=None)
    fake_mc = _make_fake_model_client(message, finish_reason="stop")
    captured = {}
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: fake_mc)
    monkeypatch.setattr("core.llm_debug_requests.append", lambda **kwargs: captured.update(kwargs))

    await llm_client.chat_turn(
        [{"role": "user", "content": "inspect the action"}],
        tools=[{"type": "function", "function": {"name": "mcp__arcade__play", "parameters": {}}}],
    )

    assert captured["messages"][-1]["content"] == "inspect the action"
    assert captured["tools"][0]["function"]["name"] == "mcp__arcade__play"
    assert captured["request_kwargs"]["tool_choice"] == "auto"


class TestLeakDetectorHelper:
    def test_two_or_more_occurrences_flagged(self):
        from core.llm_client import _looks_like_leaked_tool_call_markup as detect

        assert detect("<｜tool▁calls▁begin｜>") is True
        assert detect("<｜｜DSML｜｜tool_calls>") is True

    def test_single_occurrence_not_flagged(self):
        from core.llm_client import _looks_like_leaked_tool_call_markup as detect

        assert detect("只有一个｜竖线的正常句子") is False

    def test_empty_or_none_not_flagged(self):
        from core.llm_client import _looks_like_leaked_tool_call_markup as detect

        assert detect("") is False
        assert detect(None) is False


# ── chat_stream()：同一类泄漏的流式出口 ──────────────────────────────────────

def _make_stream_mc(pieces: list[str]):
    async def fake_stream_gen():
        for p in pieces:
            delta = types.SimpleNamespace(content=p)
            choice = types.SimpleNamespace(delta=delta)
            yield types.SimpleNamespace(choices=[choice])

    async def fake_create(**kwargs):
        return fake_stream_gen()

    completions = types.SimpleNamespace(create=fake_create)
    chat_obj = types.SimpleNamespace(completions=completions)
    fake_client = types.SimpleNamespace(chat=chat_obj)

    return ModelClient(
        name="test", provider_kind="deepseek", model="m", tool_call_mode="function_calling",
        prompt_style="narrative", params={}, client=fake_client,
    )


def _disable_thinking(monkeypatch):
    from core import thinking
    monkeypatch.setattr(thinking, "get_config", lambda: {"thinking": {"enabled": False}})


@pytest.mark.asyncio
async def test_chat_stream_drops_leaked_markup_split_across_chunks(monkeypatch):
    """真实场景：泄漏的 special token 逐 chunk 到达（每个 chunk 只有几个字符），
    不会像 chat_turn() 那样一次性拿到完整 content——必须靠滚动缓冲才能拼出
    "｜" 出现两次以上"这个判断，而不是逐 chunk 独立判断（否则永远看不出来）。"""
    from core import llm_client

    _disable_thinking(monkeypatch)
    pieces = ["我抛竿等一会～", "<", "｜", "｜", "DSML", "｜", "｜", "invoke", ">", "残留内容"]
    mc = _make_stream_mc(pieces)
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    out = []
    async for piece in llm_client.chat_stream([{"role": "user", "content": "hi"}]):
        out.append(piece)

    result = "".join(out)
    assert "｜" not in result
    assert "残留内容" not in result
    assert result.startswith("我抛竿等一会")


@pytest.mark.asyncio
async def test_chat_stream_clean_output_unaffected(monkeypatch):
    """回归：不含泄漏特征的正常流式回复，最终拼接结果与之前完全一致
    （逐 chunk 的边界可能因滚动缓冲而变化，但拼接后的内容不能变）。"""
    from core import llm_client

    _disable_thinking(monkeypatch)
    mc = _make_stream_mc(["今天", "天气", "不错", "，", "我们", "出去", "走走吧"])
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    out = []
    async for piece in llm_client.chat_stream([{"role": "user", "content": "hi"}]):
        out.append(piece)

    assert "".join(out) == "今天天气不错，我们出去走走吧"


@pytest.mark.asyncio
async def test_chat_stream_leak_after_think_tag_still_caught(monkeypatch):
    """回归：<think> 缓冲结束、进入正常输出之后才出现的泄漏，也要被滚动缓冲挡住
    （确认两套防线——think 缓冲与泄漏扫描——串联生效，不是互斥的）。"""
    from core import llm_client

    _disable_thinking(monkeypatch)
    pieces = ["<think>", "盘算中", "</think>", "你好呀", "<｜｜DSML｜｜invoke>", "脏内容"]
    mc = _make_stream_mc(pieces)
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    out = []
    async for piece in llm_client.chat_stream([{"role": "user", "content": "hi"}]):
        out.append(piece)

    result = "".join(out)
    assert result.startswith("你好呀")
    assert "｜" not in result
    assert "脏内容" not in result
