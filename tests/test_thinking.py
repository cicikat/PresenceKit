"""
tests/test_thinking.py — Brief 32 · 内部思考链

覆盖 cc-tasks/32-内部思考链.md §6 的测试项：
  1. 开关关 → 主调用 messages 与现状逐字节一致（回归）。
  2. monologue：注入位置正确、带 _layer、独白失败时静默跳过、≤max_tokens 传参正确。
  3. native 非流式：内联 <think> 被剥（含跨行/变体标签）。
  4. native 流式：思考只留独立 archive，含未闭合及跨 chunk 标签。
  5. extra_body：reasoning_extra_body 出现在请求 kwargs 且不经白名单过滤；
     只在 call_category=="chat" 且解析到 native 路线时生效。
  6. auto 模式判定：reasoning_native true/false 分别走 native/monologue。
  7. scrubber 兜底：带 think 标签的文本过 scrubber → 干净。
  8. history 铁律：注入的独白消息不会出现在 chat_turn 返回给 loop 的 assistant_message /
     content 里（那才是可能流向 history 的东西）。
"""
from __future__ import annotations

import types

import pytest

from core import thinking
from core.llm_client import ChatTurn
from core.model_registry import ModelClient


# ── 公共 helper ──────────────────────────────────────────────────────────────

def _make_fake_mc(
    *,
    content: str = "回复",
    reasoning_native: bool = False,
    reasoning_extra_body: dict | None = None,
    captured: list | None = None,
) -> ModelClient:
    """Fake ModelClient whose .client.chat.completions.create 记录 kwargs 并回放固定内容。"""

    async def fake_create(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        msg = types.SimpleNamespace(content=content, tool_calls=None)
        choice = types.SimpleNamespace(message=msg, finish_reason="stop")
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
        params={"temperature": 1.0, "max_tokens": 100},
        client=fake_client,
        reasoning_native=reasoning_native,
        reasoning_extra_body=reasoning_extra_body or {},
    )


def _disable_thinking(monkeypatch):
    monkeypatch.setattr(thinking, "get_config", lambda: {"thinking": {"enabled": False}})


def _enable_thinking(monkeypatch, **overrides):
    cfg = {"enabled": True, "mode": "auto", "monologue_max_tokens": 200, "apply_to_proactive": False}
    cfg.update(overrides)
    monkeypatch.setattr(thinking, "get_config", lambda: {"thinking": cfg})


# ===========================================================================
# 1. 回归：开关关 → chat() 请求 messages / kwargs 与现状一致
# ===========================================================================

@pytest.mark.asyncio
async def test_disabled_leaves_messages_and_kwargs_unchanged(monkeypatch):
    from core import llm_client

    _disable_thinking(monkeypatch)
    captured: list = []
    mc = _make_fake_mc(content="普通回复", captured=captured)
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    messages = [{"role": "system", "content": "sys", "_layer": "1_system_prompt"},
                {"role": "user", "content": "你好"}]
    result = await llm_client.chat(list(messages))

    assert result == "普通回复"
    sent = captured[0]
    assert "extra_body" not in sent
    # sanitize_messages 会剥掉 _layer，但消息数量/角色/内容应与现状一致（无独白注入）。
    assert len(sent["messages"]) == 2
    assert sent["messages"][-1]["content"] == "你好"


# ===========================================================================
# 2. monologue：注入位置 + _layer + 失败静默跳过 + max_tokens 传参
# ===========================================================================

@pytest.mark.asyncio
async def test_monologue_injected_before_last_user_message(monkeypatch):
    _enable_thinking(monkeypatch, mode="monologue", monologue_max_tokens=50)

    async def fake_monologue_call(messages, *, char_id):
        return "有点开心，想聊聊今天"

    monkeypatch.setattr(thinking, "_run_monologue_call", fake_monologue_call)

    messages = [
        {"role": "system", "content": "sys", "_layer": "1_system_prompt"},
        {"role": "user", "content": "你好"},
    ]
    out = await thinking.maybe_apply(messages, call_category="chat")

    assert len(out) == 3
    assert out[-1]["role"] == "user"  # 用户消息仍在最后
    injected = out[-2]
    assert injected["role"] == "system"
    assert injected["_layer"] == "11.7_inner_monologue"
    assert "有点开心，想聊聊今天" in injected["content"]
    # 原始 messages 不被就地修改（用完即弃，不泄漏给调用方持有的其它引用）。
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_monologue_call_failure_is_fail_open_noop(monkeypatch):
    _enable_thinking(monkeypatch, mode="monologue")

    async def fake_monologue_call(messages, *, char_id):
        return None  # 失败/超时/空结果

    monkeypatch.setattr(thinking, "_run_monologue_call", fake_monologue_call)

    messages = [{"role": "user", "content": "你好"}]
    out = await thinking.maybe_apply(messages, call_category="chat")

    assert out == messages


@pytest.mark.asyncio
async def test_monologue_skips_when_already_injected():
    """tool loop 多步复用同一份 messages 时不重复注入（防每步都独白一次）。"""
    messages = [
        {"role": "system", "content": "already", "_layer": "11.7_inner_monologue"},
        {"role": "user", "content": "你好"},
    ]
    out = await thinking.maybe_apply(messages, call_category="chat", is_proactive=False)
    assert out is messages or out == messages


@pytest.mark.asyncio
async def test_monologue_noop_for_non_chat_call_category(monkeypatch):
    _enable_thinking(monkeypatch, mode="monologue")
    called = False

    async def fake_monologue_call(messages, *, char_id):
        nonlocal called
        called = True
        return "不该被调用"

    monkeypatch.setattr(thinking, "_run_monologue_call", fake_monologue_call)

    messages = [{"role": "user", "content": "你好"}]
    out = await thinking.maybe_apply(messages, call_category="intent")

    assert out == messages
    assert called is False


@pytest.mark.asyncio
async def test_disabled_thinking_never_touches_model_registry(monkeypatch):
    """总开关关闭时 maybe_apply 不该为了判断而构建 ModelClient（默认路径应零额外开销）。"""
    _disable_thinking(monkeypatch)

    def _boom(cat, char_id=None):
        raise AssertionError("get_model_client 不应在 thinking 关闭时被调用")

    monkeypatch.setattr("core.model_registry.get_model_client", _boom)

    messages = [{"role": "user", "content": "你好"}]
    out = await thinking.maybe_apply(messages, call_category="chat")
    assert out == messages


@pytest.mark.asyncio
async def test_monologue_call_passes_max_tokens_and_category(monkeypatch):
    _enable_thinking(monkeypatch, monologue_max_tokens=42)

    from core import llm_client
    calls: list = []

    async def fake_chat(messages, tools=None, max_tokens_override=None, use_vision=False,
                         call_category="chat", *, char_id=None, is_proactive=False):
        calls.append({"call_category": call_category, "max_tokens_override": max_tokens_override})
        return "内心os"

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    result = await thinking._run_monologue_call(
        [{"role": "user", "content": "在吗"}], char_id=None,
    )
    assert result == "内心os"
    assert calls[0]["call_category"] == "monologue"
    assert calls[0]["max_tokens_override"] == 42


@pytest.mark.asyncio
async def test_apply_to_proactive_gates_monologue(monkeypatch):
    _enable_thinking(monkeypatch, mode="monologue", apply_to_proactive=False)
    called = False

    async def fake_monologue_call(messages, *, char_id):
        nonlocal called
        called = True
        return "x"

    monkeypatch.setattr(thinking, "_run_monologue_call", fake_monologue_call)

    messages = [{"role": "user", "content": "你好"}]
    out = await thinking.maybe_apply(messages, call_category="chat", is_proactive=True)

    assert out == messages
    assert called is False


# ===========================================================================
# 3 + 6. auto 模式判定 + native 路线剥离内联 <think> 标签（非流式）
# ===========================================================================

@pytest.mark.parametrize("reasoning_native,expected", [(True, "native"), (False, "monologue")])
def test_auto_mode_resolves_by_reasoning_native(monkeypatch, reasoning_native, expected):
    _enable_thinking(monkeypatch, mode="auto")
    mc = _make_fake_mc(reasoning_native=reasoning_native)
    assert thinking.resolve_effective_mode(mc) == expected


def test_explicit_mode_overrides_reasoning_native(monkeypatch):
    _enable_thinking(monkeypatch, mode="native")
    mc = _make_fake_mc(reasoning_native=False)
    assert thinking.resolve_effective_mode(mc) == "native"

    _enable_thinking(monkeypatch, mode="monologue")
    mc2 = _make_fake_mc(reasoning_native=True)
    assert thinking.resolve_effective_mode(mc2) == "monologue"


def test_disabled_resolves_to_none(monkeypatch):
    _disable_thinking(monkeypatch)
    mc = _make_fake_mc(reasoning_native=True)
    assert thinking.resolve_effective_mode(mc) is None


@pytest.mark.parametrize("tag", ["think", "thinking", "THINK", "Thinking"])
def test_strip_think_tags_variants_and_case(tag):
    text = f"<{tag}>盘算了一下\n跨行内容</{tag}>正文开始"
    assert thinking.strip_think_tags(text) == "正文开始"


def test_strip_think_tags_no_tags_passthrough():
    assert thinking.strip_think_tags("普通文本") == "普通文本"


def test_strip_think_tags_none_and_empty():
    assert thinking.strip_think_tags(None) is None
    assert thinking.strip_think_tags("") == ""


@pytest.mark.asyncio
async def test_chat_strips_inline_think_tag_from_content(monkeypatch):
    from core import llm_client

    _disable_thinking(monkeypatch)  # native 剥离与开关无关，是无条件防线
    mc = _make_fake_mc(content="<think>盘算\n跨行</think>你好呀")
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    result = await llm_client.chat([{"role": "user", "content": "hi"}])
    assert result == "你好呀"


# ===========================================================================
# 5. extra_body：只在 call_category=="chat" 且解析到 native 时注入，绕过白名单
# ===========================================================================

@pytest.mark.asyncio
async def test_native_injects_extra_body_bypassing_whitelist(monkeypatch):
    from core import llm_client

    _enable_thinking(monkeypatch, mode="native")
    captured: list = []
    mc = _make_fake_mc(
        content="回复",
        reasoning_native=True,
        reasoning_extra_body={"thinking": {"type": "enabled", "budget_tokens": 1024}},
        captured=captured,
    )
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    await llm_client.chat([{"role": "user", "content": "hi"}])

    sent = captured[0]
    assert sent["extra_body"] == {"thinking": {"type": "enabled", "budget_tokens": 1024}}
    # provider 白名单（anthropic_compat/deepseek 等）不会剔除 extra_body —— 它压根不在
    # resolve_params 处理的 params 字典里，是独立 kwarg，白名单逻辑摸不到它。
    assert "thinking" not in mc.params


@pytest.mark.asyncio
async def test_native_extra_body_not_applied_to_non_chat_category(monkeypatch):
    from core import llm_client

    _enable_thinking(monkeypatch, mode="native")
    captured: list = []
    mc = _make_fake_mc(
        content="意图JSON",
        reasoning_native=True,
        reasoning_extra_body={"thinking": {"type": "enabled"}},
        captured=captured,
    )
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    await llm_client.chat([{"role": "user", "content": "hi"}], call_category="intent")

    assert "extra_body" not in captured[0]


def test_build_reasoning_kwargs_empty_when_no_extra_body(monkeypatch):
    _enable_thinking(monkeypatch, mode="native")
    mc = _make_fake_mc(reasoning_native=True, reasoning_extra_body={})
    assert thinking.build_reasoning_kwargs(mc, call_category="chat") == {}


# ===========================================================================
# 4. native 流式：思考与可见正文隔离
# ===========================================================================

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


@pytest.mark.asyncio
async def test_chat_stream_buffers_until_think_closes(monkeypatch):
    from core import llm_client

    _disable_thinking(monkeypatch)
    mc = _make_stream_mc(["<think>", "盘算中", "</think>", "你好", "呀"])
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    out = []
    async for piece in llm_client.chat_stream([{"role": "user", "content": "hi"}]):
        out.append(piece)

    assert "".join(out) == "你好呀"


@pytest.mark.asyncio
async def test_chat_stream_no_think_tag_passthrough_unchanged(monkeypatch):
    from core import llm_client

    _disable_thinking(monkeypatch)
    mc = _make_stream_mc(["你", "好", "呀"])
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    out = []
    async for piece in llm_client.chat_stream([{"role": "user", "content": "hi"}]):
        out.append(piece)

    assert "".join(out) == "你好呀"


@pytest.mark.asyncio
async def test_chat_stream_unclosed_think_archived_without_display(monkeypatch):
    from core import llm_client

    _disable_thinking(monkeypatch)
    mc = _make_stream_mc(["<think>", "一直没闭合的思考内容"])
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    out = []
    async for piece in llm_client.chat_stream([{"role": "user", "content": "hi"}]):
        out.append(piece)

    assert "".join(out) == ""
    from core.llm_reasoning_store import query
    entry = query(call_id=query()[0]["call_id"])
    assert entry["parts"] == [{"source": "inline_think", "text": "一直没闭合的思考内容"}]


# ===========================================================================
# 7. reality_output_scrubber 兜底剥除内联 think 标签
# ===========================================================================

def test_scrubber_strips_think_tags_as_final_fallback():
    from core.reality_output_scrubber import scrub_reality_output_text

    text = "<think>心里嘀咕了一下</think>今天天气不错。"
    assert scrub_reality_output_text(text) == "今天天气不错。"


# ===========================================================================
# 8. 铁律：chat_turn 剥离 reasoning_content / 内联 think，不带进 assistant_message
# ===========================================================================

@pytest.mark.asyncio
async def test_chat_turn_strips_reasoning_content_and_think_tags(monkeypatch):
    from core import llm_client

    _disable_thinking(monkeypatch)

    async def fake_create(**kwargs):
        msg = types.SimpleNamespace(
            content="<think>不该出现在历史里的思考</think>好的没问题",
            tool_calls=None,
            model_dump=lambda exclude_none=True: {
                "role": "assistant",
                "content": "<think>不该出现在历史里的思考</think>好的没问题",
                "reasoning_content": "不该出现在历史里的思考",
            },
        )
        choice = types.SimpleNamespace(message=msg, finish_reason="stop")
        return types.SimpleNamespace(choices=[choice])

    completions = types.SimpleNamespace(create=fake_create)
    chat_obj = types.SimpleNamespace(completions=completions)
    fake_client = types.SimpleNamespace(chat=chat_obj)
    mc = ModelClient(
        name="test", provider_kind="deepseek", model="m", tool_call_mode="function_calling",
        prompt_style="narrative", params={}, client=fake_client,
    )
    monkeypatch.setattr(llm_client, "get_model_client", lambda cat, char_id=None: mc)

    turn = await llm_client.chat_turn([{"role": "user", "content": "hi"}], tools=[])

    assert turn.content == "好的没问题"
    assert "reasoning_content" not in turn.assistant_message
    assert turn.assistant_message["content"] == "好的没问题"
