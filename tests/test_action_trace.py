"""
tests/test_action_trace.py
===========================
Brief 27 · 工具动作痕迹层测试。

覆盖 cc-tasks/27-工具动作痕迹层.md §3 的测试清单（1-7；8 不适用，本任务未改 tag_rules.py）：
  1. execute() 成功/失败/pending_confirm/origin 拒绝 的落痕迹行为
  2. 环形上限 30 条
  3. trace_args 白名单
  4. 层 10.5 注入 / 不注入
  5. 当轮去重
  6. event_log_echo 开关
  7. peek_screen_content 敏感回归
"""
from __future__ import annotations

import importlib
import time

import pytest

from core.data_paths import DEFAULT_CHAR_ID


@pytest.fixture(autouse=True)
def _ensure_tool_registry_populated():
    """其它测试文件可能清空 _TOOL_REGISTRY 而不还原；确保本文件跑时 registry 完整。"""
    import core.tool_dispatcher as td
    if not td._TOOL_REGISTRY:
        importlib.reload(td)


def _controlled_config(overrides: dict | None = None) -> dict:
    """给 tool_dispatcher.get_config 用的最小可控配置：所有工具默认启用。"""
    cfg: dict = {"tools": {}, "scheduler": {"owner_id": ""}}
    if overrides:
        cfg.update(overrides)
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# 1. execute() 落痕迹行为
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteRecording:

    async def test_success_records_ok(self, sandbox, monkeypatch):
        import core.tool_dispatcher as td
        from core.session_state import SessionState
        from core.memory import action_trace

        monkeypatch.setattr(td, "get_config", lambda: _controlled_config())
        state = SessionState()
        result, ask = await td.execute(
            tool_name="get_time", tool_args={}, user_id="u_ok", target_id="u_ok",
            is_group=False, session_state=state, origin="user_live", char_id=DEFAULT_CHAR_ID,
        )
        assert result is not None and ask is None
        entries = action_trace.recent("u_ok", DEFAULT_CHAR_ID, max_items=10, window_hours=999)
        assert len(entries) == 1
        assert entries[0]["tool"] == "get_time"
        assert entries[0]["status"] == "ok"
        assert entries[0]["origin"] == "user_live"

    async def test_exception_records_failed(self, sandbox, monkeypatch):
        import core.tool_dispatcher as td
        from core.session_state import SessionState
        from core.memory import action_trace

        monkeypatch.setattr(td, "get_config", lambda: _controlled_config())

        async def _boom(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setitem(td._TOOL_REGISTRY["get_time"], "func", _boom)

        state = SessionState()
        await td.execute(
            tool_name="get_time", tool_args={}, user_id="u_fail", target_id="u_fail",
            is_group=False, session_state=state, origin="user_live", char_id=DEFAULT_CHAR_ID,
        )
        entries = action_trace.recent("u_fail", DEFAULT_CHAR_ID, max_items=10, window_hours=999)
        assert len(entries) == 1
        assert entries[0]["status"] == "failed"

    async def test_dangerous_confirm_records_pending(self, sandbox, monkeypatch):
        import core.tool_dispatcher as td
        from core.session_state import SessionState
        from core.memory import action_trace

        monkeypatch.setattr(td, "get_config", lambda: _controlled_config())
        monkeypatch.setattr(td, "_current_mode", lambda: "danger")
        monkeypatch.setattr("core.user_relation.has_permission", lambda *a, **k: True)

        state = SessionState()
        result, ask = await td.execute(
            tool_name="device_shutdown", tool_args={}, user_id="u_confirm", target_id="u_confirm",
            is_group=False, session_state=state, origin="user_live", char_id=DEFAULT_CHAR_ID,
        )
        assert result is None
        assert ask is not None
        assert state.status == SessionState.WAITING_CONFIRM
        entries = action_trace.recent("u_confirm", DEFAULT_CHAR_ID, max_items=10, window_hours=999)
        assert len(entries) == 1
        assert entries[0]["status"] == "pending_confirm"

    async def test_origin_rejected_not_recorded(self, sandbox, monkeypatch):
        import core.tool_dispatcher as td
        from core.session_state import SessionState
        from core.memory import action_trace

        monkeypatch.setattr(td, "get_config", lambda: _controlled_config())
        state = SessionState()
        result, ask = await td.execute(
            tool_name="get_time", tool_args={}, user_id="u_reject", target_id="u_reject",
            is_group=False, session_state=state, origin="not_a_real_origin", char_id=DEFAULT_CHAR_ID,
        )
        assert result is None and ask is None
        entries = action_trace.recent("u_reject", DEFAULT_CHAR_ID, max_items=10, window_hours=999)
        assert entries == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. 环形上限 30 条
# ─────────────────────────────────────────────────────────────────────────────

class TestRingBuffer:

    def test_caps_at_30_and_keeps_latest(self, sandbox):
        from core.memory import action_trace

        uid = "u_ring"
        for i in range(35):
            action_trace.record(
                uid, DEFAULT_CHAR_ID,
                tool="get_time", origin="user_live", status="ok",
                result_digest=f"n{i}",
            )
        entries = action_trace.recent(uid, DEFAULT_CHAR_ID, max_items=100, window_hours=999999)
        assert len(entries) == 30
        assert entries[0]["result_digest"] == "n5"
        assert entries[-1]["result_digest"] == "n34"


# ─────────────────────────────────────────────────────────────────────────────
# 3. trace_args 白名单
# ─────────────────────────────────────────────────────────────────────────────

class TestArgsDigestWhitelist:

    def test_undeclared_tool_returns_empty(self):
        from core.memory import action_trace
        # exit_yandere 未声明 trace_args
        digest = action_trace.build_args_digest("exit_yandere", {"secret": "should-not-appear"})
        assert digest == ""

    def test_declared_field_included(self):
        from core.memory import action_trace
        digest = action_trace.build_args_digest("web_search", {"query": "明天北京天气"})
        assert digest == "query=明天北京天气"

    def test_declared_field_capped_at_60_chars(self):
        from core.memory import action_trace
        digest = action_trace.build_args_digest("web_search", {"query": "x" * 100})
        assert len(digest) <= 61  # 60 + 省略号
        assert digest.endswith("…")

    def test_undeclared_field_not_leaked_even_with_whitelist(self):
        from core.memory import action_trace
        digest = action_trace.build_args_digest(
            "web_search", {"query": "天气", "internal_token": "secret-abc"},
        )
        assert "secret-abc" not in digest


# ─────────────────────────────────────────────────────────────────────────────
# 4. 层 10.5 注入 / 不注入
# ─────────────────────────────────────────────────────────────────────────────

def _apply_build_stubs(monkeypatch):
    import core.prompt_builder as _pb
    import core.presence as _pres
    import core.author_note_rotator as _anr
    import core.config_loader as _cl

    monkeypatch.setattr(_pb, "_load_jailbreak", lambda layer=None: "")
    monkeypatch.setattr(_pb, "_load_style_hint", lambda *, char_id="": "")
    monkeypatch.setattr(_pb, "_load_activity_snapshot", lambda *, char_id="": "")
    monkeypatch.setattr(_pb, "_format_afterglow_soft_hint", lambda uid, char_id="yexuan": "")
    monkeypatch.setattr(_pres, "get_last_seen_text", lambda uid: "")
    monkeypatch.setattr(_anr, "get_current_note", lambda paths=None, char_id=None: "")
    monkeypatch.setattr(_cl, "get_config", lambda: {"chat": {}})


def _build_minimal(monkeypatch, *, action_trace_entries=None, tool_result=None):
    _apply_build_stubs(monkeypatch)
    import core.prompt_builder as _pb
    from core.character_loader import Character

    char = Character(name="Companion")
    messages, meta = _pb.build(
        character=char, user_id="u_test", user_message="你好",
        history=[], relation={"role": "friend"}, profile={}, group_context=[],
        tool_result=tool_result, char_id="yexuan",
        action_trace_entries=action_trace_entries,
    )
    return messages, meta


class TestInjection:

    def test_present_with_entries(self, monkeypatch):
        entries = [{
            "ts": time.time(), "tool": "weather", "origin": "user_live",
            "status": "ok", "result_digest": "北京多云18-26度", "args_digest": "city=北京",
        }]
        messages, _ = _build_minimal(monkeypatch, action_trace_entries=entries)
        layer = next((m for m in messages if m.get("_layer") == "10.5_action_trace"), None)
        assert layer is not None
        assert "北京多云18-26度" in layer["content"]

    def test_absent_when_empty(self, monkeypatch):
        messages, _ = _build_minimal(monkeypatch, action_trace_entries=[])
        assert not any(m.get("_layer") == "10.5_action_trace" for m in messages)

    def test_absent_when_none(self, monkeypatch):
        messages, _ = _build_minimal(monkeypatch, action_trace_entries=None)
        assert not any(m.get("_layer") == "10.5_action_trace" for m in messages)

    def test_absent_when_disabled(self, monkeypatch):
        """action_trace.enabled=false 时 fetch_context 侧应已返回空列表；
        即使误传了非空 entries，也应验证 recent() 本身对 disabled 生效（见 TestEnabledGate）。
        这里只确认空列表场景下层 10.5 不出现（回归）。"""
        messages, _ = _build_minimal(monkeypatch, action_trace_entries=[])
        assert not any(m.get("_layer") == "10.5_action_trace" for m in messages)


class TestEnabledGate:

    def test_recent_empty_when_disabled(self, sandbox, monkeypatch):
        from core.memory import action_trace
        import core.config_loader as _cl

        uid = "u_disabled"
        action_trace.record(uid, DEFAULT_CHAR_ID, tool="get_time", origin="user_live", status="ok")
        monkeypatch.setattr(_cl, "get_config", lambda: {"action_trace": {"enabled": False}})
        assert action_trace.recent(uid, DEFAULT_CHAR_ID) == []

    def test_record_noop_when_disabled(self, sandbox, monkeypatch):
        from core.memory import action_trace
        import core.config_loader as _cl

        monkeypatch.setattr(_cl, "get_config", lambda: {"action_trace": {"enabled": False}})
        uid = "u_disabled2"
        action_trace.record(uid, DEFAULT_CHAR_ID, tool="get_time", origin="user_live", status="ok")

        monkeypatch.setattr(_cl, "get_config", lambda: {"action_trace": {"enabled": True}})
        assert action_trace.recent(uid, DEFAULT_CHAR_ID) == []


# ─────────────────────────────────────────────────────────────────────────────
# 5. 当轮去重
# ─────────────────────────────────────────────────────────────────────────────

class TestTurnDedup:

    def test_same_tool_result_excludes_latest_entry(self):
        from core.memory import action_trace
        entries = [
            {"ts": 100.0, "tool": "get_time", "status": "ok", "result_digest": "14:00"},
            {"ts": 200.0, "tool": "weather", "status": "ok", "result_digest": "北京多云"},
        ]
        block = action_trace.format_trace_block(
            entries, current_tool_result="工具已执行：weather，结果：北京多云18度",
        )
        assert "北京多云" not in block
        assert "14:00" in block

    def test_different_tool_keeps_all(self):
        from core.memory import action_trace
        entries = [
            {"ts": 200.0, "tool": "weather", "status": "ok", "result_digest": "北京多云"},
        ]
        block = action_trace.format_trace_block(
            entries, current_tool_result="工具已执行：get_time，结果：14:00",
        )
        assert "北京多云" in block

    def test_no_tool_result_keeps_all(self):
        from core.memory import action_trace
        entries = [
            {"ts": 200.0, "tool": "weather", "status": "ok", "result_digest": "北京多云"},
        ]
        block = action_trace.format_trace_block(entries, current_tool_result=None)
        assert "北京多云" in block

    def test_direct_question_can_be_answered_truthfully(self):
        from core.memory import action_trace
        entries = [
            {"ts": 200.0, "tool": "weather", "status": "ok", "result_digest": "北京多云"},
        ]
        block = action_trace.format_trace_block(entries)
        assert "可如实说明" in block
        assert "不必提‘工具’二字" in block

    def test_no_trace_adds_no_expression_permission(self):
        from core.memory import action_trace
        assert action_trace.format_trace_block([]) == ""


# ─────────────────────────────────────────────────────────────────────────────
# 6. event_log_echo 开关
# ─────────────────────────────────────────────────────────────────────────────

class TestEventLogEcho:

    def test_echo_on_writes_trigger_line(self, sandbox, monkeypatch):
        from core.memory import action_trace, event_log
        import core.config_loader as _cl

        monkeypatch.setattr(
            _cl, "get_config",
            lambda: {"action_trace": {"enabled": True, "event_log_echo": True}},
        )
        uid = "u_echo_on"
        action_trace.record(
            uid, DEFAULT_CHAR_ID, tool="weather", origin="user_live",
            status="ok", result_digest="北京多云",
        )
        text = event_log.get_recent_days(uid, days=1, char_id=DEFAULT_CHAR_ID)
        assert "trigger:action_trace" in text

    def test_echo_off_no_trigger_line(self, sandbox, monkeypatch):
        from core.memory import action_trace, event_log
        import core.config_loader as _cl

        monkeypatch.setattr(
            _cl, "get_config",
            lambda: {"action_trace": {"enabled": True, "event_log_echo": False}},
        )
        uid = "u_echo_off"
        action_trace.record(
            uid, DEFAULT_CHAR_ID, tool="weather", origin="user_live",
            status="ok", result_digest="北京多云",
        )
        text = event_log.get_recent_days(uid, days=1, char_id=DEFAULT_CHAR_ID)
        assert "trigger:action_trace" not in text

    def test_echo_skipped_for_non_ok_status(self, sandbox, monkeypatch):
        from core.memory import action_trace, event_log
        import core.config_loader as _cl

        monkeypatch.setattr(
            _cl, "get_config",
            lambda: {"action_trace": {"enabled": True, "event_log_echo": True}},
        )
        uid = "u_echo_failed"
        action_trace.record(
            uid, DEFAULT_CHAR_ID, tool="weather", origin="user_live",
            status="failed", result_digest="出错了",
        )
        text = event_log.get_recent_days(uid, days=1, char_id=DEFAULT_CHAR_ID)
        assert "trigger:action_trace" not in text


# ─────────────────────────────────────────────────────────────────────────────
# 7. peek_screen_content 敏感回归
# ─────────────────────────────────────────────────────────────────────────────

class TestPeekScreenSensitive:

    def test_result_digest_only_title_hint(self):
        from core.memory import action_trace
        raw_result = (
            "【窗口】Obsidian - 私密日记.md\n"
            "【可见文字】今天心情不好；和朋友吵架了\n"
            "【可交互元素】保存按钮；返回"
        )
        digest = action_trace.build_result_digest("peek_screen_content", raw_result)
        assert "Obsidian - 私密日记.md" in digest
        assert "今天心情不好" not in digest
        assert "和朋友吵架了" not in digest
        assert "可交互元素" not in digest
        assert "保存按钮" not in digest

    def test_format_line_does_not_leak_visible_text(self):
        from core.memory import action_trace
        digest = action_trace.build_result_digest(
            "peek_screen_content", "【窗口】代码编辑器\n【可见文字】敏感内容",
        )
        entry = {"ts": time.time(), "tool": "peek_screen_content", "status": "ok", "result_digest": digest}
        line = action_trace.format_line(entry)
        assert "敏感内容" not in line
        assert "代码编辑器" in line

    async def test_execute_peek_screen_content_trace_has_no_visible_text(self, sandbox, monkeypatch):
        import core.tool_dispatcher as td
        from core.session_state import SessionState
        from core.memory import action_trace

        monkeypatch.setattr(td, "get_config", lambda: _controlled_config())
        monkeypatch.setattr(td, "_current_mode", lambda: "danger")  # desktop 类工具需 danger 模式放行

        async def _fake_peek():
            return "【窗口】私密笔记\n【可见文字】超级敏感的日记内容\n【可交互元素】保存"

        monkeypatch.setitem(td._TOOL_REGISTRY["peek_screen_content"], "func", _fake_peek)

        state = SessionState()
        await td.execute(
            tool_name="peek_screen_content", tool_args={}, user_id="u_peek", target_id="u_peek",
            is_group=False, session_state=state, origin="user_live", char_id=DEFAULT_CHAR_ID,
        )
        entries = action_trace.recent("u_peek", DEFAULT_CHAR_ID, max_items=10, window_hours=999)
        assert len(entries) == 1
        assert "超级敏感的日记内容" not in entries[0]["result_digest"]
        assert "私密笔记" in entries[0]["result_digest"]
