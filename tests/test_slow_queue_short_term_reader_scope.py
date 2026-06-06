"""
tests/test_slow_queue_short_term_reader_scope.py

P1-0C.7: slow_queue handler 内部 short_term reader 默认 yexuan 风险审计

审计结论（所有测试基于此）：
  - 所有 8 个 handler 在调用 short_term 时均已正确透传 char_id
  - 没有任何 handler 在 handler 内部直接调用 short_term.load / get_history / load_for_prompt
  - capture_turn_retry 只写 short_term（不读）
  - user_profile_update 的 recent history 通过 payload 传入（已在 post_process 上游用正确 char_id 读取）

Covers:
1. user_profile_update handler 不在 handler 内部调用 short_term.load
2. consolidate_to_identity handler 不在 handler 内部调用 short_term.load
3. summarize_to_midterm handler 不在 handler 内部调用 short_term.load
4. reflect_to_episodic handler 不在 handler 内部调用 short_term.load
5. capture_turn_retry handler 对 short_term 只写不读
6. hongcha payload → short_term.load 从不被调用（无短路到 yexuan 默认桶）
7. 旧 payload 缺 char_id → WARN + fallback yexuan（DLQ 兼容层）
8. 旧 payload 缺 char_id fallback 时 short_term 仍不被读取
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ── 共用 payload 工厂 ─────────────────────────────────────────────────────────

def _mt_payload(uid: str, char_id: str) -> dict:
    return {
        "turn_id": f"turn_{uid}_{char_id}",
        "uid": uid,
        "user_content": "test_msg",
        "reply": "test_reply",
        "tags": [],
        "emotion": "neutral",
        "char_id": char_id,
    }


def _reflect_payload(uid: str, char_id: str) -> dict:
    return {
        "uid": uid,
        "mid_ids": ["mt_1", "mt_2"],
        "trigger": "eager",
        "char_id": char_id,
    }


def _consolidate_payload(uid: str, char_id: str) -> dict:
    return {"uid": uid, "char_id": char_id}


def _profile_payload(uid: str, char_id: str, recent: list) -> dict:
    return {"uid": uid, "recent": recent, "char_id": char_id}


def _capture_retry_payload(uid: str, char_id: str) -> dict:
    return {
        "turn_id": f"retry_{uid}",
        "uid": uid,
        "user_content": "retry_msg",
        "reply": "retry_reply",
        "emotion": "neutral",
        "trigger_name": "",
        "char_id": char_id,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: user_profile_update handler 不调用 short_term.load
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_user_profile_update_does_not_read_short_term(sandbox):
    """
    _handler_user_profile_update 使用 payload['recent']，
    不在 handler 内部调用 short_term.load/get_history/load_for_prompt。
    """
    from core.pipeline import _handler_user_profile_update
    import core.memory.short_term as _st

    uid = "uid_profile_test"
    recent_msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    payload = _profile_payload(uid, "hongcha", recent_msgs)

    st_load_called = []

    async def _fake_extract(u, msgs, *, char_id="yexuan"):
        pass  # no-op

    with (
        patch("core.memory.user_profile.extract_and_update", side_effect=_fake_extract),
        patch.object(_st, "load", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
        patch.object(_st, "get_history", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
        patch.object(_st, "load_for_prompt", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
    ):
        await _handler_user_profile_update(payload)

    assert st_load_called == [], (
        f"_handler_user_profile_update 不应调用 short_term 读取接口，"
        f"但调用了: {st_load_called}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: consolidate_to_identity handler 不调用 short_term.load
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_consolidate_to_identity_does_not_read_short_term(sandbox):
    """
    handler_consolidate_to_identity 读取 user_identity/episodic/user_profile，
    不读 short_term。
    """
    from core.memory.fixation_pipeline import handler_consolidate_to_identity
    import core.memory.short_term as _st

    uid = "uid_consolidate_test"
    payload = _consolidate_payload(uid, "hongcha")

    st_load_called = []

    async def _fake_consolidate(uid, llm_client, *, char_id="yexuan"):
        return True

    with (
        patch("core.memory.fixation_pipeline.consolidate_to_identity", side_effect=_fake_consolidate),
        patch.object(_st, "load", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
        patch.object(_st, "get_history", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
        patch.object(_st, "load_for_prompt", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
    ):
        await handler_consolidate_to_identity(payload)

    assert st_load_called == [], (
        f"handler_consolidate_to_identity 不应调用 short_term 读取接口，"
        f"但调用了: {st_load_called}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: summarize_to_midterm handler 不调用 short_term.load
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_summarize_to_midterm_does_not_read_short_term(sandbox):
    """
    handler_summarize_to_midterm 压缩 user_msg/reply（已在 payload 中），
    不从 short_term 读取历史。
    """
    from core.memory.fixation_pipeline import handler_summarize_to_midterm
    import core.memory.short_term as _st

    uid = "uid_summarize_test"
    payload = _mt_payload(uid, "hongcha")

    st_load_called = []

    async def _fake_summarize(**kwargs):
        return None

    with (
        patch("core.memory.fixation_pipeline.summarize_to_midterm", side_effect=_fake_summarize),
        patch.object(_st, "load", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
        patch.object(_st, "get_history", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
        patch.object(_st, "load_for_prompt", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
    ):
        await handler_summarize_to_midterm(payload)

    assert st_load_called == [], (
        f"handler_summarize_to_midterm 不应调用 short_term 读取接口，"
        f"但调用了: {st_load_called}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: reflect_to_episodic handler 不调用 short_term.load
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_reflect_to_episodic_does_not_read_short_term(sandbox):
    """
    handler_reflect_to_episodic 读取 mid_term 和 episodic，
    不读 short_term。
    """
    from core.memory.fixation_pipeline import handler_reflect_to_episodic
    import core.memory.short_term as _st

    uid = "uid_reflect_test"
    payload = _reflect_payload(uid, "hongcha")

    st_load_called = []

    async def _fake_reflect(**kwargs):
        return None

    with (
        patch("core.memory.fixation_pipeline.reflect_to_episodic", side_effect=_fake_reflect),
        patch.object(_st, "load", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
        patch.object(_st, "get_history", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
        patch.object(_st, "load_for_prompt", side_effect=lambda *a, **kw: st_load_called.append((a, kw)) or []),
    ):
        await handler_reflect_to_episodic(payload)

    assert st_load_called == [], (
        f"handler_reflect_to_episodic 不应调用 short_term 读取接口，"
        f"但调用了: {st_load_called}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5: capture_turn_retry 对 short_term 只写不读
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_capture_turn_retry_writes_not_reads_short_term(sandbox):
    """
    handler_capture_turn_retry 写 short_term（capture_turn），
    但不先读 short_term（不做条件判断读取）。
    """
    from core.memory.fixation_pipeline import handler_capture_turn_retry
    import core.memory.short_term as _st

    uid = "uid_retry_test"
    payload = _capture_retry_payload(uid, "hongcha")

    st_load_called = []
    st_append_called = []

    def _spy_load(*a, **kw):
        st_load_called.append((a, kw))
        return []

    def _spy_append(u, role, content, *, turn_id=None, char_id="yexuan"):
        st_append_called.append({"uid": u, "role": role, "char_id": char_id})
        return True

    with (
        patch.object(_st, "load", side_effect=_spy_load),
        patch.object(_st, "get_history", side_effect=_spy_load),
        patch.object(_st, "load_for_prompt", side_effect=_spy_load),
        patch.object(_st, "append", side_effect=_spy_append),
        patch("core.memory.event_log.append", return_value=True),
    ):
        await handler_capture_turn_retry(payload)

    assert st_load_called == [], (
        f"handler_capture_turn_retry 不应读 short_term，但调用了: {st_load_called}"
    )
    # 写入应带 char_id=hongcha
    for call_info in st_append_called:
        assert call_info["char_id"] == "hongcha", (
            f"capture_turn_retry 写入 short_term 应用 char_id='hongcha'，实际: {call_info}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 6: hongcha payload → yexuan short_term bucket 从不被读取
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hongcha_payload_never_reads_yexuan_short_term(sandbox):
    """
    payload char_id='hongcha' 时，任意 handler 不会以 char_id='yexuan'
    调用 short_term.load。
    """
    from core.memory.fixation_pipeline import (
        handler_summarize_to_midterm,
        handler_reflect_to_episodic,
        handler_consolidate_to_identity,
        handler_capture_turn_retry,
    )
    from core.pipeline import _handler_user_profile_update
    import core.memory.short_term as _st

    uid = "uid_hongcha_isolation"
    yexuan_reads = []

    original_load = _st.load

    def _spy_load(user_id, *args, char_id="yexuan", **kwargs):
        if char_id == "yexuan":
            yexuan_reads.append({"user_id": user_id, "caller": "load"})
        return []

    def _spy_get_history(user_id, *args, char_id="yexuan", **kwargs):
        if char_id == "yexuan":
            yexuan_reads.append({"user_id": user_id, "caller": "get_history"})
        return []

    def _spy_load_for_prompt(user_id, *args, char_id="yexuan", **kwargs):
        if char_id == "yexuan":
            yexuan_reads.append({"user_id": user_id, "caller": "load_for_prompt"})
        return []

    with (
        patch.object(_st, "load", side_effect=_spy_load),
        patch.object(_st, "get_history", side_effect=_spy_get_history),
        patch.object(_st, "load_for_prompt", side_effect=_spy_load_for_prompt),
        patch.object(_st, "append", return_value=True),
        patch("core.memory.event_log.append", return_value=True),
        patch("core.memory.fixation_pipeline.summarize_to_midterm",
              new=AsyncMock(return_value=None)),
        patch("core.memory.fixation_pipeline.reflect_to_episodic",
              new=AsyncMock(return_value=None)),
        patch("core.memory.fixation_pipeline.consolidate_to_identity",
              new=AsyncMock(return_value=True)),
        patch("core.memory.user_profile.extract_and_update", new=AsyncMock()),
    ):
        await handler_summarize_to_midterm(_mt_payload(uid, "hongcha"))
        await handler_reflect_to_episodic(_reflect_payload(uid, "hongcha"))
        await handler_consolidate_to_identity(_consolidate_payload(uid, "hongcha"))
        await handler_capture_turn_retry(_capture_retry_payload(uid, "hongcha"))
        await _handler_user_profile_update(_profile_payload(uid, "hongcha", []))

    assert yexuan_reads == [], (
        f"hongcha payload 下所有 handler 均不应以 char_id='yexuan' 读取 short_term，"
        f"但检测到: {yexuan_reads}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 7: 旧 payload 缺 char_id → WARN + fallback yexuan，不静默失败
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_legacy_dlq_payload_missing_char_id_warns_and_falls_back(caplog):
    """
    payload 缺少 char_id 时，_get_char_id_from_payload 发出 WARNING 并返回 'yexuan'。
    覆盖 fixation_pipeline 和 pipeline 两处 helper。
    """
    from core.memory.fixation_pipeline import _get_scope_from_payload as _fp_helper
    from core.pipeline import _get_scope_from_payload as _pl_helper

    with caplog.at_level(logging.WARNING):
        fp_scope = _fp_helper({"uid": "u1"}, "test_handler")
    assert fp_scope.character_id == "yexuan", f"fallback 应为 yexuan，实际: {fp_scope.character_id!r}"
    assert any("yexuan" in r.message for r in caplog.records), (
        "缺少 char_id 时应有 WARNING 日志，但未检测到"
    )

    caplog.clear()

    with caplog.at_level(logging.WARNING):
        pl_scope = _pl_helper({"uid": "u2"}, "test_handler2")
    assert pl_scope.character_id == "yexuan", f"pipeline fallback 应为 yexuan，实际: {pl_scope.character_id!r}"
    assert any("yexuan" in r.message for r in caplog.records), (
        "pipeline _get_scope_from_payload 缺少 scope/char_id 时应有 WARNING 日志"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 8: 旧 payload fallback yexuan 时，short_term 仍不被读取
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_legacy_payload_fallback_does_not_read_short_term(sandbox):
    """
    即使 payload 缺 char_id 触发 fallback yexuan，handler 自身也不读 short_term
    （fallback 仅控制写入桶，不引入额外读取）。
    """
    from core.memory.fixation_pipeline import handler_summarize_to_midterm
    import core.memory.short_term as _st

    uid = "uid_legacy_test"
    # 故意缺少 char_id
    payload_no_char = {
        "turn_id": f"turn_{uid}",
        "uid": uid,
        "user_content": "legacy_msg",
        "reply": "legacy_reply",
        "tags": [],
        "emotion": "neutral",
        # char_id 缺失
    }

    st_load_called = []

    with (
        patch("core.memory.fixation_pipeline.summarize_to_midterm", new=AsyncMock(return_value=None)),
        patch.object(_st, "load", side_effect=lambda *a, **kw: st_load_called.append(kw) or []),
        patch.object(_st, "get_history", side_effect=lambda *a, **kw: st_load_called.append(kw) or []),
        patch.object(_st, "load_for_prompt", side_effect=lambda *a, **kw: st_load_called.append(kw) or []),
    ):
        await handler_summarize_to_midterm(payload_no_char)

    assert st_load_called == [], (
        f"legacy payload fallback 不应触发 short_term 读取，但调用了: {st_load_called}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 9: _get_char_id_from_payload 有 char_id 时直接返回，不发 WARNING
# ═══════════════════════════════════════════════════════════════════════════════

def test_get_char_id_from_payload_returns_char_id_silently(caplog):
    """
    payload 含 char_id（无 scope）时 legacy fallback 直接返回 scope，不发任何 WARNING。
    """
    from core.memory.fixation_pipeline import _get_scope_from_payload as _fp_helper
    from core.pipeline import _get_scope_from_payload as _pl_helper

    with caplog.at_level(logging.WARNING):
        fp_scope = _fp_helper({"uid": "u1", "char_id": "hongcha"}, "test_handler")
    assert fp_scope.character_id == "hongcha"
    assert not caplog.records, f"char_id 存在时不应有 WARNING，但有: {caplog.records}"

    with caplog.at_level(logging.WARNING):
        pl_scope = _pl_helper({"uid": "u2", "char_id": "hongcha"}, "test_handler2")
    assert pl_scope.character_id == "hongcha"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 10: consolidate_to_identity 底层不调用 short_term（集成）
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_consolidate_to_identity_integration_no_short_term(sandbox):
    """
    consolidate_to_identity（非 mock 调用，只 mock LLM + storage）
    整个调用链中 short_term.load 均不被调用。
    """
    from core.memory.fixation_pipeline import consolidate_to_identity
    import core.memory.short_term as _st

    uid = "uid_consolidate_int"
    st_load_calls = []

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=None)

    with (
        patch.object(_st, "load", side_effect=lambda *a, **kw: st_load_calls.append((a, kw)) or []),
        patch.object(_st, "get_history", side_effect=lambda *a, **kw: st_load_calls.append((a, kw)) or []),
        patch.object(_st, "load_for_prompt", side_effect=lambda *a, **kw: st_load_calls.append((a, kw)) or []),
        # stub out storage layers
        patch("core.memory.user_identity.load", new=AsyncMock(return_value={})),
        patch("core.memory.episodic_memory.load_unconsolidated", return_value=[]),
        patch("core.memory.user_profile.load", return_value={}),
    ):
        result = await consolidate_to_identity(uid, mock_llm, char_id="hongcha")

    assert st_load_calls == [], (
        f"consolidate_to_identity 完整调用链不应读 short_term，但调用了: {st_load_calls}"
    )
    assert result is True
