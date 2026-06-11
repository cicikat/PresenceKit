"""
tests/test_scope_freeze_r1_n1_n10.py — R1 + N1 + N10 regression tests

覆盖验收标准：
  1. N1: intra-turn char hot-swap 不污染不同 char_id 桶
     （scope 冻结后，模拟热切换；fetch/build/post 全程用同一 char_id）
  2. R1: handle_message 内 conversation_lock 被持有，post_process 被 await
  3. N10: post_process 异常可观测（被 log_error 捕获，不静默丢失）
  4. _reply_with_tool_result 把 frozen_scope/char_id 传递给 post_process
  5. QQ 发送前经过 reality output scrub（动作行不进记忆）
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


_OWNER_ID = "77777"


# ── shared helpers ────────────────────────────────────────────────────────────

def _write_reality_state(uid: str):
    from core.dream.dream_state import DreamStatus, write_state
    write_state(uid, {"status": DreamStatus.REALITY_CHAT.value, "user_id": uid})


def _make_scope(uid: str, char_id: str = "char_a"):
    from core.memory.scope import MemoryScope
    return MemoryScope.reality_scope(uid, char_id)


def _patch_noise(monkeypatch, owner_id: str = _OWNER_ID):
    """Silence config / scheduler / presence side effects."""
    import core.config_loader as _cl
    monkeypatch.setattr(_cl, "get_config", lambda: {
        "scheduler": {"owner_id": owner_id},
        "llm": {"tool_call_mode": "function_calling"},
    })
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


def _patch_memory(monkeypatch):
    import core.memory.short_term as _st
    import core.memory.user_profile as _up
    import core.memory.group_context as _gc
    import core.user_relation as _ur
    monkeypatch.setattr(_st, "load_for_prompt", lambda uid, **kw: [])
    monkeypatch.setattr(_up, "load", lambda uid, **kw: {"location": "杭州"})
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_gc, "append", lambda *a, **kw: None)
    monkeypatch.setattr(_ur, "get_relation", lambda uid: {})


def _patch_output(monkeypatch):
    sent = []
    import core.output.text_output as _to
    monkeypatch.setattr(_to, "send", AsyncMock(
        side_effect=lambda tgt, segs, grp: sent.append(list(segs))
    ))
    return sent


def _patch_probe(monkeypatch):
    import core.tool_dispatcher as _td
    _td._TOOL_REGISTRY = {}
    monkeypatch.setattr(_td, "get_probe_prompt", lambda loc: "")
    monkeypatch.setattr(_td, "get_tools_schema", lambda categories=None: [])
    import core.llm_client as _llm
    monkeypatch.setattr(_llm, "chat", AsyncMock(return_value=""))
    monkeypatch.setattr(_llm, "parse_tool_call_response", lambda r: [])


def _make_pipeline(char_id: str = "char_a", llm_reply: str = "回复"):
    """Build a MagicMock pipeline pre-wired with a frozen scope."""
    from core.memory.scope import MemoryScope
    fake = MagicMock()
    fake.character = MagicMock()
    fake.character.name = "测试角色"
    fake.author_note_extra = ""
    fake._active_character_id = char_id
    fake._current_reality_scope = MagicMock(
        return_value=MemoryScope.reality_scope(_OWNER_ID, char_id)
    )
    fake.fetch_context = AsyncMock(return_value={})
    fake.build_prompt = MagicMock(return_value=([], {"pending_paths": []}))
    fake.run_llm = AsyncMock(return_value=llm_reply)
    fake.post_process = AsyncMock(
        return_value={"turn_id": "t1", "critical_written": True, "emotion": "neutral"}
    )
    return fake


# ═══════════════════════════════════════════════════════════════════════════════
# 1. N1: scope freeze — intra-turn hot-swap 不污染 char_id
# ═══════════════════════════════════════════════════════════════════════════════

async def test_n1_scope_frozen_across_fetch_build_post(sandbox, monkeypatch):
    """
    handle_message 在调用 fetch_context / build_prompt / post_process 时
    全部使用同一个冻结的 char_id，不受轮次中热切换影响。

    验证方式：记录 fetch_context / build_prompt / post_process 被调用时的
    frozen_scope.character_id / char_id 参数，断言三者一致且等于初始冻结值。
    """
    _write_reality_state(_OWNER_ID)
    _patch_noise(monkeypatch)
    _patch_memory(monkeypatch)
    _patch_output(monkeypatch)
    _patch_probe(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    import main as _main

    frozen_char_ids = {}

    async def _spy_fetch(uid, content, group_id=None, frozen_scope=None):
        frozen_char_ids["fetch"] = frozen_scope.character_id if frozen_scope else None
        return {}

    def _spy_build(uid, content, context, tool_result=None, tags=None, channel=None, char_id=None):
        frozen_char_ids["build"] = char_id
        return [], {"pending_paths": []}

    async def _spy_post(uid, content, reply, *args, **kwargs):
        fs = kwargs.get("frozen_scope")
        frozen_char_ids["post"] = fs.character_id if fs else None
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake = _make_pipeline(char_id="char_a")
    fake.fetch_context = _spy_fetch
    fake.build_prompt = _spy_build
    fake.post_process = _spy_post
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main.handle_message({
        "user_id": _OWNER_ID,
        "content": "测试消息",
        "sender_name": _OWNER_ID,
    })

    assert frozen_char_ids.get("fetch") == "char_a", (
        f"fetch_context used wrong char_id: {frozen_char_ids.get('fetch')!r}"
    )
    assert frozen_char_ids.get("build") == "char_a", (
        f"build_prompt used wrong char_id: {frozen_char_ids.get('build')!r}"
    )
    assert frozen_char_ids.get("post") == "char_a", (
        f"post_process used wrong char_id: {frozen_char_ids.get('post')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. R1: handle_message acquires conversation_lock; post_process is awaited
# ═══════════════════════════════════════════════════════════════════════════════

async def test_r1_conversation_lock_acquired(sandbox, monkeypatch):
    """
    handle_message 内部的 pipeline 步骤在 conversation_lock 持有期间运行。
    通过在锁持有时检查 post_process 是否在同一锁区内被 await 来验证。
    """
    _write_reality_state(_OWNER_ID)
    _patch_noise(monkeypatch)
    _patch_memory(monkeypatch)
    _patch_output(monkeypatch)
    _patch_probe(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    lock_held_during_post = []
    import core.conversation_gate as _cg
    original_lock = _cg.conversation_lock

    def _spy_lock(uid):
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def _ctx():
            _spy_lock.active = True
            try:
                yield
            finally:
                _spy_lock.active = False
        return _ctx()
    _spy_lock.active = False
    monkeypatch.setattr(_cg, "conversation_lock", _spy_lock)

    import main as _main
    fake = _make_pipeline(char_id="char_a")

    async def _spy_post(uid, content, reply, *args, **kwargs):
        lock_held_during_post.append(_spy_lock.active)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake.post_process = _spy_post
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main.handle_message({
        "user_id": _OWNER_ID,
        "content": "你好",
        "sender_name": _OWNER_ID,
    })

    assert lock_held_during_post, "post_process was never called"
    assert all(lock_held_during_post), (
        "post_process was called outside conversation_lock"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. N10: post_process 异常被捕获并可观测（不静默丢失）
# ═══════════════════════════════════════════════════════════════════════════════

async def test_n10_post_process_exception_observable(sandbox, monkeypatch):
    """
    post_process 抛出异常时、handle_message 不崩溃，且异常经由 log_error 被记录
    （不被 asyncio.create_task 静默吞掉）。

    R1-D 更新：post_process 现在经由 record_assistant_turn（turn_sink）调用，
    异常可能以 "qq_reality_reply_adapter.turn_sink" tag 记录，而非 "post_process.*"。
    合约：异常必须被 log_error 捕获，不允许静默丢弃。
    """
    _write_reality_state(_OWNER_ID)
    _patch_noise(monkeypatch)
    _patch_memory(monkeypatch)
    _patch_output(monkeypatch)
    _patch_probe(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    logged_errors = []
    import core.error_handler as _eh
    monkeypatch.setattr(_eh, "log_error", lambda tag, err: logged_errors.append((tag, err)))

    import main as _main
    fake = _make_pipeline(char_id="char_a")
    fake.post_process = AsyncMock(side_effect=RuntimeError("写入失败"))
    monkeypatch.setattr(_main, "_pipeline", fake)

    # Should not raise despite post_process failing
    await _main.handle_message({
        "user_id": _OWNER_ID,
        "content": "你好",
        "sender_name": _OWNER_ID,
    })

    # Exception must have been captured by log_error — not silently dropped.
    # After R1-D: post_process is called inside record_assistant_turn (turn_sink),
    # so the exception may be logged as "qq_reality_reply_adapter.turn_sink" instead of
    # a "post_process.*" tag.  Accept either — the contract is non-silent capture.
    captured = [
        (tag, err) for tag, err in logged_errors
        if "post_process" in tag or "turn_sink" in tag or "qq_reality_reply_adapter" in tag
    ]
    assert captured, (
        f"post_process exception was not logged — silent drop detected. "
        f"logged_errors={logged_errors}"
    )
    assert isinstance(captured[0][1], RuntimeError)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. _reply_with_tool_result 传递 frozen_scope/char_id 给 post_process
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reply_with_tool_result_passes_frozen_scope(sandbox, monkeypatch):
    """
    _reply_with_tool_result(frozen_scope=...) 把 scope 透传给 post_process，
    而不是让 post_process 内部重新读取 active_prompt_assets.json（N1）。
    """
    import main as _main

    scope = _make_scope(_OWNER_ID, "char_frozen")

    import core.memory.short_term as _st
    import core.memory.user_profile as _up
    import core.memory.group_context as _gc
    import core.user_relation as _ur
    monkeypatch.setattr(_st, "load_for_prompt", lambda uid, **kw: [])
    monkeypatch.setattr(_up, "load", lambda uid, **kw: {})
    monkeypatch.setattr(_gc, "get_recent", lambda gid: [])
    monkeypatch.setattr(_ur, "get_relation", lambda uid: {})
    _patch_output(monkeypatch)

    import core.response_processor as _rp
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    captured = {}

    async def _spy_post(uid, content, reply, *args, **kwargs):
        captured["frozen_scope"] = kwargs.get("frozen_scope")
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake = _make_pipeline(char_id="char_frozen")
    fake.post_process = _spy_post
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main._reply_with_tool_result(
        "tool_data", _OWNER_ID, _OWNER_ID, False, frozen_scope=scope
    )

    assert captured.get("frozen_scope") is scope, (
        "_reply_with_tool_result did not forward frozen_scope to post_process"
    )
    assert captured["frozen_scope"].character_id == "char_frozen"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. QQ 发送前 reality output scrub 剥除动作行（记忆路径不含 *动作*）
# ═══════════════════════════════════════════════════════════════════════════════

async def test_memory_path_scrubbed_before_post_process(sandbox, monkeypatch):
    """
    handle_message 在把 reply 传给 post_process 之前，经过 scrub_reality_output_text
    剥除动作行，保证 short_term / event_log 只写对话文本。
    """
    _write_reality_state(_OWNER_ID)
    _patch_noise(monkeypatch)
    _patch_memory(monkeypatch)
    _patch_output(monkeypatch)
    _patch_probe(monkeypatch)

    import core.response_processor as _rp
    # LLM returns action + dialogue mixed
    monkeypatch.setattr(_rp, "process", lambda reply, name: [reply] if reply else [])

    import main as _main
    fake = _make_pipeline(char_id="char_a", llm_reply="*她微微低头*\n好的，我明白了。")
    captured_reply = []

    async def _spy_post(uid, content, reply, *args, **kwargs):
        captured_reply.append(reply)
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    fake.post_process = _spy_post
    monkeypatch.setattr(_main, "_pipeline", fake)

    await _main.handle_message({
        "user_id": _OWNER_ID,
        "content": "你好",
        "sender_name": _OWNER_ID,
    })

    assert captured_reply, "post_process was not called"
    mem = captured_reply[0]
    assert "*她微微低头*" not in mem, (
        f"Action line leaked into memory reply: {mem!r}"
    )
    assert "好的，我明白了。" in mem, (
        f"Dialogue text missing from memory reply: {mem!r}"
    )
