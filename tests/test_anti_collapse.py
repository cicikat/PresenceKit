"""
detect_reply_length_collapse 单元测试（CC 任务 24 · 2.1 长/短两挡非对称触发）
"""

from core.memory.short_term import detect_reply_length_collapse


def _history(lengths: list[int]) -> list[dict]:
    return [
        {"role": "assistant", "content": "字" * n}
        for n in lengths
    ]


def test_four_long_replies_trigger_long_hint():
    # 近 4 条全部 >= short_max(60) -> 触发长句版提示（易触发）
    hint = detect_reply_length_collapse(_history([90, 95, 100, 110]))
    assert hint is not None
    assert "收短" in hint


def test_three_long_replies_not_enough():
    # 只有 3 条长句，不足 recent_n_long=4 -> 不触发
    hint = detect_reply_length_collapse(_history([90, 95, 100]))
    assert hint is None


def test_seven_short_replies_trigger_short_hint():
    # 近 7 条全部 < short_max(60) -> 触发通用打破惯性提示（难触发）
    hint = detect_reply_length_collapse(_history([3, 5, 8, 10, 12, 20, 30]))
    assert hint is not None
    assert "太短" in hint
    assert r"\n\n" in hint


def test_six_short_replies_not_enough():
    # 只有 6 条短句，不足 recent_n_short=7 -> 不触发
    hint = detect_reply_length_collapse(_history([3, 5, 8, 10, 12, 20]))
    assert hint is None


def test_mixed_short_and_long_not_triggered():
    # 最近 4 条不全长（含短句），最近 7 条不全短（含长句）-> 都不触发
    hint = detect_reply_length_collapse(_history([5, 90, 20, 90, 90, 90, 5]))
    assert hint is None


def test_non_assistant_messages_ignored():
    history = [
        {"role": "user", "content": "字" * 100},
        {"role": "assistant", "content": "字" * 90},
        {"role": "assistant", "content": "字" * 95},
        {"role": "assistant", "content": "字" * 100},
        {"role": "assistant", "content": "字" * 110},
    ]
    hint = detect_reply_length_collapse(history)
    assert hint is not None
    assert "收短" in hint


def test_custom_short_max_and_recent_n():
    hint = detect_reply_length_collapse(
        _history([2, 3, 4, 4]), short_max=5, recent_n_long=4, recent_n_short=7,
    )
    # 全部 < short_max=5 但不足 recent_n_short=7 条 -> 不触发
    assert hint is None
    hint2 = detect_reply_length_collapse(
        _history([2, 3, 4, 4, 3, 2, 1]), short_max=5, recent_n_long=4, recent_n_short=7,
    )
    assert hint2 is not None
    assert "太短" in hint2
    assert r"\n\n" in hint2


def test_long_check_takes_priority_over_short():
    # 近 4 条全长句（也满足不了短句判断，因为长度不足 short_max），确保走长句分支
    hint = detect_reply_length_collapse(_history([90, 90, 90, 90, 90, 90, 90]))
    assert "收短" in hint
