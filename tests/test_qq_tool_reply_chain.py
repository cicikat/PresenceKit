"""
tests/test_qq_tool_reply_chain.py — P2.1: QQ 工具确认回复链路修复验证

覆盖：
  1. _reply_with_tool_result 触发 pipeline.post_process
  2. 传入 post_process 的 reply 是 scrub 后文本，不含动作行（*动作*）
  3. 发送到 QQ 的 segments 经过 strip_render_tags（无 <say> 标签）
  4. <say>你好</say> 不会原样发给 QQ
  5. envelope 是 stamp_qq()（can_write_memory=True, source=QQ）
  6. LLM 异常时不崩溃、不发送、不调用 post_process
  7. QQ 主消息路径（handle_message）不被破坏（smoke test）
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_fake_pipeline(llm_reply: str = "你好") -> MagicMock:
    fake = MagicMock()
    fake.character = MagicMock()
    fake.character.name = "TestChar"
    fake.author_note_extra = ""
    fake.build_prompt = MagicMock(return_value=([], {"pending_paths": []}))
    fake.run_llm = AsyncMock(return_value=llm_reply)
    fake.post_process = AsyncMock(
        return_value={"turn_id": "t1", "critical_written": True, "emotion": "neutral"}
    )
    return fake


def _patch_memory(monkeypatch):
    import core.memory.short_term as _st
    import core.memory.user_profile as _up
    import core.memory.group_context as _gc
    import core.user_relation as _ur
    monkeypatch.setattr(_st, "load_for_prompt", lambda uid: [])
    monkeypatch.setattr(_up, "load", lambda uid: {})
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_ur, "get_relation", lambda uid: {})


def _patch_text_output(monkeypatch):
    sent: list[list[str]] = []
    mock_send = AsyncMock(side_effect=lambda tgt, segs, grp: sent.append(list(segs)))
    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", mock_send)
    return sent


# ═══════════════════════════════════════════════════════════════════════════════
# 1. post_process 被调用
# ═══════════════════════════════════════════════════════════════════════════════

async def test_post_process_is_called(sandbox, monkeypatch):
    """工具确认回复后 pipeline.post_process 必须被调用（写入 short_term / event_log）。"""
    import main as _main

    fake = _make_fake_pipeline("这是结果。")
    monkeypatch.setattr(_main, "_pipeline", fake)
    _patch_memory(monkeypatch)
    _patch_text_output(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)
    await asyncio.sleep(0.05)

    fake.post_process.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 传入 post_process 的 reply 不含 *动作*
# ═══════════════════════════════════════════════════════════════════════════════

async def test_memory_reply_excludes_action_lines(sandbox, monkeypatch):
    """post_process 的 reply 参数经过 scrub，*动作行* 已被剥除。"""
    import main as _main

    # LLM 返回含动作行的混合回复
    raw = "*她轻轻抬起头*\n这是正常对话内容。"
    fake = _make_fake_pipeline(raw)
    monkeypatch.setattr(_main, "_pipeline", fake)
    _patch_memory(monkeypatch)
    _patch_text_output(monkeypatch)

    import core.response_processor as _rp
    # process returns the raw string as one segment (simplest path)
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    captured_replies: list[str] = []

    async def spy_post_process(uid, content, reply, *args, **kwargs):
        captured_replies.append(reply)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake.post_process = spy_post_process
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)
    await asyncio.sleep(0.05)

    assert captured_replies, "post_process should have been called"
    memory_reply = captured_replies[0]
    assert "*她轻轻抬起头*" not in memory_reply
    assert "这是正常对话内容。" in memory_reply


# ═══════════════════════════════════════════════════════════════════════════════
# 3. QQ 发送的 segments 经过 strip_render_tags
# ═══════════════════════════════════════════════════════════════════════════════

async def test_qq_segments_have_tags_stripped(sandbox, monkeypatch):
    """发送到 QQ 的文本不含 <say> / <thought> 等渲染标签。"""
    import main as _main

    fake = _make_fake_pipeline("<say>你好</say>")
    monkeypatch.setattr(_main, "_pipeline", fake)
    _patch_memory(monkeypatch)
    sent = _patch_text_output(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)

    assert sent, "text_output.send should have been called"
    all_text = " ".join(seg for segs in sent for seg in segs)
    assert "<say>" not in all_text
    assert "</say>" not in all_text


# ═══════════════════════════════════════════════════════════════════════════════
# 4. <say>你好</say> 不原样发出 <say>
# ═══════════════════════════════════════════════════════════════════════════════

async def test_say_tag_not_sent_raw(sandbox, monkeypatch):
    """<say>你好</say> 清洗后 QQ 只收到「你好」，不含标签。"""
    import main as _main

    fake = _make_fake_pipeline("<say>你好</say>")
    monkeypatch.setattr(_main, "_pipeline", fake)
    _patch_memory(monkeypatch)
    sent = _patch_text_output(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)

    assert sent
    all_text = " ".join(seg for segs in sent for seg in segs)
    assert "你好" in all_text
    assert "<say>" not in all_text


# ═══════════════════════════════════════════════════════════════════════════════
# 5. envelope 是 stamp_qq()
# ═══════════════════════════════════════════════════════════════════════════════

async def test_envelope_is_stamp_qq(sandbox, monkeypatch):
    """post_process 收到的 envelope 等价于 stamp_qq()（can_write_memory=True, source=QQ）。"""
    import main as _main
    from core.write_envelope import stamp_qq, SourceType

    fake = _make_fake_pipeline("回复内容。")
    _patch_memory(monkeypatch)
    _patch_text_output(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    captured_envelopes: list = []

    async def spy_post_process(uid, content, reply, *args, **kwargs):
        captured_envelopes.append(kwargs.get("envelope"))
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake.post_process = spy_post_process
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)
    await asyncio.sleep(0.05)

    assert captured_envelopes, "post_process should have been called"
    env = captured_envelopes[0]
    expected = stamp_qq()
    assert env.can_write_memory == expected.can_write_memory
    assert env.can_affect_mood == expected.can_affect_mood
    assert env.source == expected.source
    assert env.source == SourceType.QQ


# ═══════════════════════════════════════════════════════════════════════════════
# 6. LLM 异常：不崩溃、不发送、不调用 post_process
# ═══════════════════════════════════════════════════════════════════════════════

async def test_llm_error_handled_gracefully(sandbox, monkeypatch):
    """run_llm 抛异常时函数安全返回，text_output.send / post_process 均不被调用。"""
    import main as _main

    fake = _make_fake_pipeline()
    fake.run_llm = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    monkeypatch.setattr(_main, "_pipeline", fake)
    _patch_memory(monkeypatch)
    sent = _patch_text_output(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    # Should not raise
    await _main._reply_with_tool_result("tool_data", "u1", "u1", False)
    await asyncio.sleep(0.05)

    assert sent == [], "Nothing should be sent on LLM error"
    fake.post_process.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 7. QQ 主消息路径 smoke test（handle_message 不被破坏）
# ═══════════════════════════════════════════════════════════════════════════════

async def test_handle_message_main_path_intact(sandbox, monkeypatch):
    """handle_message 正常路径：run_llm 被调用，post_process 被调用，text_output.send 被调用。"""
    from core.dream.dream_state import DreamStatus, write_state

    _UID = "11111"
    write_state(_UID, {"status": DreamStatus.REALITY_CHAT.value, "user_id": _UID})

    import core.config_loader as _cl
    monkeypatch.setattr(_cl, "get_config", lambda: {
        "scheduler": {"owner_id": _UID},
        "llm": {"tool_call_mode": "function_calling"},
    })

    import core.scheduler.loop as _sl
    monkeypatch.setattr(_sl, "mark_user_active", lambda: None)
    import core.presence as _pr
    monkeypatch.setattr(_pr, "update_last_message", lambda uid: None)
    import core.scheduler.state_machine as _sm
    monkeypatch.setattr(_sm, "notify_owner_turn", lambda uid: None)

    import core.memory.user_profile as _up
    monkeypatch.setattr(_up, "load", lambda uid: {"location": "杭州"})
    import core.memory.group_context as _gc
    monkeypatch.setattr(_gc, "append", lambda *a, **kw: None)

    import core.tool_dispatcher as _td
    _td._TOOL_REGISTRY = {}
    monkeypatch.setattr(_td, "get_probe_prompt", lambda loc: "")
    monkeypatch.setattr(_td, "get_tools_schema", lambda categories=None: [])
    import core.llm_client as _llm
    monkeypatch.setattr(_llm, "chat", AsyncMock(return_value=""))
    monkeypatch.setattr(_llm, "parse_tool_call_response", lambda r: [])

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    sent = _patch_text_output(monkeypatch)

    import main as _main
    fake = _make_fake_pipeline("正常回复内容。")
    fake.fetch_context = AsyncMock(return_value={})
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main.handle_message({
        "user_id": _UID,
        "content": "你好",
        "sender_name": _UID,
    })
    await asyncio.sleep(0.05)

    fake.run_llm.assert_called_once()
    assert sent, "QQ main path should send a reply"
