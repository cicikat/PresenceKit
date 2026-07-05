"""
tests/test_anti_collapse_prefix_governance.py — CC 任务 24 · 2.2

「嗯。」句首坍缩治理三层措施回归测试：
(a) 历史投影去同质：build() 组装层9历史时，填充词前缀命中 → 只保留最早一条完整前缀，
    其余各条剥掉开头，且不修改传入的原始 history 对象（不写回 short_term）。
(b) 提示文案避免 priming：填充词前缀命中时用不复读字面的文案；非填充词前缀保留引用式文案。
(c) 输出端校验重试：Pipeline._anti_collapse_prefix_retry 命中 P 时追加强指令重试一次，
    重试仍命中且 P 为填充词时剥离前缀。
"""

from __future__ import annotations

import pytest

from core.memory.short_term import (
    detect_reply_homogeneity,
    detect_reply_homogeneity_prefix,
    is_filler_prefix,
)
from core.prompt_builder import _dedupe_filler_prefix_history


# ── is_filler_prefix ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("prefix", ["嗯。", "啊，", "呃…", "哦～", "唔,", "哈!"])
def test_is_filler_prefix_true(prefix):
    assert is_filler_prefix(prefix)


@pytest.mark.parametrize("prefix", ["现在，", "那你", "", "。嗯"])
def test_is_filler_prefix_false(prefix):
    assert not is_filler_prefix(prefix)


# ── detect_reply_homogeneity_prefix / detect_reply_homogeneity ──────────────

def _assistant_history(contents: list[str]) -> list[dict]:
    return [{"role": "assistant", "content": c} for c in contents]


def test_detect_prefix_returns_raw_prefix():
    history = _assistant_history(["嗯。今天还好", "嗯。没事", "嗯。你说吧"])
    assert detect_reply_homogeneity_prefix(history) == "嗯。"


def test_detect_prefix_none_when_insufficient_hits():
    history = _assistant_history(["嗯。今天还好", "挺好的呀", "嗯。你说吧"])
    assert detect_reply_homogeneity_prefix(history) is None


def test_hint_text_no_priming_for_filler_prefix():
    history = _assistant_history(["嗯。今天还好", "嗯。没事", "嗯。你说吧"])
    hint = detect_reply_homogeneity(history)
    assert hint is not None
    assert "嗯" not in hint  # 不复读字面前缀
    assert "语气词" in hint


def test_hint_text_quotes_literal_for_non_filler_prefix():
    history = _assistant_history(["现在，我们走吧", "现在，你听我说", "现在，别闹了"])
    hint = detect_reply_homogeneity(history)
    assert hint is not None
    assert "现在" in hint  # 非填充词前缀（prefix_len=2 → "现在"）保留引用式文案


# ── _dedupe_filler_prefix_history (build() 侧历史投影) ───────────────────────

def test_dedupe_keeps_first_strips_rest():
    history = [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "嗯。在的"},
        {"role": "user", "content": "干嘛呢"},
        {"role": "assistant", "content": "嗯。没干嘛"},
        {"role": "user", "content": "陪我说话"},
        {"role": "assistant", "content": "嗯。好啊"},
    ]
    original_snapshot = [dict(m) for m in history]

    projected = _dedupe_filler_prefix_history(history, "嗯。")

    assistant_contents = [m["content"] for m in projected if m["role"] == "assistant"]
    assert assistant_contents[0] == "嗯。在的"  # 最早一条保留完整前缀
    assert assistant_contents[1] == "没干嘛"     # 后续剥掉前缀
    assert assistant_contents[2] == "好啊"

    # 原始 history 对象不受影响（不写回 short_term）
    assert history == original_snapshot


def test_dedupe_sets_raw_content_on_stripped_messages_only():
    history = [
        {"role": "assistant", "content": "嗯。第一条"},
        {"role": "assistant", "content": "嗯。第二条"},
    ]
    projected = _dedupe_filler_prefix_history(history, "嗯。")
    assert "_raw_content" not in projected[0]
    assert projected[1]["_raw_content"] == "嗯。第二条"
    assert projected[1]["content"] == "第二条"


def test_dedupe_ignores_messages_not_matching_prefix():
    history = [
        {"role": "assistant", "content": "嗯。第一条"},
        {"role": "assistant", "content": "完全不同的开头"},
        {"role": "assistant", "content": "嗯。第三条"},
    ]
    projected = _dedupe_filler_prefix_history(history, "嗯。")
    contents = [m["content"] for m in projected]
    assert contents == ["嗯。第一条", "完全不同的开头", "第三条"]


# ── Pipeline._anti_collapse_prefix_retry (输出端校验重试) ────────────────────

def _make_pipeline():
    from core.pipeline import Pipeline
    return Pipeline.__new__(Pipeline)


def _history_messages(contents: list[str], raw: dict[int, str] | None = None) -> list[dict]:
    raw = raw or {}
    out = []
    for i, c in enumerate(contents):
        m = {"role": "assistant", "content": c, "_layer": "9_history"}
        if i in raw:
            m["_raw_content"] = raw[i]
        out.append(m)
    return out


@pytest.mark.asyncio
async def test_prefix_retry_not_triggered_when_reply_does_not_match(monkeypatch):
    pipeline = _make_pipeline()
    messages = _history_messages(["嗯。第一条", "第二条", "第三条"], raw={1: "嗯。第二条", 2: "嗯。第三条"})

    called = {"chat": 0}

    async def _fake_chat(_messages):
        called["chat"] += 1
        return "完全不同的开口"

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    result = await pipeline._anti_collapse_prefix_retry(messages, "完全不一样的回复开头")
    assert result == "完全不一样的回复开头"
    assert called["chat"] == 0  # 未命中前缀，不重试


@pytest.mark.asyncio
async def test_prefix_retry_strips_filler_prefix_on_second_hit(monkeypatch):
    pipeline = _make_pipeline()
    messages = _history_messages(["嗯。第一条", "第二条", "第三条"], raw={1: "嗯。第二条", 2: "嗯。第三条"})

    async def _fake_chat(_messages):
        return "嗯。这次还是这样开头"

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    result = await pipeline._anti_collapse_prefix_retry(messages, "嗯。原始回复也这样开头")
    assert result == "这次还是这样开头"


@pytest.mark.asyncio
async def test_prefix_retry_accepts_retry_result_when_prefix_gone(monkeypatch):
    pipeline = _make_pipeline()
    messages = _history_messages(["嗯。第一条", "第二条", "第三条"], raw={1: "嗯。第二条", 2: "嗯。第三条"})

    async def _fake_chat(_messages):
        return "换了个开头说话"

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    result = await pipeline._anti_collapse_prefix_retry(messages, "嗯。原始回复")
    assert result == "换了个开头说话"


@pytest.mark.asyncio
async def test_prefix_retry_disabled_by_config(monkeypatch):
    pipeline = _make_pipeline()
    messages = _history_messages(["嗯。第一条", "第二条", "第三条"], raw={1: "嗯。第二条", 2: "嗯。第三条"})

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"anti_collapse": {"prefix_retry": False}},
    )

    async def _fake_chat(_messages):
        raise AssertionError("不应该重试")

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    result = await pipeline._anti_collapse_prefix_retry(messages, "嗯。原始回复")
    assert result == "嗯。原始回复"
