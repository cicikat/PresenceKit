"""
tests/test_anti_collapse_persistence.py — Brief 54-B

反坍缩提示持久化倒计时回归测试：
(1) 长度维度：detect_reply_length_collapse 命中 → 计数器设为 hint_rounds，连续
    hint_rounds 轮（含触发当轮）注入同一份文案；期间再次命中重置为满值，不提前清零。
(2) 分段维度：note_segment_collapse_signal 用"原始文本"判定"无换行+超长"，连续
    segment_recent_n 轮命中才触发；1 轮不触发；触发后同样延续 hint_rounds 轮。
(3) 两个维度可同轮合并注入。
"""

from __future__ import annotations

import itertools

from core.memory.short_term import (
    get_anti_collapse_hint,
    note_segment_collapse_signal,
    reset_anti_collapse_state,
    DEFAULT_HINT_ROUNDS,
    DEFAULT_SEGMENT_MIN_LEN,
    DEFAULT_SEGMENT_RECENT_N,
)

_uid_counter = itertools.count()


def _fresh_uid() -> str:
    """每个测试用例独立 uid，避免模块级内存状态跨用例串扰。"""
    return f"ac_test_{next(_uid_counter)}"


def _long_history(n: int, length: int = 90) -> list[dict]:
    return [{"role": "assistant", "content": "字" * length} for _ in range(n)]


def _empty_history() -> list[dict]:
    return []


# ── 长度维度：触发 → 3 轮衰减 → 期间再触发重置 ───────────────────────────────

def test_length_hint_persists_for_hint_rounds_after_trigger():
    uid = _fresh_uid()
    triggering_history = _long_history(4)  # 满足 recent_n_long=4 全长句
    normal_history = _long_history(1)      # 之后不再满足触发条件

    # 第1轮：真正触发
    hint1 = get_anti_collapse_hint(uid, triggering_history)
    assert hint1 is not None
    assert "收短" in hint1

    # 第2、3轮：detect 本身不再命中（history 已换成不满足条件的），但倒计时仍在，继续注入
    hint2 = get_anti_collapse_hint(uid, normal_history)
    assert hint2 is not None
    assert "收短" in hint2

    hint3 = get_anti_collapse_hint(uid, normal_history)
    assert hint3 is not None
    assert "收短" in hint3

    # 第4轮：倒计时耗尽（hint_rounds=3 已经在第1/2/3轮各消费一次），不再注入
    hint4 = get_anti_collapse_hint(uid, normal_history)
    assert hint4 is None


def test_length_hint_resets_to_full_on_retrigger_mid_countdown():
    uid = _fresh_uid()
    triggering_history = _long_history(4)
    normal_history = _long_history(1)

    get_anti_collapse_hint(uid, triggering_history)  # 轮1：触发，remaining 3->2
    get_anti_collapse_hint(uid, normal_history)       # 轮2：衰减，remaining 2->1

    # 轮3：期间再次触发 → 重置为满值而不是继续从 1 往下掉
    hint3 = get_anti_collapse_hint(uid, triggering_history)
    assert hint3 is not None

    # 重置后应该还能再撑 2 轮（不算本轮）而不是立刻归零
    hint4 = get_anti_collapse_hint(uid, normal_history)
    assert hint4 is not None
    hint5 = get_anti_collapse_hint(uid, normal_history)
    assert hint5 is not None
    hint6 = get_anti_collapse_hint(uid, normal_history)
    assert hint6 is None


def test_length_hint_none_when_never_triggered():
    uid = _fresh_uid()
    assert get_anti_collapse_hint(uid, _empty_history()) is None
    assert get_anti_collapse_hint(uid, _long_history(1)) is None


# ── 分段维度：连续 2 轮无 \n + 超长 才触发；1 轮不触发 ───────────────────────

def test_segment_hint_not_triggered_by_single_round():
    uid = _fresh_uid()
    long_no_newline = "话" * (DEFAULT_SEGMENT_MIN_LEN + 10)

    note_segment_collapse_signal(uid, long_no_newline)
    hint = get_anti_collapse_hint(uid, _empty_history())
    assert hint is None


def test_segment_hint_triggered_by_two_consecutive_rounds():
    uid = _fresh_uid()
    long_no_newline = "话" * (DEFAULT_SEGMENT_MIN_LEN + 10)

    note_segment_collapse_signal(uid, long_no_newline)
    note_segment_collapse_signal(uid, long_no_newline)

    hint = get_anti_collapse_hint(uid, _empty_history())
    assert hint is not None
    assert "分段" in hint


def test_segment_streak_breaks_on_short_or_newlined_reply():
    uid = _fresh_uid()
    long_no_newline = "话" * (DEFAULT_SEGMENT_MIN_LEN + 10)
    with_newline = "话" * (DEFAULT_SEGMENT_MIN_LEN + 10) + "\n" + "话"

    note_segment_collapse_signal(uid, long_no_newline)
    note_segment_collapse_signal(uid, with_newline)  # 打断连续，streak 归零
    note_segment_collapse_signal(uid, long_no_newline)  # 只重新计数到 1，还不够 2

    hint = get_anti_collapse_hint(uid, _empty_history())
    assert hint is None


def test_segment_hint_below_threshold_not_triggered():
    uid = _fresh_uid()
    short_no_newline = "话" * (DEFAULT_SEGMENT_MIN_LEN - 5)

    note_segment_collapse_signal(uid, short_no_newline)
    note_segment_collapse_signal(uid, short_no_newline)

    hint = get_anti_collapse_hint(uid, _empty_history())
    assert hint is None


def test_segment_hint_persists_for_hint_rounds():
    uid = _fresh_uid()
    long_no_newline = "话" * (DEFAULT_SEGMENT_MIN_LEN + 10)

    note_segment_collapse_signal(uid, long_no_newline)
    note_segment_collapse_signal(uid, long_no_newline)

    assert get_anti_collapse_hint(uid, _empty_history()) is not None  # 轮1
    assert get_anti_collapse_hint(uid, _empty_history()) is not None  # 轮2
    assert get_anti_collapse_hint(uid, _empty_history()) is not None  # 轮3
    assert get_anti_collapse_hint(uid, _empty_history()) is None      # 轮4：耗尽


def test_segment_custom_thresholds_via_kwargs():
    uid = _fresh_uid()
    text = "话" * 15  # 高于自定义阈值 10，低于默认阈值 40

    note_segment_collapse_signal(uid, text, segment_min_len=10, segment_recent_n=1)
    hint = get_anti_collapse_hint(uid, _empty_history())
    assert hint is not None
    assert "分段" in hint


# ── 两个维度可同轮合并 ────────────────────────────────────────────────────────

def test_length_and_segment_hints_combine_in_same_round():
    uid = _fresh_uid()
    long_no_newline = "话" * (DEFAULT_SEGMENT_MIN_LEN + 10)
    note_segment_collapse_signal(uid, long_no_newline)
    note_segment_collapse_signal(uid, long_no_newline)

    triggering_history = _long_history(4)
    hint = get_anti_collapse_hint(uid, triggering_history)
    assert hint is not None
    assert "收短" in hint
    assert "分段" in hint


# ── reset_anti_collapse_state 测试工具本身 ───────────────────────────────────

def test_reset_anti_collapse_state_clears_specific_uid():
    uid = _fresh_uid()
    triggering_history = _long_history(4)
    get_anti_collapse_hint(uid, triggering_history)

    reset_anti_collapse_state(uid)
    assert get_anti_collapse_hint(uid, _empty_history()) is None


def test_defaults_match_documented_values():
    assert DEFAULT_HINT_ROUNDS == 3
    assert DEFAULT_SEGMENT_MIN_LEN == 40
    assert DEFAULT_SEGMENT_RECENT_N == 2
