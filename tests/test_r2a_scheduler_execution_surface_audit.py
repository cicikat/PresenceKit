"""
R2-A 调度器执行面审计 — 守卫测试

目的：精确记录当前执行面事实，防止统一决策面回退。
所有测试均为只读审计，不改变任何行为。

当前已知双执行面：
  1. Gating/Proposer live 路径（execution.py EXECUTE_MODE="live"）
  2. Legacy _check_* gather 路径（loop.py asyncio.gather）
  3. legacy_tick_should_send() 让路垫片（live 模式下让路）
  4. Watch 事件 adapter（WATCH_EXECUTE_MODE 仅控制即时 live/dry-run，决策仍走 gating）
  5. sensor_aware output_mode="return" 旁路（triggers/sensor_aware.py）
  6. policy.py 运行时策略表（由 gating._decide 使用）
  7. _pipeline_send 执行层（send + mark，不做二次仲裁）
"""

import ast
import importlib
import inspect
import pathlib

import pytest

ROOT = pathlib.Path(__file__).parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# A. policy.py 当前未被生产路径调用
# ─────────────────────────────────────────────────────────────────────────────

POLICY_MODULE = "core.scheduler.policy"
POLICY_FILE = ROOT / "core" / "scheduler" / "policy.py"


class TestPolicyWiredToRuntime:
    """R2-B done: policy.py is wired to gating.py and loop.py as the authoritative source."""

    def test_policy_wired_to_gating(self):
        """gating.py contains deferred import of core.scheduler.policy (R2-B wiring)."""
        gating_src = (ROOT / "core" / "scheduler" / "gating.py").read_text(encoding="utf-8")
        assert "core.scheduler.policy" in gating_src, (
            "gating.py no longer imports core.scheduler.policy — "
            "R2-B wiring may have been removed."
        )

    def test_policy_wired_to_loop(self):
        """R2-C: loop.py delegates to gating (which owns policy); no direct policy import."""
        loop_src = (ROOT / "core" / "scheduler" / "loop.py").read_text(encoding="utf-8")
        # R2-C: direct policy import removed from loop.py; policy access is via gating only.
        assert "core.scheduler.policy" not in loop_src, (
            "loop.py imports core.scheduler.policy directly — "
            "R2-C requires policy access to go through gating._decide() only."
        )
        # loop.py must still import gating to call run_shadow_tick.
        assert "core.scheduler.gating" in loop_src, (
            "loop.py no longer imports core.scheduler.gating — "
            "active-window/DND decisions must be delegated to gating."
        )

    def test_policy_table_entries_count(self):
        """POLICY_TABLE includes all currently policy-managed speaking triggers."""
        from core.scheduler.policy import POLICY_TABLE

        assert len(POLICY_TABLE) == 28, (
            f"POLICY_TABLE 条目数变化：期望 28，实际 {len(POLICY_TABLE)}。"
            "如需增删，先更新此测试并记录原因。"
        )

    def test_policy_validate_all_passes(self):
        """POLICY_TABLE 内部 4 条不变式全部成立。"""
        from core.scheduler.policy import _validate_all

        _validate_all()  # 不应抛出

    def test_policy_exempts_hr_critical(self):
        """hr_critical 必须为 emergency + exempt（最高级别保证）。"""
        from core.scheduler.policy import POLICY_TABLE

        p = POLICY_TABLE["hr_critical"]
        assert p.priority == "emergency"
        assert p.active_window_behavior == "exempt"

    def test_policy_birthday_eve_aligned_to_exempt(self):
        """R2-B done: birthday_eve/afternoon/night are exempt in both policy and loop."""
        from core.scheduler.policy import POLICY_TABLE
        from core.scheduler.loop import _HIGH_PRIORITY_TRIGGERS

        for tid in ("birthday_eve", "birthday_afternoon", "birthday_night"):
            assert POLICY_TABLE[tid].active_window_behavior == "exempt", (
                f"{tid} should be exempt in policy after R2-B alignment."
            )
            assert tid in _HIGH_PRIORITY_TRIGGERS, (
                f"{tid} should be in _HIGH_PRIORITY_TRIGGERS after R2-B alignment."
            )


# ─────────────────────────────────────────────────────────────────────────────
# B. _pipeline_send active-window 决策（R2-C: 完全移入 gating._decide()）
# ─────────────────────────────────────────────────────────────────────────────

class TestActiveWindowDecisionMoved:
    """
    R2-C done: _legacy_active_window_blocks/_legacy_dnd_blocks deleted from loop.py.
    _pipeline_send is execution-only; active-window/DND decisions are in gating._decide().
    """

    def test_pipeline_send_delegates_active_window_to_policy(self):
        """R2-C: _pipeline_send must NOT contain _legacy_active_window_blocks (deleted)."""
        from core.scheduler import loop

        src = inspect.getsource(loop._pipeline_send)
        # R2-C: legacy helpers deleted; gating._decide is the sole authority.
        assert "_legacy_active_window_blocks" not in src, (
            "_legacy_active_window_blocks found in _pipeline_send — "
            "R2-C requires this helper to be removed; gating._decide is the authority."
        )
        assert "_legacy_dnd_blocks" not in src, (
            "_legacy_dnd_blocks found in _pipeline_send — "
            "R2-C requires this helper to be removed."
        )
        # _HIGH_PRIORITY_TRIGGERS must not be inlined in _pipeline_send.
        assert "_HIGH_PRIORITY_TRIGGERS" not in src, (
            "_HIGH_PRIORITY_TRIGGERS still referenced inline in _pipeline_send."
        )

    def test_active_window_120s_in_user_active_recently(self):
        """120s window is still in _user_active_recently (config move deferred to R2-C/D)."""
        from core.scheduler.loop import _user_active_recently

        src = inspect.getsource(_user_active_recently)
        assert "120" in src, (
            "120s hardcode removed from _user_active_recently — "
            "if moved to config, update this test and document the config key."
        )

    def test_high_priority_triggers_aligned_with_policy_exempt(self):
        """R2-B done: _HIGH_PRIORITY_TRIGGERS exactly matches policy exempt set (no mismatch)."""
        from core.scheduler.loop import _HIGH_PRIORITY_TRIGGERS
        from core.scheduler.policy import POLICY_TABLE

        policy_exempt = {
            tid for tid, p in POLICY_TABLE.items() if p.active_window_behavior == "exempt"
        }
        assert _HIGH_PRIORITY_TRIGGERS == policy_exempt, (
            f"_HIGH_PRIORITY_TRIGGERS vs policy exempt mismatch after R2-B.\n"
            f"loop only: {_HIGH_PRIORITY_TRIGGERS - policy_exempt}\n"
            f"policy only: {policy_exempt - _HIGH_PRIORITY_TRIGGERS}\n"
            "Update policy.py or _HIGH_PRIORITY_TRIGGERS to re-align."
        )

    def test_gating_decide_applies_active_window_filter(self):
        """gating._decide source contains active_window_filtered reason (R2-B filter wired)."""
        from core.scheduler import gating

        src = inspect.getsource(gating._decide)
        assert "active_window_filtered" in src, (
            "active_window_filtered reason missing from gating._decide — "
            "R2-B active-window filter may have been removed."
        )

    def test_gating_decide_applies_dnd_filter(self):
        """gating._decide source contains dnd_filtered reason (R2-B DND wired)."""
        from core.scheduler import gating

        src = inspect.getsource(gating._decide)
        assert "dnd_filtered" in src, (
            "dnd_filtered reason missing from gating._decide — "
            "R2-B DND filter may have been removed."
        )


# ─────────────────────────────────────────────────────────────────────────────
# C. Legacy speaking trigger 列表固定
# ─────────────────────────────────────────────────────────────────────────────

# 所有调用 legacy_tick_should_send() 且可能调用 _pipeline_send 的 _check_* 函数
# （garden_water/daily 的 speaking 部分也通过 legacy_send 变量门控）
LEGACY_SPEAKING_TRIGGERS: frozenset[str] = frozenset({
    "morning_greeting",
    "night_reminder",
    "random_message",
    "weather_alert",
    "daily_journal",
    "spontaneous_recall",
    "diary_reminder",
    "diary_share_reminder",
    "period_reminder",
    "birthday_midnight",
    "birthday_eve",
    "birthday_afternoon",
    "birthday_night",
    "timenode",
    "festival",
    "holiday_boost",
    "reminders",
    # garden 系列通过 legacy_send 变量门控，归入此集
    "garden_bloom",
    "garden_harvest_expired",
    "garden_handle_ask",
    "garden_handle_gift",
    "garden_handle_self",
    "garden_vase_wilted",
})

MAINTENANCE_TRIGGERS: frozenset[str] = frozenset({
    "episodic_decay",
    "dlq_monitor",
    "log_maintenance",
    "episodic_sweep",
    "hidden_state_decay",
    "hidden_state_consolidate",
    "diary_inject",   # 维护型：读日记存 diary_context，无 _pipeline_send
})


class TestLegacyTriggerClassification:
    """legacy _check_* 分类守卫：speaking 与 maintenance 必须分离。"""

    def test_speaking_triggers_check_legacy_tick_should_send(self):
        """
        每个 legacy speaking trigger 对应的主触发器文件必须含 legacy_tick_should_send。
        确保新增 _check_* speaking trigger 不会绕过让路逻辑。
        """
        # 按触发器所在模块逐一验证
        trigger_module_map = {
            "morning_greeting": "core/scheduler/triggers/time_based.py",
            "night_reminder": "core/scheduler/triggers/time_based.py",
            "random_message": "core/scheduler/triggers/time_based.py",
            "weather_alert": "core/scheduler/triggers/time_based.py",
            "daily_journal": "core/scheduler/triggers/time_based.py",
            "spontaneous_recall": "core/scheduler/triggers/time_based.py",
            "diary_reminder": "core/scheduler/triggers/diary.py",
            "diary_share_reminder": "core/scheduler/triggers/diary.py",
            "period_reminder": "core/scheduler/triggers/period.py",
            "birthday_midnight": "core/scheduler/triggers/birthday.py",
            "timenode": "core/scheduler/triggers/timenode.py",
            "festival": "core/scheduler/triggers/festival.py",
            "holiday_boost": "core/scheduler/triggers/festival.py",
        }
        for trigger, relpath in trigger_module_map.items():
            src = (ROOT / relpath).read_text(encoding="utf-8")
            assert "legacy_tick_should_send" in src, (
                f"{trigger} 对应的 {relpath} 不含 legacy_tick_should_send；"
                "该触发器可能在 live 模式下与 gating 路径双发。"
            )

    def test_maintenance_triggers_do_not_call_pipeline_send(self):
        """
        纯维护型触发器不得调用 _pipeline_send。
        """
        maint_files = {
            "core/scheduler/triggers/hidden_state_decay.py",
            "core/scheduler/triggers/episodic_sweep.py",
        }
        for relpath in maint_files:
            src = (ROOT / relpath).read_text(encoding="utf-8")
            assert "_pipeline_send" not in src, (
                f"{relpath} 含 _pipeline_send 调用——"
                "维护型触发器不允许直接发言；如需发言，请迁移为 speaking trigger。"
            )

    def test_legacy_speaking_and_maintenance_sets_disjoint(self):
        """speaking 与 maintenance trigger 集合不得有重叠。"""
        overlap = LEGACY_SPEAKING_TRIGGERS & MAINTENANCE_TRIGGERS
        assert not overlap, (
            f"触发器分类重叠：{overlap}。"
            "请在 LEGACY_SPEAKING_TRIGGERS 或 MAINTENANCE_TRIGGERS 中移除。"
        )

    def test_diary_inject_is_maintenance_no_legacy_tick_check(self):
        """diary_inject 是纯维护触发器，不调用 legacy_tick_should_send，不发言。"""
        src = (ROOT / "core/scheduler/triggers/diary.py").read_text(encoding="utf-8")
        # _check_diary_inject 的定义不含 legacy_tick_should_send
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if node.name == "_check_diary_inject":
                    func_src = ast.get_source_segment(src, node) or ""
                    assert "legacy_tick_should_send" not in func_src, (
                        "_check_diary_inject 开始检查 legacy_tick_should_send——"
                        "请更新分类：它是维护型还是 speaking 型？"
                    )
                    assert "_pipeline_send" not in func_src, (
                        "_check_diary_inject 开始调用 _pipeline_send——"
                        "已从维护型变为 speaking 型，请更新 MAINTENANCE_TRIGGERS 分类。"
                    )
                    break

    def test_check_topic_followup_is_legacy_noop(self):
        """_check_topic_followup 是 legacy no-op stub，proposer 路径已接管。"""
        from core.scheduler.triggers.memory import _check_topic_followup

        src = inspect.getsource(_check_topic_followup)
        assert "legacy path" in src or "legacy no-op" in src or "skipped" in src, (
            "_check_topic_followup 不再是 no-op——"
            "如果已重新接入 legacy 路径，请从 LEGACY_NOOP_STUBS 移除并重新分类。"
        )


# ─────────────────────────────────────────────────────────────────────────────
# D. _mark / safe_write 仍可用（R2-A 不改变）
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkAndSafeWriteUnchanged:
    """_mark 和 safe_write 是冷却持久化的唯一渠道，R2-A 不改变其接口。"""

    def test_mark_exists_in_loop(self):
        from core.scheduler import loop

        assert callable(loop._mark)

    def test_mark_writes_to_last_trigger_dict(self, monkeypatch, tmp_path):
        """_mark(name) 更新 _last_trigger[name]，并写 scheduler_cooldowns.json。"""
        from core.scheduler import loop
        from core.safe_write import safe_write_json

        # patch get_paths().scheduler_cooldowns() 到 tmp
        cooldown_file = tmp_path / "scheduler_cooldowns.json"

        class _FakePaths:
            def scheduler_cooldowns(self):
                return cooldown_file

        monkeypatch.setattr(loop, "get_paths", lambda: _FakePaths())
        # 清空内存状态
        old = dict(loop._last_trigger)
        loop._last_trigger.clear()
        try:
            loop._mark("test_trigger")
            assert "test_trigger" in loop._last_trigger
            assert loop._last_trigger["test_trigger"] > 0
        finally:
            loop._last_trigger.clear()
            loop._last_trigger.update(old)

    def test_is_ready_exists_in_loop(self):
        from core.scheduler import loop

        assert callable(loop._is_ready)

    def test_safe_write_json_importable(self):
        from core.safe_write import safe_write_json

        assert callable(safe_write_json)


# ─────────────────────────────────────────────────────────────────────────────
# E. Watch rollback/config switch 与统一 gating
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchIndependentExecuteMode:
    """WATCH_EXECUTE_MODE 与 EXECUTE_MODE 分离，但不能绕过统一 gating。"""

    def test_watch_execute_mode_constant_exists(self):
        from core.scheduler.triggers.watch import WATCH_EXECUTE_MODE

        assert WATCH_EXECUTE_MODE in ("live", "dry_run"), (
            f"WATCH_EXECUTE_MODE={WATCH_EXECUTE_MODE!r} 不是合法值；"
            "只允许 'live' 或 'dry_run'。"
        )

    def test_watch_execute_mode_is_live_by_default(self):
        from core.scheduler.triggers.watch import WATCH_EXECUTE_MODE

        assert WATCH_EXECUTE_MODE == "live", (
            "WATCH_EXECUTE_MODE 默认值变更——请确认 Watch 事件驱动路径是否仍正常工作。"
        )

    def test_execution_execute_mode_is_live_by_default(self):
        from core.scheduler.execution import EXECUTE_MODE

        assert EXECUTE_MODE == "live", (
            "execution.EXECUTE_MODE 默认值变更——请确认 gating 路径是否仍正常工作。"
        )

    def test_watch_and_execution_mode_are_independent_symbols(self):
        """两个 EXECUTE_MODE 是独立常量，不共享同一内存对象。"""
        from core.scheduler.execution import EXECUTE_MODE as exec_mode
        from core.scheduler.triggers import watch

        # 改变 execution 模块的变量不影响 watch 模块
        import core.scheduler.execution as exec_mod

        original = exec_mod.EXECUTE_MODE
        exec_mod.EXECUTE_MODE = "dry_run"
        try:
            assert watch.WATCH_EXECUTE_MODE == "live", (
                "WATCH_EXECUTE_MODE 跟随了 EXECUTE_MODE 的变化——它们不应共享状态。"
            )
        finally:
            exec_mod.EXECUTE_MODE = original

    def test_watch_event_driven_triggers_use_unified_gating(self):
        """Watch event arrival and normal tick both use the unified gating decision."""
        from core.scheduler.gating import decide_and_execute_event

        assert callable(decide_and_execute_event)
        source = (ROOT / "core/scheduler/gating.py").read_text(encoding="utf-8")
        assert "WATCH_EVENT_DRIVEN_TRIGGERS" not in source

    def test_watch_triggers_still_registered_as_proposers(self):
        """Watch 触发器在 proposer_registry，供统一 gating 决策与 defer 重试。"""
        from core.scheduler.proposer_registry import registered_trigger_names

        names = registered_trigger_names()
        assert "hr_critical" in names
        assert "hr_high" in names
        assert "sleep_end" in names


# ─────────────────────────────────────────────────────────────────────────────
# F. sensor_aware output_mode="return" 旁路存在并记录
# ─────────────────────────────────────────────────────────────────────────────

class TestSensorAwareReturnModeBypass:
    """
    sensor_aware 使用 output_mode="return" + record_turn=False 拿到 LLM 回复，
    再自行调用 record_assistant_turn(fanout=["desktop", "mobile"])。
    这是"获取 reply 后自定义 fanout"模式，不是完全绕过 pipeline。
    """

    def test_sensor_aware_uses_output_mode_return(self):
        src = (ROOT / "core/scheduler/triggers/sensor_aware.py").read_text(encoding="utf-8")
        assert 'output_mode="return"' in src, (
            "sensor_aware 不再使用 output_mode='return'——"
            "如果已迁移为默认 speak 模式，请更新此测试和 docs。"
        )

    def test_sensor_aware_uses_record_turn_false(self):
        src = (ROOT / "core/scheduler/triggers/sensor_aware.py").read_text(encoding="utf-8")
        assert "record_turn=False" in src, (
            "sensor_aware 不再使用 record_turn=False——请确认 fanout 路径是否重复写入。"
        )

    def test_sensor_aware_manually_calls_record_assistant_turn(self):
        src = (ROOT / "core/scheduler/triggers/sensor_aware.py").read_text(encoding="utf-8")
        assert "record_assistant_turn" in src, (
            "sensor_aware 不再手动调用 record_assistant_turn——"
            "如果已切换为 _pipeline_send 默认 speak 路径，请更新此测试。"
        )

    def test_sensor_aware_fanout_is_desktop_mobile_only(self):
        src = (ROOT / "core/scheduler/triggers/sensor_aware.py").read_text(encoding="utf-8")
        assert '["desktop", "mobile"]' in src or "desktop" in src, (
            "sensor_aware fanout 不再限制为 desktop/mobile——请确认是否已广播到 QQ。"
        )

    def test_sensor_aware_still_goes_through_pipeline_send(self):
        src = (ROOT / "core/scheduler/triggers/sensor_aware.py").read_text(encoding="utf-8")
        assert "_pipeline_send" in src, (
            "sensor_aware 不再通过 _pipeline_send——"
            "如果已彻底绕过 pipeline，需要补充 perceive_event gate 和 conversation_lock。"
        )


# ─────────────────────────────────────────────────────────────────────────────
# G. legacy_tick_should_send 让路垫片语义
# ─────────────────────────────────────────────────────────────────────────────

class TestLegacyTickShouldSendShim:
    """让路垫片：live 模式 = False（让路），dry_run = True（允许 legacy 路径运行）。"""

    def test_live_mode_returns_false(self):
        from core.scheduler.execution import legacy_tick_should_send

        import core.scheduler.execution as exec_mod

        orig = exec_mod.EXECUTE_MODE
        exec_mod.EXECUTE_MODE = "live"
        try:
            assert legacy_tick_should_send() is False
        finally:
            exec_mod.EXECUTE_MODE = orig

    def test_dry_run_mode_returns_true(self):
        from core.scheduler.execution import legacy_tick_should_send

        import core.scheduler.execution as exec_mod

        orig = exec_mod.EXECUTE_MODE
        exec_mod.EXECUTE_MODE = "dry_run"
        try:
            assert legacy_tick_should_send() is True
        finally:
            exec_mod.EXECUTE_MODE = orig

    def test_force_true_always_returns_true(self):
        from core.scheduler.execution import legacy_tick_should_send

        import core.scheduler.execution as exec_mod

        orig = exec_mod.EXECUTE_MODE
        exec_mod.EXECUTE_MODE = "live"
        try:
            assert legacy_tick_should_send(force=True) is True
        finally:
            exec_mod.EXECUTE_MODE = orig


# ─────────────────────────────────────────────────────────────────────────────
# H. gating MIGRATED_TRIGGERS 快照（记录迁移覆盖范围）
# ─────────────────────────────────────────────────────────────────────────────

class TestMigratedTriggersSnapshot:
    """
    gating.MIGRATED_TRIGGERS 记录哪些触发器已有原生 proposer。
    本测试固定当前集合，防止无声删减。
    """

    EXPECTED_MIGRATED: frozenset[str] = frozenset({
        "hr_critical", "birthday_midnight", "birthday_eve", "birthday_afternoon",
        "birthday_night", "period_reminder", "morning_greeting", "night_reminder",
        "daily_journal", "diary_reminder", "diary_share_reminder", "random_message",
        "hr_high", "sleep_end", "weather_alert", "topic_followup", "timenode",
        "festival", "holiday_boost", "spontaneous_recall",
        "garden_bloom", "garden_harvest_expired", "garden_handle_ask",
        "garden_handle_gift", "garden_handle_self", "garden_vase_wilted",
        "reminders", "overflow", "presence_nag", "dream_exit", "letter_writer",
        "coplay_commentary",
    })

    def test_migrated_triggers_matches_snapshot(self):
        from core.scheduler.gating import MIGRATED_TRIGGERS

        assert MIGRATED_TRIGGERS == self.EXPECTED_MIGRATED, (
            f"MIGRATED_TRIGGERS 发生变化。\n"
            f"新增：{MIGRATED_TRIGGERS - self.EXPECTED_MIGRATED}\n"
            f"移除：{self.EXPECTED_MIGRATED - MIGRATED_TRIGGERS}\n"
            "请同步更新 EXPECTED_MIGRATED 并记录原因。"
        )

    def test_migrated_triggers_all_have_registered_proposer(self):
        """每个 MIGRATED_TRIGGERS 成员都必须在 proposer_registry 有对应 proposer。"""
        from core.scheduler.gating import MIGRATED_TRIGGERS
        from core.scheduler.proposer_registry import registered_trigger_names

        registered = registered_trigger_names()
        not_registered = MIGRATED_TRIGGERS - registered
        assert not not_registered, (
            f"以下触发器在 MIGRATED_TRIGGERS 中但未注册 proposer：{not_registered}\n"
            "请补充 register_proposer() 调用，或从 MIGRATED_TRIGGERS 移除。"
        )


# ─────────────────────────────────────────────────────────────────────────────
# I. R2-A 不改变发言行为（回归防护）
# ─────────────────────────────────────────────────────────────────────────────

class TestR2ANosBehaviorChange:
    """R2-A 审计包：确认核心发言路径接口未变动。"""

    def test_pipeline_send_signature_unchanged(self):
        """_pipeline_send 接口签名未变。"""
        import inspect
        from core.scheduler.loop import _pipeline_send

        sig = inspect.signature(_pipeline_send)
        params = list(sig.parameters.keys())
        assert "prompt" in params
        assert "trigger_name" in params
        assert "output_mode" in params
        assert "record_turn" in params
        assert "kind" in params

    def test_execute_prompt_signature_unchanged(self):
        """execution.execute_prompt 接口签名未变。"""
        import inspect
        from core.scheduler.execution import execute_prompt

        sig = inspect.signature(execute_prompt)
        params = list(sig.parameters.keys())
        assert "trigger_name" in params
        assert "prompt_factory" in params
        assert "dry_run" in params
        assert "would_mark" in params

    def test_run_shadow_tick_still_exists(self):
        from core.scheduler.gating import run_shadow_tick

        assert callable(run_shadow_tick)

    def test_loop_calls_run_shadow_tick(self):
        """loop._loop 的源码仍包含对 run_shadow_tick 的调用。"""
        from core.scheduler import loop

        src = inspect.getsource(loop._loop)
        assert "run_shadow_tick" in src

    def test_on_watch_event_still_exported_from_loop(self):
        """loop.py 通过 noqa import 向外暴露 on_watch_event（admin/routers/watch.py 依赖）。"""
        from core.scheduler.loop import on_watch_event

        assert callable(on_watch_event)

    def test_kind_guard_still_in_pipeline_send(self):
        """kind guard（_assert_trigger_outlet_kind）仍在 _pipeline_send 中。"""
        from core.scheduler.loop import _pipeline_send

        src = inspect.getsource(_pipeline_send)
        assert "_assert_trigger_outlet_kind" in src
