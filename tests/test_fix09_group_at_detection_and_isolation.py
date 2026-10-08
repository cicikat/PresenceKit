"""
tests/test_fix09_group_at_detection_and_isolation.py — FIX-09

覆盖场景：
  qq_adapter._parse_event @ 检测：
    1. 消息段数组含 at → 通过（NapCat array 格式）
    2. CQ 串含 at → 通过（兜底）
    3. 两者都无 @ → 丢弃（return None）
    4. _self_id 为空 → fail-loud，丢弃（return None）
    5. @ 标记从 content 中被清理（CQ 串格式）
    6. @ 标记从 content 中被清理（消息段数组格式）

  群聊隔离路径：
    7. handle_message 群消息走 _handle_group_message，不进 reality 主链
    8. _handle_group_message 完成：text_output.send 被调用，record_assistant_turn 未被调用
    9. _handle_group_message 将机器人回复追加进 group_context
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


@pytest.fixture(autouse=True)
def enable_group_admission(monkeypatch):
    from core import qq_admission
    monkeypatch.setattr(qq_admission, "get_config", lambda: {
        "qq": {"group_enabled": True}, "scheduler": {"owner_id": "99999"},
    })


_SELF_ID = "10086"
_GROUP_ID = "999"
_USER_ID = "12345"


# ═══════════════════════════════════════════════════════════════════════════════
# qq_adapter._parse_event 单元测试
# ═══════════════════════════════════════════════════════════════════════════════

def _make_raw_group(
    raw_message: str | None = None,
    message_array: list | None = None,
    user_id: str = _USER_ID,
    group_id: str = _GROUP_ID,
    text: str = "hello",
) -> dict:
    """构造 OneBot 11 群消息原始事件。raw_message=None 用 text 填充；""则保持空串。"""
    arr = message_array if message_array is not None else [{"type": "text", "data": {"text": text}}]
    rm = text if raw_message is None else raw_message
    return {
        "post_type": "message",
        "message_type": "group",
        "user_id": int(user_id),
        "group_id": int(group_id),
        "raw_message": rm,
        "message": arr,
        "sender": {"nickname": "TestUser"},
        "time": 0,
    }


def _set_self_id(value: str):
    import core.qq_adapter as _qa
    _qa._self_id = value


# ── 1. 消息段数组含 at → 通过 ─────────────────────────────────────────────────

def test_at_detection_via_array(monkeypatch):
    """NapCat 以消息段数组下发 @ 时，_parse_event 应返回消息而不是 None。"""
    _set_self_id(_SELF_ID)
    raw = _make_raw_group(
        raw_message="hello",  # 无 CQ at 串
        message_array=[
            {"type": "at", "data": {"qq": _SELF_ID}},
            {"type": "text", "data": {"text": "hello"}},
        ],
    )
    from core.qq_adapter import _parse_event
    result = _parse_event(raw)
    assert result is not None
    assert result["content"] == "hello"


# ── 2. CQ 串含 at → 通过（兜底） ─────────────────────────────────────────────

def test_at_detection_via_cq_string(monkeypatch):
    """raw_message 含 CQ at 串时（无消息段 at），_parse_event 应返回消息。"""
    _set_self_id(_SELF_ID)
    cq = f"[CQ:at,qq={_SELF_ID}] hello"
    raw = _make_raw_group(
        raw_message=cq,
        message_array=[{"type": "text", "data": {"text": "hello"}}],
    )
    from core.qq_adapter import _parse_event
    result = _parse_event(raw)
    assert result is not None


# ── 3. 两者都无 @ → 丢弃 ──────────────────────────────────────────────────────

def test_no_at_returns_none(monkeypatch):
    """群消息中没有 @ 机器人时，_parse_event 应返回 None。"""
    _set_self_id(_SELF_ID)
    raw = _make_raw_group(
        raw_message="hello",
        message_array=[{"type": "text", "data": {"text": "hello"}}],
    )
    from core.qq_adapter import _parse_event
    result = _parse_event(raw)
    assert result is None


# ── 4. _self_id 为空 → fail-loud，丢弃 ────────────────────────────────────────

def test_empty_self_id_returns_none_and_logs_error(monkeypatch, caplog):
    """_self_id 未初始化时，_parse_event 应 fail-loud（记录 error）并返回 None。"""
    import logging
    _set_self_id("")
    raw = _make_raw_group(
        message_array=[{"type": "at", "data": {"qq": _SELF_ID}}],
    )
    from core.qq_adapter import _parse_event
    with caplog.at_level(logging.ERROR, logger="core.qq_adapter"):
        result = _parse_event(raw)
    assert result is None
    assert any("_self_id" in r.message for r in caplog.records)
    _set_self_id(_SELF_ID)  # 复原


# ── 5. @ 标记被清理（CQ 串格式） ────────────────────────────────────────────

def test_cq_at_stripped_from_content(monkeypatch):
    """CQ at 串应从 content 中被清理，只保留实际文字。"""
    _set_self_id(_SELF_ID)
    cq = f"[CQ:at,qq={_SELF_ID}] 你好"
    raw = _make_raw_group(raw_message=cq, message_array=[])
    from core.qq_adapter import _parse_event
    result = _parse_event(raw)
    assert result is not None
    assert "[CQ:" not in result["content"]
    assert "你好" in result["content"]


# ── 6. @ 标记被清理（消息段数组格式） ───────────────────────────────────────

def test_array_at_stripped_from_content(monkeypatch):
    """消息段数组格式的 @ 经 _extract_text_content 不会留残留，content 只含文字。"""
    _set_self_id(_SELF_ID)
    raw = _make_raw_group(
        raw_message="",  # 让代码走 message_array 路径提取文本
        message_array=[
            {"type": "at", "data": {"qq": _SELF_ID}},
            {"type": "text", "data": {"text": "今天天气"}},
        ],
    )
    from core.qq_adapter import _parse_event
    result = _parse_event(raw)
    assert result is not None
    assert "今天天气" in result["content"]
    assert "[CQ:" not in result["content"]


# ═══════════════════════════════════════════════════════════════════════════════
# 群聊隔离路径测试
# ═══════════════════════════════════════════════════════════════════════════════

def _make_group_msg(user_id: str = _USER_ID, content: str = "hi", group_id: str = _GROUP_ID) -> dict:
    return {
        "user_id": user_id,
        "group_id": group_id,
        "content": content,
        "sender_name": "TestUser",
    }


def _patch_pipeline_for_group(monkeypatch):
    import main as _main
    fake = MagicMock()
    fake.character = MagicMock()
    fake.character.name = "Companion"
    fake.character.system_prompt = "你是一个AI助手。"
    fake.character.description = ""
    fake.character.personality = ""
    fake.run_llm = AsyncMock(return_value="好的！")
    monkeypatch.setattr(_main, "_pipeline", fake)
    return fake


def _patch_infra(monkeypatch):
    """静默外部副作用（scheduler / presence / config）。"""
    try:
        import core.scheduler.loop as _sl
        monkeypatch.setattr(_sl, "mark_user_active", lambda: None)
    except Exception:
        pass
    try:
        import core.presence as _pr
        monkeypatch.setattr(_pr, "update_last_message", lambda uid: None)
    except Exception:
        pass
    try:
        import core.scheduler.state_machine as _sm
        monkeypatch.setattr(_sm, "notify_owner_turn", lambda uid: None)
    except Exception:
        pass
    try:
        import core.config_loader as _cl
        monkeypatch.setattr(_cl, "get_config", lambda: {
            "scheduler": {"owner_id": "99999"},
            "memory": {"group_context_lines": 50},
        })
    except Exception:
        pass


def _patch_response_processor(monkeypatch):
    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])
    monkeypatch.setattr(_rp, "strip_render_tags", lambda s: s)


# ── 7. handle_message 群消息走隔离路径，不进 reality 主链 ───────────────────

async def test_group_message_skips_reality_chain(sandbox, monkeypatch):
    """群消息经 handle_message 后，record_assistant_turn 不被调用。"""
    _patch_pipeline_for_group(monkeypatch)
    _patch_infra(monkeypatch)
    _patch_response_processor(monkeypatch)

    sent = []
    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", AsyncMock(side_effect=lambda t, s, is_group=False: sent.append(s)))

    gc_appended = []
    from core.memory import group_context as _gc
    monkeypatch.setattr(_gc, "append", lambda gid, name, content: gc_appended.append((name, content)))
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])

    record_calls = []
    import core.turn_sink as _ts
    monkeypatch.setattr(_ts, "record_assistant_turn", AsyncMock(side_effect=lambda **kw: record_calls.append(kw)))

    import main as _main
    msg = _make_group_msg()
    await _main.handle_message(msg)

    assert len(sent) == 1, "text_output.send 应被调用一次"
    assert record_calls == [], "record_assistant_turn 不应被调用（群聊隔离）"


# ── 8. _handle_group_message 直发回复，不写主记忆 ──────────────────────────

async def test_handle_group_message_sends_without_memory_write(sandbox, monkeypatch):
    """_handle_group_message 调用 text_output.send，但不调用 record_assistant_turn。"""
    _patch_pipeline_for_group(monkeypatch)
    _patch_infra(monkeypatch)
    _patch_response_processor(monkeypatch)

    sent = []
    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", AsyncMock(side_effect=lambda t, s, is_group=False: sent.append((t, s, is_group))))

    gc_appended = []
    from core.memory import group_context as _gc
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_gc, "append", lambda gid, name, content: gc_appended.append((name, content)))

    import core.turn_sink as _ts
    record_mock = AsyncMock()
    monkeypatch.setattr(_ts, "record_assistant_turn", record_mock)

    import main as _main
    await _main._handle_group_message(_GROUP_ID, "TestUser", "hi", _GROUP_ID)

    assert len(sent) == 1
    target, segments, is_group = sent[0]
    assert is_group is True
    assert target == _GROUP_ID
    record_mock.assert_not_called()


# ── 9. 机器人回复追加进 group_context ────────────────────────────────────────

async def test_bot_reply_appended_to_group_context(sandbox, monkeypatch):
    """_handle_group_message 成功回复后，应把机器人回复追加进 group_context。"""
    _patch_pipeline_for_group(monkeypatch)
    _patch_infra(monkeypatch)
    _patch_response_processor(monkeypatch)

    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", AsyncMock())

    gc_appended = []
    from core.memory import group_context as _gc
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_gc, "append", lambda gid, name, content: gc_appended.append((name, content)))

    import main as _main
    await _main._handle_group_message(_GROUP_ID, "TestUser", "hi", _GROUP_ID)

    # 机器人回复（名字为 char.name）应被追加
    bot_entries = [(n, c) for n, c in gc_appended if n == "Companion"]
    assert len(bot_entries) == 1
    assert "好的！" in bot_entries[0][1]
