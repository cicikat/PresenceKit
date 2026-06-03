"""
tests/test_qq_dream_guard.py — QQ 入梦 guard

覆盖场景：
  1. DREAM_ACTIVE 时 QQ owner 消息被拒（不进入 pipeline）
  2. DREAM_CLOSING 时同样被拒
  3. 被拒后 post_process 不被调用
  4. 被拒后无 short_term / event_log 写入
  5. 非 dream 状态（REALITY_CHAT）时 owner 消息正常进入 pipeline
  6. 非 owner 消息在 DREAM_ACTIVE 时不被 guard 拦截（guard 只限 owner）
  7. stamp_qq() 在非梦境 owner 聊天中仍被正确使用（WriteEnvelope 无误伤）
  8. get_reality_guard_status 异常时 guard fail-closed（不进入 pipeline）— P2.4
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_OWNER_ID = "99999"
_OTHER_ID = "88888"


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_msg(user_id: str = _OWNER_ID, content: str = "你好", group_id=None) -> dict:
    return {
        "user_id": user_id,
        "content": content,
        "sender_name": user_id,
        **({"group_id": group_id} if group_id else {}),
    }


def _write_dream_state(sandbox, uid: str, status: str):
    from core.dream.dream_state import write_state
    write_state(uid, {"status": status, "user_id": uid})


def _patch_pipeline(monkeypatch):
    """Return a fake pipeline and wire it into main._pipeline."""
    import main as _main
    fake = MagicMock()
    fake.character = MagicMock()
    fake.character.name = "叶瑄"
    fake.author_note_extra = ""
    fake.fetch_context = AsyncMock(return_value={})
    fake.build_prompt = MagicMock(return_value=([], {"pending_paths": []}))
    fake.run_llm = AsyncMock(return_value="回复内容")
    fake.post_process = AsyncMock(return_value={"turn_id": "t1", "critical_written": True, "emotion": "neutral"})
    monkeypatch.setattr(_main, "_pipeline", fake)
    return fake


def _patch_text_output(monkeypatch):
    """Capture text_output.send calls."""
    sent = []
    mock_send = AsyncMock(side_effect=lambda target, segments, is_group: sent.append(segments))

    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", mock_send)
    return sent


def _patch_scheduler_noise(monkeypatch):
    """Silence scheduler / presence side effects."""
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


def _patch_response_processor(monkeypatch):
    """Return a simple reply with <say> to verify stripping."""
    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])


def _patch_tool_dispatcher(monkeypatch):
    import core.tool_dispatcher as _td
    _td._TOOL_REGISTRY = {}
    monkeypatch.setattr(_td, "get_probe_prompt", lambda loc: "")
    monkeypatch.setattr(_td, "get_tools_schema", lambda categories=None: [])


def _patch_llm_client(monkeypatch):
    import core.llm_client as _llm
    monkeypatch.setattr(_llm, "chat", AsyncMock(return_value=""))
    monkeypatch.setattr(_llm, "parse_tool_call_response", lambda r: [])


def _patch_config(monkeypatch, owner_id: str = _OWNER_ID):
    import core.config_loader as _cl
    monkeypatch.setattr(_cl, "get_config", lambda: {
        "scheduler": {"owner_id": owner_id},
        "llm": {"tool_call_mode": "function_calling"},
    })


def _patch_group_context(monkeypatch):
    from core.memory import group_context as _gc
    monkeypatch.setattr(_gc, "append", lambda *a, **kw: None)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DREAM_ACTIVE → owner QQ 消息被拒，pipeline 不被调用
# ═══════════════════════════════════════════════════════════════════════════════

async def test_dream_active_rejects_owner_message(sandbox, monkeypatch):
    """DREAM_ACTIVE 时 handle_message 提前返回，pipeline.run_llm 未被调用。"""
    from core.dream.dream_state import DreamStatus

    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.DREAM_ACTIVE.value)
    _patch_config(monkeypatch)
    _patch_scheduler_noise(monkeypatch)
    fake_pipeline = _patch_pipeline(monkeypatch)
    sent = _patch_text_output(monkeypatch)

    import main as _main
    await _main.handle_message(_make_msg())

    fake_pipeline.run_llm.assert_not_called()
    fake_pipeline.post_process.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DREAM_CLOSING → 同样被拒
# ═══════════════════════════════════════════════════════════════════════════════

async def test_dream_closing_rejects_owner_message(sandbox, monkeypatch):
    """DREAM_CLOSING 状态下 QQ owner 消息同样被 guard 拦截。"""
    from core.dream.dream_state import DreamStatus

    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.DREAM_CLOSING.value)
    _patch_config(monkeypatch)
    _patch_scheduler_noise(monkeypatch)
    fake_pipeline = _patch_pipeline(monkeypatch)

    import main as _main
    await _main.handle_message(_make_msg())

    fake_pipeline.run_llm.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 被拒后 post_process 不被调用（不写 runtime/memory）
# ═══════════════════════════════════════════════════════════════════════════════

async def test_dream_guard_no_post_process(sandbox, monkeypatch):
    """Guard 拦截后 post_process（含 short_term / event_log 写入）不被调用。"""
    from core.dream.dream_state import DreamStatus

    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.DREAM_ACTIVE.value)
    _patch_config(monkeypatch)
    _patch_scheduler_noise(monkeypatch)
    fake_pipeline = _patch_pipeline(monkeypatch)

    # Also patch short_term / event_log to assert they're untouched
    import core.memory.short_term as _st
    import core.memory.event_log as _el
    st_append = MagicMock(return_value=True)
    el_append = MagicMock(return_value=True)
    monkeypatch.setattr(_st, "append", st_append)
    monkeypatch.setattr(_el, "append", el_append)

    import main as _main
    await _main.handle_message(_make_msg())

    fake_pipeline.post_process.assert_not_called()
    st_append.assert_not_called()
    el_append.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. REALITY_CHAT → owner 消息正常进入 pipeline
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reality_chat_passes_through(sandbox, monkeypatch):
    """非梦境状态下 owner QQ 消息正常到达 pipeline（run_llm 被调用）。"""
    from core.dream.dream_state import DreamStatus

    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.REALITY_CHAT.value)
    _patch_config(monkeypatch)
    _patch_scheduler_noise(monkeypatch)
    _patch_text_output(monkeypatch)
    _patch_response_processor(monkeypatch)
    _patch_tool_dispatcher(monkeypatch)
    _patch_llm_client(monkeypatch)
    _patch_group_context(monkeypatch)
    fake_pipeline = _patch_pipeline(monkeypatch)

    # Patch presence
    try:
        import core.presence as _pr
        monkeypatch.setattr(_pr, "update_last_message", lambda uid: None)
    except Exception:
        pass

    # Patch memory / profile lookups needed inside handle_message
    try:
        import core.memory.user_profile as _up
        monkeypatch.setattr(_up, "load", lambda uid: {"location": "杭州"})
    except Exception:
        pass

    import main as _main
    await _main.handle_message(_make_msg())

    fake_pipeline.run_llm.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 非 owner 消息在 DREAM_ACTIVE 时不被拦截（guard 限 owner）
# ═══════════════════════════════════════════════════════════════════════════════

async def test_non_owner_not_blocked_by_dream_guard(sandbox, monkeypatch):
    """非 owner 用户发消息时，dream guard 不拦截（guard 只作用于 owner）。"""
    from core.dream.dream_state import DreamStatus

    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.DREAM_ACTIVE.value)
    _patch_config(monkeypatch, owner_id=_OWNER_ID)
    _patch_scheduler_noise(monkeypatch)
    _patch_text_output(monkeypatch)
    _patch_response_processor(monkeypatch)
    _patch_tool_dispatcher(monkeypatch)
    _patch_llm_client(monkeypatch)
    _patch_group_context(monkeypatch)
    fake_pipeline = _patch_pipeline(monkeypatch)

    try:
        import core.memory.user_profile as _up
        monkeypatch.setattr(_up, "load", lambda uid: {"location": "杭州"})
    except Exception:
        pass

    # Message from a different user (not owner)
    import main as _main
    await _main.handle_message(_make_msg(user_id=_OTHER_ID))

    # pipeline should be entered for non-owner
    fake_pipeline.run_llm.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. get_reality_guard_status 异常时 guard fail-closed（不进入 pipeline）
# ═══════════════════════════════════════════════════════════════════════════════

async def test_dream_guard_fail_closed_on_exception(sandbox, monkeypatch):
    """get_reality_guard_status 抛异常时 guard fail-closed，owner QQ 消息被拒，不进入 pipeline。"""
    _patch_config(monkeypatch)
    _patch_scheduler_noise(monkeypatch)
    sent = _patch_text_output(monkeypatch)
    fake_pipeline = _patch_pipeline(monkeypatch)

    import core.dream.dream_state as _ds
    monkeypatch.setattr(
        _ds, "get_reality_guard_status",
        lambda uid: (_ for _ in ()).throw(RuntimeError("disk error")),
    )

    import main as _main
    await _main.handle_message(_make_msg())

    # Fail-closed: pipeline not reached
    fake_pipeline.run_llm.assert_not_called()
    # User-visible rejection message sent
    assert len(sent) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 7. stamp_qq() 在非梦境 owner 聊天中仍被正确使用
# ═══════════════════════════════════════════════════════════════════════════════

async def test_stamp_qq_used_in_reality_chat(sandbox, monkeypatch):
    """非梦境时 handle_message 的 post_process 携带 stamp_qq() envelope（QQ 现实聊天无误伤）。"""
    from core.dream.dream_state import DreamStatus
    from core.write_envelope import stamp_qq

    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.REALITY_CHAT.value)
    _patch_config(monkeypatch)
    _patch_scheduler_noise(monkeypatch)
    _patch_text_output(monkeypatch)
    _patch_response_processor(monkeypatch)
    _patch_tool_dispatcher(monkeypatch)
    _patch_llm_client(monkeypatch)
    _patch_group_context(monkeypatch)

    try:
        import core.memory.user_profile as _up
        monkeypatch.setattr(_up, "load", lambda uid: {"location": "杭州"})
    except Exception:
        pass

    captured_envelopes = []

    import main as _main
    fake = MagicMock()
    fake.character = MagicMock()
    fake.character.name = "叶瑄"
    fake.author_note_extra = ""
    fake.fetch_context = AsyncMock(return_value={})
    fake.build_prompt = MagicMock(return_value=([], {"pending_paths": []}))
    fake.run_llm = AsyncMock(return_value="回复内容")

    async def fake_post_process(uid, content, reply, *args, **kwargs):
        captured_envelopes.append(kwargs.get("envelope"))
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake.post_process = fake_post_process
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main.handle_message(_make_msg())

    # Allow the asyncio.create_task to run
    await asyncio.sleep(0.01)

    assert len(captured_envelopes) == 1
    env = captured_envelopes[0]
    expected = stamp_qq()
    assert env.can_write_memory == expected.can_write_memory
    assert env.can_affect_mood == expected.can_affect_mood
    assert env.source == expected.source
