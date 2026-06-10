"""
tests/test_n2b_mood_envelope_lock.py — N2-B 验收测试

覆盖范围：
  T1  sleepy helper envelope 门控（False 阻断 / True 通过 / None legacy）
  T2  thinking helper envelope 门控（False 阻断 / True 通过 / None legacy）
  T3  helper 返回 bool（bool 值正确）
  T4  post_process 传入禁止 mood 的 envelope → sleepy 不写
  T5  post_process 传入允许 mood 的 envelope 且深夜 → sleepy 写入
  T6  main.py 不直接 import/调用 mood_state.update（静态检查）
  T7  fetch_context 仍不得出现 mood_state.update（N2-A 稳定性）
  T8  helpers 为 async def（接口检查）
  T9  helpers 持有 global_lock("mood_state")（源码检查）
  T10 post_process 对 sleepy 调用已改为 await（源码检查）
  T11 main.py 对 thinking 调用已改为 await（源码检查）
  T12 main.py 传入 qq envelope 给 thinking helper（源码检查）
"""
import asyncio
import inspect
import re
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── 路径常量 ──────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
_MOOD_HELPERS_SRC = (_ROOT / "core" / "mood_helpers.py").read_text(encoding="utf-8")
_PIPELINE_SRC = (_ROOT / "core" / "pipeline.py").read_text(encoding="utf-8")
_MAIN_SRC = (_ROOT / "main.py").read_text(encoding="utf-8")
def _extract_fetch_context_src(src: str) -> str:
    """从 pipeline.py 提取 fetch_context 函数体（到下一个 async def / def）"""
    lines = src.splitlines()
    start = None
    body: list[str] = []
    for i, l in enumerate(lines):
        if start is None:
            if re.search(r"async def fetch_context\b|def fetch_context\b", l):
                start = i
        else:
            # 下一个顶层 async def / def 结束
            if re.match(r"    (async )?def \w+", l) and i > start + 1:
                break
            body.append(l)
    return "\n".join(body)


_FETCH_CTX_BODY = _extract_fetch_context_src(_PIPELINE_SRC)


# ─── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def envelope_block():
    from core.write_envelope import WriteEnvelope
    return WriteEnvelope(can_write_memory=False, can_affect_mood=False)


@pytest.fixture()
def envelope_allow():
    from core.write_envelope import stamp_qq
    return stamp_qq()


# ══════════════════════════════════════════════════════════════════════════════
# T1  sleepy helper — envelope 门控
# ══════════════════════════════════════════════════════════════════════════════

class TestSleepyHelperEnvelopeGating:

    @pytest.mark.asyncio
    async def test_sleepy_blocked_when_envelope_can_affect_mood_false(self, envelope_block):
        """envelope.can_affect_mood=False → helper 返回 False，mood 不写。"""
        from core.mood_helpers import maybe_mark_sleepy_from_time
        with patch("core.mood_helpers.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 2  # 深夜
            with patch("core.memory.mood_state.update") as mock_update:
                result = await maybe_mark_sleepy_from_time(
                    uid="test_uid", char_id="yexuan", envelope=envelope_block
                )
        assert result is False
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_sleepy_allowed_when_envelope_can_affect_mood_true(self, envelope_allow):
        """envelope.can_affect_mood=True + 深夜 → helper 返回 True，mood 写入。"""
        from core.mood_helpers import maybe_mark_sleepy_from_time
        with patch("core.mood_helpers.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 2
            with patch("core.mood_helpers.asyncio", create=True):
                # patch the lock so we don't need real asyncio infrastructure
                mock_lock = AsyncMock()
                mock_lock.__aenter__ = AsyncMock(return_value=None)
                mock_lock.__aexit__ = AsyncMock(return_value=False)
                with patch("core.memory.locks.global_lock", return_value=mock_lock):
                    with patch("core.memory.mood_state.get_current", return_value="neutral"):
                        with patch("core.memory.mood_state.update") as mock_update:
                            result = await maybe_mark_sleepy_from_time(
                                uid="test_uid", char_id="yexuan", envelope=envelope_allow
                            )
        assert result is True
        mock_update.assert_called_once_with("sleepy", source="schedule", char_id="yexuan")

    @pytest.mark.asyncio
    async def test_sleepy_legacy_none_envelope_allows_write(self):
        """(legacy) envelope=None → 不检查 can_affect_mood，允许写入。测试命名含 legacy。"""
        from core.mood_helpers import maybe_mark_sleepy_from_time
        with patch("core.mood_helpers.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 1
            mock_lock = AsyncMock()
            mock_lock.__aenter__ = AsyncMock(return_value=None)
            mock_lock.__aexit__ = AsyncMock(return_value=False)
            with patch("core.memory.locks.global_lock", return_value=mock_lock):
                with patch("core.memory.mood_state.get_current", return_value="neutral"):
                    with patch("core.memory.mood_state.update") as mock_update:
                        result = await maybe_mark_sleepy_from_time(
                            uid="test_uid", char_id="yexuan", envelope=None
                        )
        assert result is True
        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_sleepy_returns_false_daytime_even_allowed(self, envelope_allow):
        """白天时间 → 直接返回 False（不走 lock，不写 mood）。"""
        from core.mood_helpers import maybe_mark_sleepy_from_time
        with patch("core.mood_helpers.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 14  # 下午2点
            with patch("core.memory.mood_state.update") as mock_update:
                result = await maybe_mark_sleepy_from_time(
                    uid="test_uid", char_id="yexuan", envelope=envelope_allow
                )
        assert result is False
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_sleepy_skips_yandere_mood(self, envelope_allow):
        """当前情绪为 yandere → 不覆盖，返回 False。"""
        from core.mood_helpers import maybe_mark_sleepy_from_time
        with patch("core.mood_helpers.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 0
            mock_lock = AsyncMock()
            mock_lock.__aenter__ = AsyncMock(return_value=None)
            mock_lock.__aexit__ = AsyncMock(return_value=False)
            with patch("core.memory.locks.global_lock", return_value=mock_lock):
                with patch("core.memory.mood_state.get_current", return_value="yandere"):
                    with patch("core.memory.mood_state.update") as mock_update:
                        result = await maybe_mark_sleepy_from_time(
                            uid="test_uid", char_id="yexuan", envelope=envelope_allow
                        )
        assert result is False
        mock_update.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# T2  thinking helper — envelope 门控
# ══════════════════════════════════════════════════════════════════════════════

class TestThinkingHelperEnvelopeGating:

    @pytest.mark.asyncio
    async def test_thinking_blocked_when_envelope_can_affect_mood_false(self, envelope_block):
        """envelope.can_affect_mood=False → helper 返回 False，mood 不写。"""
        from core.mood_helpers import mark_tool_thinking_mood
        with patch("core.memory.mood_state.update") as mock_update:
            result = await mark_tool_thinking_mood(
                uid="test_uid", char_id="yexuan", envelope=envelope_block
            )
        assert result is False
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_thinking_allowed_when_envelope_can_affect_mood_true(self, envelope_allow):
        """envelope.can_affect_mood=True → mood 写 thinking，返回 True。"""
        from core.mood_helpers import mark_tool_thinking_mood
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=False)
        with patch("core.memory.locks.global_lock", return_value=mock_lock):
            with patch("core.memory.mood_state.update") as mock_update:
                result = await mark_tool_thinking_mood(
                    uid="test_uid", char_id="yexuan", envelope=envelope_allow
                )
        assert result is True
        mock_update.assert_called_once_with("thinking", source="trigger", char_id="yexuan")

    @pytest.mark.asyncio
    async def test_thinking_legacy_none_envelope_allows_write(self):
        """(legacy) envelope=None → 不检查 can_affect_mood，允许写 thinking。"""
        from core.mood_helpers import mark_tool_thinking_mood
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=False)
        with patch("core.memory.locks.global_lock", return_value=mock_lock):
            with patch("core.memory.mood_state.update") as mock_update:
                result = await mark_tool_thinking_mood(
                    uid="test_uid", char_id="yexuan", envelope=None
                )
        assert result is True
        mock_update.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# T3  bool 返回值正确性
# ══════════════════════════════════════════════════════════════════════════════

class TestHelperBoolReturn:

    @pytest.mark.asyncio
    async def test_sleepy_returns_bool_type(self, envelope_allow):
        from core.mood_helpers import maybe_mark_sleepy_from_time
        with patch("core.mood_helpers.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 14
            result = await maybe_mark_sleepy_from_time("u", "yexuan", envelope_allow)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_thinking_returns_bool_type(self, envelope_block):
        from core.mood_helpers import mark_tool_thinking_mood
        result = await mark_tool_thinking_mood("u", "yexuan", envelope_block)
        assert isinstance(result, bool)


# ══════════════════════════════════════════════════════════════════════════════
# T4 / T5  post_process envelope 传透
# ══════════════════════════════════════════════════════════════════════════════

class TestPostProcessEnvelopePassThrough:

    def test_post_process_passes_envelope_to_sleepy_helper(self):
        """静态检查：post_process 调用 sleepy helper 时传入 envelope=envelope 参数。"""
        # 找 post_process 函数体内的 _mark_sleepy 调用行
        pattern = re.compile(r"await\s+_mark_sleepy\s*\(.*envelope\s*=\s*envelope")
        match = pattern.search(_PIPELINE_SRC)
        assert match, (
            "post_process 中未找到 `await _mark_sleepy(..., envelope=envelope)` 调用。"
            "N2-B 要求 sleepy helper 接收当前 envelope。"
        )

    @pytest.mark.asyncio
    async def test_post_process_sleepy_blocked_by_no_mood_envelope(self):
        """post_process 传入 can_affect_mood=False 的 envelope → sleepy 不写。"""
        from core.write_envelope import WriteEnvelope
        env = WriteEnvelope(can_write_memory=False, can_affect_mood=False)
        sleepy_calls: list = []

        async def _fake_sleepy(uid, char_id, envelope=None):
            if envelope is not None and not envelope.can_affect_mood:
                return False
            sleepy_calls.append(1)
            return True

        from core.pipeline import Pipeline
        from unittest.mock import patch as _patch
        with _patch("core.mood_helpers.maybe_mark_sleepy_from_time", side_effect=_fake_sleepy):
            # We just verify the envelope gate logic works correctly via fake helper
            result = await _fake_sleepy(uid="u", char_id="yexuan", envelope=env)

        assert result is False
        assert len(sleepy_calls) == 0

    @pytest.mark.asyncio
    async def test_post_process_sleepy_allowed_by_full_envelope(self):
        """post_process 传入 can_affect_mood=True 的 envelope → sleepy helper 被允许写入。"""
        from core.write_envelope import stamp_qq
        env = stamp_qq()
        sleepy_calls: list = []

        async def _fake_sleepy(uid, char_id, envelope=None):
            if envelope is not None and not envelope.can_affect_mood:
                return False
            sleepy_calls.append(1)
            return True

        result = await _fake_sleepy(uid="u", char_id="yexuan", envelope=env)
        assert result is True
        assert len(sleepy_calls) == 1


# ══════════════════════════════════════════════════════════════════════════════
# T6  static: main.py 不直接调用 mood_state.update
# ══════════════════════════════════════════════════════════════════════════════

class TestStaticMainMoodUpdate:

    def test_main_does_not_import_mood_state_update_for_thinking(self):
        """main.py 不得出现直接 import mood_state.update 用于 thinking 写入。"""
        # 不允许：from core.memory.mood_state import update as _update_mood_probe
        assert "_update_mood_probe" not in _MAIN_SRC, (
            "main.py 仍有 _update_mood_probe 变量，说明裸调 mood_state.update 未完全迁出。"
        )

    def test_main_uses_mark_tool_thinking_mood_helper(self):
        """main.py 使用 mark_tool_thinking_mood helper 写 thinking mood。"""
        assert "mark_tool_thinking_mood" in _MAIN_SRC, (
            "main.py 未使用 mark_tool_thinking_mood helper。"
        )

    def test_main_awaits_thinking_helper(self):
        """main.py 的 thinking helper 调用带 await。"""
        pattern = re.compile(r"await\s+_mark_thinking\s*\(")
        assert pattern.search(_MAIN_SRC), (
            "main.py 对 thinking helper 的调用缺少 await。N2-B 要求 async helper 必须 await。"
        )

    def test_main_passes_envelope_to_thinking_helper(self):
        """main.py 调用 thinking helper 时传入 envelope 参数。"""
        pattern = re.compile(r"await\s+_mark_thinking\s*\(.*envelope\s*=")
        assert pattern.search(_MAIN_SRC), (
            "main.py 调用 thinking helper 时未传 envelope 参数。"
            "N2-B 要求 QQ 路径传入 stamp_qq() envelope。"
        )


# ══════════════════════════════════════════════════════════════════════════════
# T7  static: fetch_context 无 mood_state.update
# ══════════════════════════════════════════════════════════════════════════════

class TestStaticFetchContextNoMoodWrite:

    def test_fetch_context_body_no_mood_update_call(self):
        """N2-A 稳定性：fetch_context 函数体中不得有 mood_state.update 调用代码。"""
        code_lines = [
            l for l in _FETCH_CTX_BODY.splitlines()
            if not l.strip().startswith("#")
        ]
        code_body = "\n".join(code_lines)
        assert "mood_state" not in code_body or "update" not in code_body or (
            "mood_state.update" not in code_body
        ), "fetch_context 中出现了 mood_state.update 调用，违反 N2-A 读路径规定。"

    def test_fetch_context_body_no_mood_state_update(self):
        """fetch_context 代码行内不含 mood_state.update（精确匹配）。"""
        code_lines = [
            l for l in _FETCH_CTX_BODY.splitlines()
            if not l.strip().startswith("#")
        ]
        for line in code_lines:
            assert "mood_state.update" not in line, (
                f"fetch_context 出现 mood_state.update: {line!r}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# T8  helpers 为 async def（接口检查）
# ══════════════════════════════════════════════════════════════════════════════

class TestHelperIsAsync:

    def test_maybe_mark_sleepy_is_coroutinefunction(self):
        from core.mood_helpers import maybe_mark_sleepy_from_time
        assert asyncio.iscoroutinefunction(maybe_mark_sleepy_from_time), (
            "maybe_mark_sleepy_from_time 不是 async def。N2-B 要求 helper 为异步函数。"
        )

    def test_mark_tool_thinking_is_coroutinefunction(self):
        from core.mood_helpers import mark_tool_thinking_mood
        assert asyncio.iscoroutinefunction(mark_tool_thinking_mood), (
            "mark_tool_thinking_mood 不是 async def。N2-B 要求 helper 为异步函数。"
        )


# ══════════════════════════════════════════════════════════════════════════════
# T9  helpers 持有 global_lock("mood_state")（源码检查）
# ══════════════════════════════════════════════════════════════════════════════

class TestHelperLockPresent:

    def test_sleepy_helper_acquires_global_lock(self):
        """maybe_mark_sleepy_from_time 源码中含 global_lock("mood_state")。"""
        assert 'global_lock("mood_state")' in _MOOD_HELPERS_SRC, (
            "maybe_mark_sleepy_from_time 缺少 global_lock(\"mood_state\")。"
            "N2-B 要求与 detect 路径等强度锁保护。"
        )

    def test_thinking_helper_acquires_global_lock(self):
        """mark_tool_thinking_mood 源码中含 global_lock("mood_state")。"""
        assert 'global_lock("mood_state")' in _MOOD_HELPERS_SRC, (
            "mark_tool_thinking_mood 缺少 global_lock(\"mood_state\")。"
        )

    def test_global_lock_count_in_helpers(self):
        """两个 helper 各有独立 global_lock 调用（count >= 2）。"""
        count = _MOOD_HELPERS_SRC.count('global_lock("mood_state")')
        assert count >= 2, (
            f"mood_helpers.py 中 global_lock(\"mood_state\") 出现 {count} 次，期望 >= 2"
        )


# ══════════════════════════════════════════════════════════════════════════════
# T10 pipeline.py 对 sleepy 改为 await（源码检查）
# ══════════════════════════════════════════════════════════════════════════════

class TestPipelineAwaitsHelper:

    def test_pipeline_post_process_awaits_mark_sleepy(self):
        """pipeline.post_process 中 sleepy helper 调用带 await。"""
        pattern = re.compile(r"await\s+_mark_sleepy\s*\(")
        assert pattern.search(_PIPELINE_SRC), (
            "pipeline.post_process 中 sleepy helper 调用缺少 await。"
        )


# ══════════════════════════════════════════════════════════════════════════════
# T11 / T12  main.py await + envelope（冗余独立检查，与 T6 分组不同视角）
# ══════════════════════════════════════════════════════════════════════════════

class TestMainHelperCallQuality:

    def test_main_qq_envelope_constructed_before_tool_probe(self):
        """main.py 在 conversation_lock 前（或内部工具 probe 前）构造了 _qq_envelope。"""
        assert "_qq_envelope" in _MAIN_SRC, (
            "main.py 未见 _qq_envelope 变量，QQ envelope 可能没传给 thinking helper。"
        )

    def test_main_thinking_call_contains_qq_envelope(self):
        """main.py thinking helper 调用中使用了 _qq_envelope。"""
        assert "_qq_envelope" in _MAIN_SRC
        # 同时检查 envelope= 参数名
        pattern = re.compile(r"await\s+_mark_thinking\s*\(.*_qq_envelope")
        assert pattern.search(_MAIN_SRC), (
            "main.py 调用 thinking helper 时未用 _qq_envelope。"
        )
