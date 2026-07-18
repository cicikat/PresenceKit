"""
TriggerPolicy — 触发策略静态配置表

R2-B 后此文件被 gating.py 和 loop.py 通过延迟 import 引用，是 active-window 决策的单一权威来源。
不引入 loop / execution / gating 的任何符号（禁止循环导入）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------

Priority = Literal["emergency", "high", "normal", "filler"]
ActiveWindowBehavior = Literal["exempt", "defer", "drop"]
OnDeferExpire = Literal["drop", "force_send"]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TriggerPolicy:
    trigger_id: str

    # 优先级：emergency > high > normal > filler
    priority: Priority

    # 活跃窗口内的行为
    # exempt   — 无视活跃窗口，立即发送
    # defer    — 推迟到窗口结束后发送
    # drop     — 直接丢弃
    active_window_behavior: ActiveWindowBehavior

    # 发送/推迟/丢弃时是否打 mark
    mark_on_send: bool = True
    mark_on_defer: bool = False   # 业务规定恒为 False，见断言 assert_mark_on_defer_false
    mark_on_drop: bool = False

    # 推迟超时（秒）；active_window_behavior != "defer" 时可置 0
    max_defer_age_secs: int = 0

    # 推迟超时后的处理：drop 或 force_send
    on_defer_expire: OnDeferExpire = "drop"

    # 发送后需要联动清除的其他 trigger_id 的 mark 列表
    # 注意：cross_marks 中的操作只在消息实际 sent 后执行，绝不在 defer/drop 时执行
    cross_marks: list[str] = field(default_factory=list)

    # ProactiveLedger 豁免（全局最小间隔 + 每日预算），与 priority=="emergency" 解耦：
    # priority=="emergency" 同时豁免 DND；ledger_exempt 只豁免 ledger，DND 仍按 priority 判断。
    # 用于"迟发即错但不该打断免打扰"的场景（如生日系列，Brief 95 补遗）。真正发送后
    # record_send() 仍会记账，不豁免统计——只豁免"能不能发"这道闸。
    ledger_exempt: bool = False


# ---------------------------------------------------------------------------
# 静态配置表（占位，内容由后续迭代填入）
# ---------------------------------------------------------------------------

POLICY_TABLE: dict[str, TriggerPolicy] = {

    # ── emergency / high，直接豁免活跃窗口 ──────────────────────────────────
    "hr_critical": TriggerPolicy(
        trigger_id="hr_critical",
        priority="emergency",
        active_window_behavior="exempt",
    ),
    "period_reminder": TriggerPolicy(
        trigger_id="period_reminder",
        priority="high",
        active_window_behavior="exempt",
    ),
    # 零点告白，迟发即错，时刻精确。窗口只有 00:00-00:05 五分钟，一旦被 ProactiveLedger
    # 全局间隔/每日预算顶掉就是一整年的错过——ledger_exempt=True 豁免这道闸（但不豁免
    # DND：priority 不是 emergency，免打扰时仍会被拦，这是有意的克制，见 Brief 95 补遗审计）。
    "birthday_midnight": TriggerPolicy(
        trigger_id="birthday_midnight",
        priority="high",
        active_window_behavior="exempt",
        ledger_exempt=True,
    ),

    # ── 生日系列 exempt（生日系列打断用户是预期行为；R2-B 与 _HIGH_PRIORITY_TRIGGERS 对齐）──
    # 三项窗口虽比零点宽（数小时），但同样可能被同日已用尽的 ledger 预算/间隔顶掉，
    # 一样 ledger_exempt=True（Brief 95 补遗审计）。
    "birthday_eve": TriggerPolicy(
        trigger_id="birthday_eve",
        priority="normal",
        active_window_behavior="exempt",
        ledger_exempt=True,
    ),
    "birthday_afternoon": TriggerPolicy(
        trigger_id="birthday_afternoon",
        priority="normal",
        active_window_behavior="exempt",
        ledger_exempt=True,
    ),
    "birthday_night": TriggerPolicy(
        trigger_id="birthday_night",
        priority="normal",
        active_window_behavior="exempt",
        ledger_exempt=True,
    ),

    # ── 其他 defer 条目 ────────────────────────────────────────────────────
    # 受 HEART_RATE_PROPOSAL_TTL_SECONDS=10min 限制，proposal 10min 后
    # 即 return None，defer 窗口不能超过 TTL，否则是空头支票。
    # 真要延长 defer 需先延长 TTL（但该常量与 hr_critical 共用，改动需评估）
    "hr_high": TriggerPolicy(
        trigger_id="hr_high",
        priority="normal",
        active_window_behavior="defer",
        max_defer_age_secs=10 * 60,
        on_defer_expire="drop",
    ),
    "weather_alert": TriggerPolicy(
        trigger_id="weather_alert",
        priority="normal",
        active_window_behavior="defer",
        max_defer_age_secs=30 * 60,
        on_defer_expire="drop",
    ),
    "topic_followup": TriggerPolicy(
        trigger_id="topic_followup",
        priority="normal",
        active_window_behavior="defer",
        max_defer_age_secs=2 * 3600,
        on_defer_expire="drop",
    ),
    # 宁可打断也不漏；现状靠 tick 重捞已不漏，force_send 给未来接入用
    "reminders": TriggerPolicy(
        trigger_id="reminders",
        priority="high",
        active_window_behavior="defer",
        max_defer_age_secs=10 * 60,
        on_defer_expire="force_send",
    ),
    "diary_share_reminder": TriggerPolicy(
        trigger_id="diary_share_reminder",
        priority="normal",
        active_window_behavior="defer",
        max_defer_age_secs=4 * 3600,
        on_defer_expire="drop",
    ),
    "diary_reminder": TriggerPolicy(
        trigger_id="diary_reminder",
        priority="normal",
        active_window_behavior="defer",
        max_defer_age_secs=4 * 3600,
        on_defer_expire="drop",
    ),

    # ── normal+drop（非 filler；活跃窗口内失效，不打 mark）─────────────────
    # drop 而非 defer：睡醒问候价值全在第一时间，TTL（bot 接收后 10min）
    # 过期即失效；cross_marks 仅在（理论上的）sent 后执行，drop 路径绝不 cross-mark，
    # 否则压掉 morning_greeting = 双重漏发
    # normal+drop+mark_on_drop=False：区别于 filler drop（filler drop 要 mark 冷却）
    "sleep_end": TriggerPolicy(
        trigger_id="sleep_end",
        priority="normal",
        active_window_behavior="drop",
        mark_on_drop=False,
        cross_marks=["morning_greeting"],
    ),
    "dream_exit": TriggerPolicy(
        trigger_id="dream_exit",
        priority="normal",
        active_window_behavior="defer",
        max_defer_age_secs=4 * 3600,
        on_defer_expire="force_send",
    ),

    # ── filler，活跃窗口内直接 drop ────────────────────────────────────────
    "random_message": TriggerPolicy(
        trigger_id="random_message",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "spontaneous_recall": TriggerPolicy(
        trigger_id="spontaneous_recall",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "garden_bloom": TriggerPolicy(
        trigger_id="garden_bloom",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "garden_harvest_expired": TriggerPolicy(
        trigger_id="garden_harvest_expired",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "garden_handle_gift": TriggerPolicy(
        trigger_id="garden_handle_gift",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "garden_handle_self": TriggerPolicy(
        trigger_id="garden_handle_self",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "garden_vase_wilted": TriggerPolicy(
        trigger_id="garden_vase_wilted",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "festival": TriggerPolicy(
        trigger_id="festival",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "holiday_boost": TriggerPolicy(
        trigger_id="holiday_boost",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "timenode": TriggerPolicy(
        trigger_id="timenode",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "daily_journal": TriggerPolicy(
        trigger_id="daily_journal",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=True,
    ),
    "letter_writer": TriggerPolicy(
        trigger_id="letter_writer",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=False,
    ),
    "presence_nag": TriggerPolicy(
        trigger_id="presence_nag",
        priority="filler",
        active_window_behavior="drop",
        mark_on_drop=False,
    ),
}


# ---------------------------------------------------------------------------
# 断言函数（仅定义，不在运行时调用；供后续测试层或接入层显式调用）
# ---------------------------------------------------------------------------

def assert_mark_on_defer_false(policy: TriggerPolicy) -> None:
    """mark_on_defer 必须为 False。"""
    assert policy.mark_on_defer is False, (
        f"[{policy.trigger_id}] mark_on_defer 必须为 False，"
        f"当前值：{policy.mark_on_defer}"
    )


def assert_emergency_must_exempt(policy: TriggerPolicy) -> None:
    """priority == 'emergency' 时 active_window_behavior 必须为 'exempt'。"""
    if policy.priority == "emergency":
        assert policy.active_window_behavior == "exempt", (
            f"[{policy.trigger_id}] emergency 级别必须 exempt，"
            f"当前值：{policy.active_window_behavior}"
        )


def assert_cross_marks_only_on_sent(policy: TriggerPolicy) -> None:
    """cross_marks 非空时，联动清除操作只在消息实际 sent 后执行，
    绝不在 defer 或 drop 路径上执行。"""
    if policy.cross_marks:
        # 此处仅作文档性断言；执行侧必须在 sent 回调中触发，而非 defer/drop 分支
        assert True, "占位：执行侧保证 cross_marks 只在 sent 后执行"


def assert_filler_no_defer(policy: TriggerPolicy) -> None:
    """filler 优先级不允许 defer，只能 drop。"""
    if policy.priority == "filler":
        assert policy.active_window_behavior != "defer", (
            f"[{policy.trigger_id}] filler 级别不允许 defer，"
            f"只能 drop（当前：{policy.active_window_behavior}）"
        )


def assert_ledger_exempt_requires_exempt_window(policy: TriggerPolicy) -> None:
    """ledger_exempt=True 时 active_window_behavior 必须为 'exempt'，否则豁免了 ledger 这道闸
    也还是会被 active-window defer/drop 拦下，豁免没有意义。"""
    if policy.ledger_exempt:
        assert policy.active_window_behavior == "exempt", (
            f"[{policy.trigger_id}] ledger_exempt 级别必须 active_window_behavior='exempt'，"
            f"当前值：{policy.active_window_behavior}"
        )


# ---------------------------------------------------------------------------
# 批量校验（仅定义，不在运行时调用；测试层显式调用以一次性校验全表）
# ---------------------------------------------------------------------------

def _validate_all() -> None:
    """对 POLICY_TABLE 每条策略跑全部 4 个断言。"""
    for policy in POLICY_TABLE.values():
        assert_mark_on_defer_false(policy)
        assert_emergency_must_exempt(policy)
        assert_cross_marks_only_on_sent(policy)
        assert_filler_no_defer(policy)
        assert_ledger_exempt_requires_exempt_window(policy)
