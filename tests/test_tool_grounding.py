from __future__ import annotations

import time

import pytest


@pytest.mark.asyncio
async def test_required_intent_is_marked_even_when_tool_is_not_exposed(monkeypatch):
    from core import tool_dispatcher
    from core.pretool_router import route_pretool

    monkeypatch.setattr(tool_dispatcher, "get_tools_schema", lambda categories=None: [])
    monkeypatch.setattr("core.growth.mcp_proficiency.filter_schemas", lambda items, char_id: items)
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *args, **kwargs: True)
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "weather", {
        "keywords": ["天气"], "category": "info", "parameters": {"required": []},
    })

    class State:
        NORMAL = "normal"
        WAITING_CONFIRM = "waiting_confirm"
        WAITING_INPUT = "waiting_input"
        status = NORMAL

    result = await route_pretool(
        "查天气", "u-ground", "c1", "qq", "u-ground", False, State(),
        tool_loop_enabled=True, categories=["info"],
    )
    assert result.must_call_tool is True
    assert result.required_tool_names == {"weather"}
    assert result.route == "skipped_for_tool_loop"


def _grounding_messages(validity="execution_failed", names=("weather",)):
    from core.tool_grounding import GROUNDING_LAYER
    return [{
        "role": "system",
        "_layer": GROUNDING_LAYER,
        "_tool_grounding": {
            "required": True, "tool_names": list(names), "result_validity": validity,
        },
    }]


def test_failed_required_call_detects_claim_without_rewriting():
    from core.tool_grounding import check_unverified_claim

    hit = check_unverified_claim("已经查到北京天气了。", _grounding_messages())
    assert hit and "已经查到" in hit["snippet"]
    assert "execution_failed" in hit["basis"]


@pytest.mark.asyncio
async def test_pipeline_guard_rewrites_once_with_nudge_and_records_trace(monkeypatch):
    from core import runtime_signal_observability as obs
    obs._reset_for_tests()
    pipeline = _make_pipeline()
    seen = {"n": 0}

    async def _fake_chat(messages, **_kwargs):
        seen["n"] += 1
        seen["last"] = messages[-1]["content"]
        return "唔，这个我还没查成，等下再试试。"

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    result = await pipeline._guard_unverified_claim(_grounding_messages(), "已经查到北京天气了。")
    assert result == "唔，这个我还没查成，等下再试试。"
    assert seen["n"] == 1
    assert "没有得到可确认的成功结果" in seen["last"]
    assert "我还没能实际完成这一步" not in result
    codes = {(s["category"], s["code"]) for s in obs.snapshot()["signals"]}
    assert ("tool_grounding", "unverified_claim") in codes


@pytest.mark.asyncio
async def test_pipeline_guard_passes_through_when_retry_still_claims(monkeypatch):
    pipeline = _make_pipeline()
    calls = {"n": 0}

    async def _fake_chat(_messages, **_kwargs):
        calls["n"] += 1
        return "已经查到了呀"

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    result = await pipeline._guard_unverified_claim(_grounding_messages(), "已经查到了")
    assert result == "已经查到了呀"
    assert calls["n"] == 1  # 最多重写 1 次


@pytest.mark.asyncio
async def test_pipeline_guard_no_llm_call_when_no_claim(monkeypatch):
    pipeline = _make_pipeline()

    async def _boom(_messages, **_kwargs):
        raise AssertionError("must not call")

    monkeypatch.setattr("core.llm_client.chat", _boom)
    assert await pipeline._guard_unverified_claim(_grounding_messages(), "我在想怎么说") == "我在想怎么说"


@pytest.mark.parametrize("text", [
    "我刚才看到了你发的照片",
    "你完成了吗？",
    "你已经发送了吗",
    "我还没完成这件事",
    "没有查到相关的结果",
    "你说「已经完成了」对吧",
    "你发的那个文件我已经读过一点点点",
])
def test_claim_regex_ignores_negation_question_user_subject_and_quotes(text):
    from core.tool_grounding import find_unverified_claim

    assert find_unverified_claim(text) is None


def test_claim_regex_still_catches_plain_claim():
    from core.tool_grounding import find_unverified_claim

    assert find_unverified_claim("好的。我已经打开了。") == "我已经打开了"


def test_successful_current_tool_result_allows_claim():
    from core.tool_grounding import check_unverified_claim

    assert check_unverified_claim(
        "已经查到北京天气了。", _grounding_messages("current_turn"),
    ) is None


def test_non_loop_system_layer_tool_result_counts_as_success():
    from core.tool_grounding import check_unverified_claim
    from core.tools.tool_result import frame_tool_result

    messages = _grounding_messages("none") + [{
        "role": "system", "_layer": "10_tool_result",
        "content": frame_tool_result("多云", char_name="C", validity="current_turn"),
    }]
    assert check_unverified_claim("已经查到北京天气了。", messages) is None


@pytest.mark.parametrize("status,confirm,kind", [
    ("tool_executed", None, "ok"),          # MCP / self_db / self_tool 成功均为 tool_executed
    ("discovery", None, "info"),            # load_tools_* 回执
    ("confirmation_required", "确认吗", "pending_confirm"),
    ("tool_failed", None, "failed"),
    ("outcome_unknown", None, "failed"),
])
def test_classify_tool_outcome(status, confirm, kind):
    from core.tool_grounding import classify_tool_outcome

    assert classify_tool_outcome(status, confirm) == kind


@pytest.mark.parametrize("status,has_result,validity", [
    (None, True, "current_turn"),
    ("tool_executed", True, "current_turn"),
    ("discovery", True, "none"),
    ("confirmation_required", False, "none"),
    ("tool_failed", True, "execution_failed"),
    ("outcome_unknown", True, "outcome_unknown"),
])
def test_grounding_validity_for_status(status, has_result, validity):
    from core.tool_grounding import grounding_validity_for_status

    assert grounding_validity_for_status(status, has_result) == validity


def test_history_trace_is_not_current_result():
    from core.memory.action_trace import format_trace_block

    block = format_trace_block([{
        "ts": time.time() - 3600,
        "tool": "weather",
        "status": "ok",
        "result_digest": "北京多云",
    }])
    assert "历史操作参考" in block
    assert "不是本轮工具结果" in block


# ── 工单 C1：工具 meta 文本泄漏检测 ──────────────────────────────────────────

def test_detect_tool_meta_leak_matches_discovery_fixed_phrases():
    from core.tool_grounding import detect_tool_meta_leak

    leaked = ("加载电脑桌面与应用操作的工具定义。含：desktop_minimize、desktop_restore。"
              "只发现工具，不执行任何业务操作；下一轮才能调用具体工具。")
    assert detect_tool_meta_leak(leaked)
    assert detect_tool_meta_leak("该分类已加载。请使用当前提供的具体工具。未执行任何业务操作。")


def test_detect_tool_meta_leak_matches_discovery_tool_name_prefix():
    from core.tool_grounding import detect_tool_meta_leak

    assert detect_tool_meta_leak("可以先调用 load_tools_desktop 看看有什么工具")


def test_detect_tool_meta_leak_matches_dense_tool_name_enumeration():
    from core.tool_grounding import detect_tool_meta_leak

    names = frozenset({"desktop_minimize", "desktop_restore", "get_time", "weather"})
    text = "我可以用 desktop_minimize、desktop_restore 和 get_time 来帮你"
    assert detect_tool_meta_leak(text, names)
    assert not detect_tool_meta_leak("我可以用 desktop_minimize 来帮你", names)


def test_detect_tool_meta_leak_does_not_flag_ordinary_mentions_of_tools():
    from core.tool_grounding import detect_tool_meta_leak

    assert not detect_tool_meta_leak("我今天没有调用任何工具，就是单纯聊聊天")
    assert not detect_tool_meta_leak("这个工具不执行危险操作，你可以放心用")
    assert not detect_tool_meta_leak("")
    assert not detect_tool_meta_leak(None)


def _make_pipeline():
    from core.pipeline import Pipeline
    return Pipeline.__new__(Pipeline)


_LEAKED = ("加载电脑桌面与应用操作的工具定义。含：desktop_minimize。"
           "只发现工具，不执行任何业务操作；下一轮才能调用具体工具。")


@pytest.mark.asyncio
async def test_guard_tool_meta_leak_passes_clean_reply_without_retry(monkeypatch):
    pipeline = _make_pipeline()
    called = {"chat": 0}

    async def _fake_chat(_messages, **_kwargs):
        called["chat"] += 1
        return "不该被调用"

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    result = await pipeline._guard_tool_meta_leak([], "今天天气不错，出去走走吧")
    assert result == "今天天气不错，出去走走吧"
    assert called["chat"] == 0


@pytest.mark.asyncio
async def test_guard_tool_meta_leak_retries_once_and_accepts_clean_retry(monkeypatch):
    pipeline = _make_pipeline()
    seen = {}

    async def _fake_chat(messages, **_kwargs):
        seen["messages"] = messages
        return "好呀，我们聊聊别的"

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    result = await pipeline._guard_tool_meta_leak([{"role": "user", "content": "hi"}], _LEAKED)
    assert result == "好呀，我们聊聊别的"
    # 重试请求里显式要求不得提及工具内部结构
    assert seen["messages"][-1]["role"] == "system"
    assert "工具" in seen["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_guard_tool_meta_leak_falls_back_when_retry_still_leaks(monkeypatch):
    pipeline = _make_pipeline()

    async def _fake_chat(_messages, **_kwargs):
        return _LEAKED

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    result = await pipeline._guard_tool_meta_leak([], _LEAKED)
    assert "只发现工具" not in result
    assert "load_tools_" not in result
    assert result  # 兜底话术非空


@pytest.mark.asyncio
async def test_guard_tool_meta_leak_is_fail_open_on_retry_error(monkeypatch):
    pipeline = _make_pipeline()

    async def _boom(_messages, **_kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr("core.llm_client.chat", _boom)
    result = await pipeline._guard_tool_meta_leak([], _LEAKED)
    assert result == _LEAKED  # 异常不阻断发送，保持原行为


@pytest.mark.asyncio
async def test_guard_tool_meta_leak_is_counted_in_runtime_signals(monkeypatch):
    from core import runtime_signal_observability as obs
    obs._reset_for_tests()
    pipeline = _make_pipeline()

    async def _fake_chat(_messages, **_kwargs):
        return "好呀"

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    await pipeline._guard_tool_meta_leak([], _LEAKED)
    codes = {(s["category"], s["code"]): s["count"] for s in obs.snapshot()["signals"]}
    assert codes[("tool_loop_discovery", "tool_meta_leak")] == 1


def test_tool_result_frame_carries_time_and_failure_validity():
    from core.tools.tool_result import frame_tool_result

    framed = frame_tool_result(
        "服务不可用",
        char_name="Companion",
        generated_at=0,
        validity="execution_failed",
    )
    assert "1970-01-01" in framed
    assert "本轮执行失败" in framed
    assert "不得当作已完成事实" in framed
