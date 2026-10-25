"""
tests/test_write_envelope.py — WriteEnvelope v0 单元测试

覆盖验收标准中要求的全部场景：
  1. fail-closed  — 未 stamp → capture_turn 不写 short_term / event_log
  2. owner chat   — stamp_user_chat() → 正常写入
  3. QQ           — stamp_qq() → 正常写入
  4. QQ + is_test — is_test=True 强制关闭 → 不写
  5. sensor 原始事件 — stamp_sensor_watch() → profile 不写
  6. sensor assistant turn — stamp_sensor() → 记忆仍写
  7. mood gate    — can_affect_mood=False → mood 不变化
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── 异步上下文管理器辅助 ──────────────────────────────────────────────────────

class _AsyncCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ── capture_turn 测试辅助 ─────────────────────────────────────────────────────
# capture_turn 内部通过 `from core.memory import short_term, event_log` 懒导入。
# 正确的 patch 方式：先 import 目标模块，再 patch.object 其上的 append 函数。

def _patch_capture_dependencies():
    """返回 (st_mock, el_mock, context_manager)。"""
    import core.memory.short_term as _st
    import core.memory.event_log  as _el

    st = MagicMock()
    st.append = MagicMock(return_value=True)
    el = MagicMock()
    el.append = MagicMock(return_value=True)

    ctx = patch.multiple(
        "",
        **{},   # placeholder — we do explicit patch.object below
    )
    return st, el


# ═══════════════════════════════════════════════════════════════════════════════
# 1. WriteEnvelope 零值 — fail-closed
# ═══════════════════════════════════════════════════════════════════════════════

class TestWriteEnvelopeDefaults:
    def test_zero_value_is_fail_closed(self):
        from core.write_envelope import WriteEnvelope
        env = WriteEnvelope()
        assert env.can_write_memory is False
        assert env.can_affect_mood is False

    def test_is_test_forces_closed(self):
        from core.write_envelope import WriteEnvelope
        env = WriteEnvelope(can_write_memory=True, can_affect_mood=True, is_test=True)
        assert env.can_write_memory is False
        assert env.can_affect_mood is False

    def test_is_debug_forces_closed(self):
        from core.write_envelope import WriteEnvelope
        env = WriteEnvelope(can_write_memory=True, can_affect_mood=True, is_debug=True)
        assert env.can_write_memory is False
        assert env.can_affect_mood is False

    def test_stamp_test_returns_closed(self):
        from core.write_envelope import stamp_test
        env = stamp_test()
        assert env.can_write_memory is False
        assert env.can_affect_mood is False

    def test_stamp_debug_returns_closed(self):
        from core.write_envelope import stamp_debug
        env = stamp_debug()
        assert env.can_write_memory is False
        assert env.can_affect_mood is False


# ═══════════════════════════════════════════════════════════════════════════════
# 8. fail-closed — 未 stamp → capture_turn 不写
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailClosed:
    def test_unstamped_capture_turn_skips_writes(self):
        """WriteEnvelope() 零值 → short_term / event_log 均不写。"""
        import core.memory.short_term as _st
        import core.memory.event_log  as _el
        from core.memory.fixation_pipeline import capture_turn

        with patch.object(_st, 'append', MagicMock(return_value=True)) as st_mock, \
             patch.object(_el, 'append', MagicMock(return_value=True)) as el_mock:
            turn_id = capture_turn(uid="u_closed", user_msg="hello", reply="ok")
            # envelope 默认 WriteEnvelope() → fail-closed

        assert turn_id is not None, "turn_id 应始终返回，即使写入被阻断"
        assert not st_mock.called, "short_term 不应写入"
        assert not el_mock.called, "event_log 不应写入"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. owner chat stamp — 正常写入
# ═══════════════════════════════════════════════════════════════════════════════

class TestStampUserChat:
    def test_stamp_user_chat_allows_write(self):
        from core.write_envelope import stamp_user_chat
        env = stamp_user_chat()
        assert env.can_write_memory is True
        assert env.can_affect_mood is True

    def test_capture_turn_writes_with_user_chat_stamp(self):
        import core.memory.short_term as _st
        import core.memory.event_log  as _el
        from core.write_envelope import stamp_user_chat
        from core.memory.fixation_pipeline import capture_turn

        with patch.object(_st, 'append', MagicMock(return_value=True)) as st_mock, \
             patch.object(_el, 'append', MagicMock(return_value=True)) as el_mock:
            turn_id = capture_turn(
                uid="u_chat",
                user_msg="你好",
                reply="在的",
                envelope=stamp_user_chat(),
            )

        assert turn_id is not None
        assert st_mock.called,  "short_term.append 应被调用"
        assert el_mock.called,  "event_log.append 应被调用"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. QQ stamp — 仍然写入
# ═══════════════════════════════════════════════════════════════════════════════

class TestStampQQ:
    def test_stamp_qq_allows_write(self):
        from core.write_envelope import stamp_qq
        env = stamp_qq()
        assert env.can_write_memory is True
        assert env.can_affect_mood is True

    def test_capture_turn_writes_with_qq_stamp(self):
        import core.memory.short_term as _st
        import core.memory.event_log  as _el
        from core.write_envelope import stamp_qq
        from core.memory.fixation_pipeline import capture_turn

        with patch.object(_st, 'append', MagicMock(return_value=True)) as st_mock, \
             patch.object(_el, 'append', MagicMock(return_value=True)) as el_mock:
            turn_id = capture_turn(
                uid="u_qq",
                user_msg="test",
                reply="ok",
                envelope=stamp_qq(),
            )

        assert turn_id is not None
        assert st_mock.called
        assert el_mock.called


# ═══════════════════════════════════════════════════════════════════════════════
# 4. QQ + is_test — 不写
# ═══════════════════════════════════════════════════════════════════════════════

class TestQQWithIsTest:
    def test_qq_is_test_forces_closed(self):
        from core.write_envelope import WriteEnvelope, SourceType
        # stamp_qq() 的字段 + is_test=True
        env = WriteEnvelope(
            source=SourceType.QQ,
            can_write_memory=True,
            can_affect_mood=True,
            is_test=True,
        )
        assert env.can_write_memory is False
        assert env.can_affect_mood is False

    def test_capture_turn_skips_with_is_test(self):
        import core.memory.short_term as _st
        import core.memory.event_log  as _el
        from core.write_envelope import WriteEnvelope, SourceType
        from core.memory.fixation_pipeline import capture_turn

        env = WriteEnvelope(
            source=SourceType.QQ,
            can_write_memory=True,
            can_affect_mood=True,
            is_test=True,
        )

        with patch.object(_st, 'append', MagicMock(return_value=True)) as st_mock, \
             patch.object(_el, 'append', MagicMock(return_value=True)) as el_mock:
            capture_turn(uid="u_test", user_msg="hi", reply="ok", envelope=env)

        assert not st_mock.called, "is_test=True → short_term 不应写"
        assert not el_mock.called, "is_test=True → event_log 不应写"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. sensor 原始事件 — profile 不写，objective health state 正常写入
# ═══════════════════════════════════════════════════════════════════════════════

class TestSensorWatchRaw:
    def test_stamp_sensor_watch_blocks_write(self):
        from core.write_envelope import stamp_sensor_watch
        env = stamp_sensor_watch()
        assert env.can_write_memory is False
        assert env.can_affect_mood is False

    def test_sensor_router_writes_health_not_profile(self, sandbox, monkeypatch):
        """Raw sensor data persists only in the uid-global health state."""
        import admin.routers.sensor as _sensor_module
        from core.memory import health_state, user_profile

        monkeypatch.setattr(
            _sensor_module, "get_config",
            lambda: {"scheduler": {"owner_id": "owner1"}},
        )
        user_profile.save("owner1", {"name": "profile-only"})

        _sensor_module._save_sensor_to_health_state({"steps": 5000, "battery": 80})

        assert user_profile.load("owner1")["name"] == "profile-only"
        assert health_state.load("owner1")["phone_sensor_today"]["steps"] == 5000

    def test_watch_router_heart_rate_writes_health_not_profile(self, sandbox):
        """Raw heart-rate observations do not use the character profile."""
        import admin.routers.watch as _watch_module
        from core.memory import health_state, user_profile

        user_profile.save("owner1", {"name": "profile-only"})

        _watch_module._append_heart_rate_event("owner1", 110, triggered=True)

        assert user_profile.load("owner1")["name"] == "profile-only"
        assert health_state.load("owner1")["heart_rate_events"][-1]["value"] == 110


# ═══════════════════════════════════════════════════════════════════════════════
# 6. sensor assistant turn — 记忆仍写
# ═══════════════════════════════════════════════════════════════════════════════

class TestSensorAssistantTurn:
    def test_stamp_sensor_allows_write(self):
        from core.write_envelope import stamp_sensor
        env = stamp_sensor()
        assert env.can_write_memory is True
        assert env.can_affect_mood is True

    def test_capture_turn_writes_with_sensor_stamp(self):
        import core.memory.short_term as _st
        import core.memory.event_log  as _el
        from core.write_envelope import stamp_sensor
        from core.memory.fixation_pipeline import capture_turn

        with patch.object(_st, 'append', MagicMock(return_value=True)) as st_mock, \
             patch.object(_el, 'append', MagicMock(return_value=True)) as el_mock:
            turn_id = capture_turn(
                uid="u_sensor",
                user_msg="",
                reply="你心率有点高",
                trigger_name="hr_high",
                envelope=stamp_sensor(),
            )

        assert turn_id is not None
        # trigger_name 非空 → 只写 assistant 行，不写 user 行
        assert el_mock.called, "event_log.append 应被调用（sensor assistant turn）"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. mood gate — can_affect_mood=False → mood 不变化
# ═══════════════════════════════════════════════════════════════════════════════

class TestMoodGate:
    @pytest.mark.asyncio
    async def test_mood_not_updated_when_can_affect_mood_false(self, monkeypatch):
        """post_process 中 can_affect_mood=False 时 mood_state.update 不被调用。"""
        import core.memory.locks as _locks_mod
        import core.memory.short_term as _st_mod
        import core.memory.fixation_pipeline as _fp_mod
        import core.memory.mood_state as _ms_mod
        import core.post_process.slow_queue as _sq_mod
        import core.config_loader as _cfg_mod
        import core.tag_rules as _tr_mod
        import core.llm_client as _llm_mod

        mood_calls = []

        monkeypatch.setattr(_locks_mod, "uid_lock",   lambda uid: _AsyncCtx())
        monkeypatch.setattr(_locks_mod, "global_lock", lambda name: _AsyncCtx())
        monkeypatch.setattr(_st_mod,   "load",        lambda uid: [])
        monkeypatch.setattr(_fp_mod,   "capture_turn", MagicMock(return_value="tid1"))
        monkeypatch.setattr(_ms_mod,   "update",      lambda *a, **kw: mood_calls.append(a))
        monkeypatch.setattr(_sq_mod,   "enqueue",     MagicMock())
        monkeypatch.setattr(_cfg_mod,  "get_config",  lambda: {"memory": {"summary_every_n_rounds": 20}})
        monkeypatch.setattr(_tr_mod,   "get_tags",    MagicMock(return_value=set()))

        from core.write_envelope import WriteEnvelope
        from core.pipeline import Pipeline

        class _FakeChar:
            name = "Companion"

        p = Pipeline(_FakeChar(), lore_engine=None)
        env = WriteEnvelope(can_write_memory=True, can_affect_mood=False)
        await p.post_process("u1", "你好", "在呢", envelope=env)

        assert mood_calls == [], "can_affect_mood=False 时 mood_state.update 不应被调用"

    @pytest.mark.asyncio
    async def test_mood_updated_when_can_affect_mood_true(self, monkeypatch):
        """can_affect_mood=True 时 mood_state.update 应被调用。"""
        import core.memory.locks as _locks_mod
        import core.memory.short_term as _st_mod
        import core.memory.fixation_pipeline as _fp_mod
        import core.memory.mood_state as _ms_mod
        import core.post_process.slow_queue as _sq_mod
        import core.config_loader as _cfg_mod
        import core.tag_rules as _tr_mod
        import core.llm_client as _llm_mod
        import core.user_relation as _ur_mod

        mood_calls = []

        monkeypatch.setattr(_locks_mod, "uid_lock",   lambda uid: _AsyncCtx())
        monkeypatch.setattr(_locks_mod, "global_lock", lambda name: _AsyncCtx())
        monkeypatch.setattr(_st_mod,   "load",        lambda uid: [])
        monkeypatch.setattr(_fp_mod,   "capture_turn", MagicMock(return_value="tid2"))
        monkeypatch.setattr(_ms_mod,   "update",      lambda *a, **kw: mood_calls.append(a))
        monkeypatch.setattr(_sq_mod,   "enqueue",     MagicMock())
        monkeypatch.setattr(_cfg_mod,  "get_config",  lambda: {"memory": {"summary_every_n_rounds": 20}})
        monkeypatch.setattr(_tr_mod,   "get_tags",    MagicMock(return_value=set()))
        monkeypatch.setattr(_ur_mod,   "get_relation", MagicMock(return_value={"priority": 1}))

        from core.write_envelope import stamp_user_chat
        from core.pipeline import Pipeline

        class _FakeChar:
            name = "Companion"

        p = Pipeline(_FakeChar(), lore_engine=None)
        await p.post_process("u1", "你好", "在呢", envelope=stamp_user_chat())

        assert len(mood_calls) >= 1, "can_affect_mood=True 时 mood_state.update 应被调用"
