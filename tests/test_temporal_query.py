"""
tests/test_temporal_query.py — 查询侧时间意图解析器测试（Brief 48）

core.memory.temporal_query.parse_query_time_range 是纯规则函数，无 IO/无 LLM，
不需要 sandbox fixture。表驱动覆盖 brief 列出的词形集合 + 模糊表述 + 跨月/跨年边界。
"""

from datetime import datetime

import pytest

from core.memory.temporal_query import parse_query_time_range

# 固定 now：2026-07-15 (周三) 10:30，本地时区。选周三是为了让"上周三/周一(最近一个)"
# 这类星期相关用例落在同一周内，方便手算校验（结果已用独立脚本跑过 core 实现交叉核对）。
NOW = datetime(2026, 7, 15, 10, 30).timestamp()


def _d(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d)


# (query, now, expected_since, expected_until) — expected 为 None 时表示应返回 None。
TABLE = [
    # ── 覆盖 brief 列出的词形集合 ──────────────────────────────────────────
    ("昨天你说了什么", NOW, _d(2026, 7, 14), _d(2026, 7, 15)),
    ("前天聊到的事", NOW, _d(2026, 7, 13), _d(2026, 7, 14)),
    ("3天前说的那个事", NOW, _d(2026, 7, 12), _d(2026, 7, 13)),
    ("我们上周聊了什么", NOW, _d(2026, 7, 2), _d(2026, 7, 9)),
    ("上周末去哪儿了", NOW, _d(2026, 7, 11), _d(2026, 7, 13)),
    ("上周三说的事", NOW, _d(2026, 7, 8), _d(2026, 7, 9)),
    ("上个月报的班怎么样了", NOW, _d(2026, 6, 1), _d(2026, 7, 1)),
    ("上月的事", NOW, _d(2026, 6, 1), _d(2026, 7, 1)),
    ("7月10日说的那件事", NOW, _d(2026, 7, 10), _d(2026, 7, 11)),
    ("2026年7月10日说的事", NOW, _d(2026, 7, 10), _d(2026, 7, 11)),
    ("7-10号说的事", NOW, _d(2026, 7, 10), _d(2026, 7, 11)),
    ("周一说的那件事", NOW, _d(2026, 7, 13), _d(2026, 7, 14)),  # 裸"周X"，最近一次
    ("周三说的那件事", NOW, _d(2026, 7, 15), _d(2026, 7, 16)),  # 今天正好周三，距离0
    ("150天前搬的家", NOW, _d(2026, 2, 15), _d(2026, 2, 16)),
    # ── 模糊表述：宁可不过滤，返回 None ────────────────────────────────────
    ("之前说过的事", NOW, None, None),
    ("很久以前的事了", NOW, None, None),
    ("以后再说吧", NOW, None, None),
    ("没有任何时间词的普通问题", NOW, None, None),
    ("下周三有空吗", NOW, None, None),  # 未来意图不在查询侧覆盖范围，保守不处理
    ("0天前", NOW, None, None),  # 非法/无意义的天数，不处理
    # ── 跨月 / 跨年边界 ────────────────────────────────────────────────────
    ("12月30日说的事", datetime(2026, 1, 5, 9, 0).timestamp(), _d(2025, 12, 30), _d(2025, 12, 31)),
    ("上个月的事", datetime(2026, 1, 3, 9, 0).timestamp(), _d(2025, 12, 1), _d(2026, 1, 1)),
    ("昨天说的事", datetime(2026, 1, 1, 0, 0).timestamp(), _d(2025, 12, 31), _d(2026, 1, 1)),
    ("前天说的事", datetime(2026, 3, 1, 23, 59, 59).timestamp(), _d(2026, 2, 27), _d(2026, 2, 28)),
]


@pytest.mark.parametrize("query,now,expected_since,expected_until", TABLE, ids=[t[0] for t in TABLE])
def test_parse_query_time_range_table(query, now, expected_since, expected_until):
    result = parse_query_time_range(query, now)
    if expected_since is None:
        assert result is None, f"{query!r} 应返回 None，实际 {result}"
        return
    assert result is not None, f"{query!r} 应解析出时间范围，实际 None"
    since_ts, until_ts = result
    assert datetime.fromtimestamp(since_ts) == expected_since, (
        f"{query!r} since 不符：期望 {expected_since}，实际 {datetime.fromtimestamp(since_ts)}"
    )
    assert datetime.fromtimestamp(until_ts) == expected_until, (
        f"{query!r} until 不符：期望 {expected_until}，实际 {datetime.fromtimestamp(until_ts)}"
    )


def test_until_ts_is_exclusive_and_covers_full_day():
    """"昨天" = [昨日00:00, 昨日24:00) —— until 恰好是今天 00:00，含昨天全天。"""
    since_ts, until_ts = parse_query_time_range("昨天说了什么", NOW)
    today_midnight = datetime(2026, 7, 15, 0, 0).timestamp()
    assert until_ts == today_midnight
    # 昨天最后一秒（23:59:59）应仍在范围内，今天第一秒不应在范围内。
    assert since_ts <= datetime(2026, 7, 14, 23, 59, 59).timestamp() < until_ts
    assert not (since_ts <= today_midnight < until_ts)


def test_non_string_and_empty_input_returns_none():
    assert parse_query_time_range("", NOW) is None
    assert parse_query_time_range("   ", NOW) is None
    assert parse_query_time_range(None, NOW) is None  # type: ignore[arg-type]


def test_parse_failure_is_fail_open(monkeypatch):
    """内部解析抛异常时不传播，返回 None（fail-open）。"""
    import core.memory.temporal_query as tq

    def _boom(text, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(tq, "_parse", _boom)
    assert parse_query_time_range("昨天", NOW) is None


def test_no_time_word_query_returns_none_for_regression_guard():
    """无时间词的正常问题必须返回 None，保证 retrieve() 默认参数路径行为不变。"""
    assert parse_query_time_range("我们聊聊今天的心情吧", NOW) is None
