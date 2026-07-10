"""
tests/test_run_llm_retry.py — Brief 50 · 工单G.1

覆盖 core/pipeline.py::Pipeline.run_llm() 的实际重试语义。

run_llm() 把 llm_client.chat() 包一层 core/error_handler.py::with_retry：
  - 按 config.error.max_retries（默认3）重试，每次失败之间 sleep
    config.error.retry_delay_seconds（默认2）。
  - 全部重试耗尽后**不重新抛出异常**，而是返回 config.error.fallback_message
    （默认"我现在有点累，等会儿再聊～"）——降级，不是抛出。
  - 每次重试都是对同一个 messages 参数的重新调用，不产生累积/重复副作用。

先读 core/error_handler.py::with_retry 源码确认了以上实际行为，再写断言，不凭空假设次数。
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest


@dataclass
class _MockCharacter:
    name: str = "Companion"


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    """重试间不真实 sleep，测试固定 max_retries/retry_delay/fallback_message。"""
    import core.error_handler as error_handler

    monkeypatch.setattr(error_handler.asyncio, "sleep", AsyncMock())

    import core.config_loader as config_loader

    fixed_config = {
        "error": {
            "max_retries": 3,
            "retry_delay_seconds": 0,
            "fallback_message": "我现在有点累，等会儿再聊～",
        },
        "anti_collapse": {"prefix_retry": False},
    }
    monkeypatch.setattr(config_loader, "get_config", lambda: fixed_config)


def _make_pipeline():
    from core.pipeline import Pipeline

    return Pipeline(_MockCharacter(), lore_engine=None)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 首次失败、重试成功
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_first_failure_then_success_returns_reply(monkeypatch, sandbox):
    """第一次调用抛异常，第二次成功 → run_llm 返回成功结果，chat 恰好被调用2次。"""
    import core.llm_client as llm_client

    calls = []

    async def fake_chat(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            raise RuntimeError("first attempt fails")
        return "回复文本"

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    pipeline = _make_pipeline()
    reply = await pipeline.run_llm([{"role": "user", "content": "hi"}])

    assert reply == "回复文本"
    assert len(calls) == 2, f"expected exactly 2 chat() calls, got {len(calls)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 连续失败：耗尽重试后降级为 fallback_message，不抛出
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_all_retries_fail_degrades_to_fallback_message(monkeypatch, sandbox):
    """max_retries 次全部失败 → run_llm 不抛出异常，返回 config.error.fallback_message。"""
    import core.llm_client as llm_client

    call_count = 0

    async def always_fail(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError(f"attempt {call_count} fails")

    monkeypatch.setattr(llm_client, "chat", always_fail)

    pipeline = _make_pipeline()
    reply = await pipeline.run_llm([{"role": "user", "content": "hi"}])

    assert reply == "我现在有点累，等会儿再聊～"
    assert call_count == 3, f"expected exactly max_retries=3 chat() calls, got {call_count}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 重试间不重复副作用：每次调用收到同一份 messages，不累积/不重复追加
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_retries_do_not_duplicate_or_mutate_messages(monkeypatch, sandbox):
    """三次重试收到的 messages 内容完全一致（同一份输入，不因重试累积追加）。"""
    import core.llm_client as llm_client

    received: list[list[dict]] = []

    async def always_fail(messages, **kwargs):
        received.append(list(messages))
        raise RuntimeError("boom")

    monkeypatch.setattr(llm_client, "chat", always_fail)

    pipeline = _make_pipeline()
    original_messages = [{"role": "user", "content": "hi"}]
    await pipeline.run_llm(original_messages)

    assert len(received) == 3
    assert all(msgs == original_messages for msgs in received), (
        f"messages must be identical across retries, got: {received}"
    )
    # run_llm 本身也不应该就地修改调用方传入的 messages 列表
    assert original_messages == [{"role": "user", "content": "hi"}]
