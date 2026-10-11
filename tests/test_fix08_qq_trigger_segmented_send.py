"""
tests/test_fix08_qq_trigger_segmented_send.py — FIX-08: 触发器路径分段发送

验证 QQChannel.send 经 response_processor 切段后逐条发送，
与正常对话回复路径行为一致。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def legacy_broadcast_mode(monkeypatch):
    monkeypatch.setattr('core.qq_service.outbound_mode', lambda: 'all')


# ── 共享 fixture ──────────────────────────────────────────────────────────────

class _FakeChar:
    name = "叶瑄"


class _FakePipeline:
    character = _FakeChar()
    _active_character_id = None


# ═══════════════════════════════════════════════════════════════════════════════
# T1. QQChannel.send 通过 response_processor + text_output.send 分段发送
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_qq_channel_send_calls_text_output_send(monkeypatch):
    """QQChannel.send 必须调用 text_output.send 而非直接调 qq_adapter.send_message。"""
    from channels.qq import QQChannel

    text_output_calls = []

    async def fake_text_output_send(target_id, segments, is_group=False):
        text_output_calls.append((target_id, segments, is_group))

    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", fake_text_output_send)

    import core.pipeline_registry as _pr
    monkeypatch.setattr(_pr, "get", lambda: _FakePipeline())

    ch = QQChannel("owner123")
    await ch.send("你好", "owner123")

    assert len(text_output_calls) == 1
    target, segments, is_group = text_output_calls[0]
    assert target == "owner123"
    assert segments == ["你好"]
    assert is_group is False


@pytest.mark.asyncio
async def test_qq_channel_send_splits_long_content(monkeypatch):
    """超长内容应被切成多段传给 text_output.send。"""
    from channels.qq import QQChannel
    from core import response_processor

    # 构造超过单段上限的内容（两段）
    long_text = "a" * 250 + "\n\n" + "b" * 250

    text_output_calls = []

    async def fake_text_output_send(target_id, segments, is_group=False):
        text_output_calls.append((target_id, list(segments), is_group))

    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", fake_text_output_send)

    import core.pipeline_registry as _pr
    monkeypatch.setattr(_pr, "get", lambda: _FakePipeline())

    ch = QQChannel("owner123")
    await ch.send(long_text, "owner123")

    assert len(text_output_calls) == 1
    _, segments, _ = text_output_calls[0]
    # 结果应为多段
    assert len(segments) >= 2


@pytest.mark.asyncio
async def test_qq_channel_send_uses_target_id_when_provided(monkeypatch):
    """target_id 存在时应用 target_id 而非 user_id 作为发送目标。"""
    from channels.qq import QQChannel

    text_output_calls = []

    async def fake_text_output_send(target_id, segments, is_group=False):
        text_output_calls.append((target_id, segments, is_group))

    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", fake_text_output_send)

    import core.pipeline_registry as _pr
    monkeypatch.setattr(_pr, "get", lambda: _FakePipeline())

    ch = QQChannel("owner123")
    await ch.send("hello", "owner123", target_id="group456", is_group=True)

    target, _, is_group = text_output_calls[0]
    assert target == "group456"
    assert is_group is True


@pytest.mark.asyncio
async def test_qq_channel_send_works_without_pipeline(monkeypatch):
    """pipeline 未注册时 char_name 降级为空串，仍能正常切段发送。"""
    from channels.qq import QQChannel

    text_output_calls = []

    async def fake_text_output_send(target_id, segments, is_group=False):
        text_output_calls.append(segments)

    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", fake_text_output_send)

    import core.pipeline_registry as _pr
    monkeypatch.setattr(_pr, "get", lambda: None)

    ch = QQChannel("owner123")
    await ch.send("你好啊", "owner123")

    assert len(text_output_calls) == 1
    assert text_output_calls[0] == ["你好啊"]


@pytest.mark.asyncio
async def test_qq_channel_send_does_not_call_qq_adapter_directly(monkeypatch):
    """QQChannel.send 不应直接调用 qq_adapter.send_message（避免绕过分段逻辑）。"""
    from channels.qq import QQChannel

    direct_calls = []

    async def fake_qq_adapter_send(target, content, is_group):
        direct_calls.append(content)

    import core.qq_adapter as _qa
    monkeypatch.setattr(_qa, "send_message", fake_qq_adapter_send)

    # text_output.send 会调 qq_adapter，这里只拦截 QQChannel 的直接路径
    async def fake_text_output_send(target_id, segments, is_group=False):
        pass  # absorb the call

    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", fake_text_output_send)

    import core.pipeline_registry as _pr
    monkeypatch.setattr(_pr, "get", lambda: _FakePipeline())

    ch = QQChannel("owner123")
    await ch.send("你好", "owner123")

    # qq_adapter.send_message 不应被 QQChannel.send 直接调用
    assert direct_calls == []


# ═══════════════════════════════════════════════════════════════════════════════
# T2. 静态分析：QQChannel.send 不含直接 qq_adapter 调用
# ═══════════════════════════════════════════════════════════════════════════════

def test_qq_channel_source_uses_text_output_not_adapter():
    """channels/qq.py 的 send 方法体不应直接调用 qq_adapter.send_message。"""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "channels" / "qq.py").read_text(encoding="utf-8")

    # send 方法体内不应有直接的 qq_adapter.send_message 调用
    assert "qq_adapter.send_message" not in src, (
        "QQChannel.send 不应直接调用 qq_adapter.send_message，应经由 text_output.send"
    )
    # 应调用 text_output.send
    assert "text_output.send" in src, (
        "QQChannel.send 应调用 text_output.send 以实现分段发送"
    )
    # 应调用 response_processor.process
    assert "response_processor.process" in src, (
        "QQChannel.send 应调用 response_processor.process 切段"
    )
