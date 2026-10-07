"""core.reply_context 单测（Brief 98 §2：引用回复 reply_to 前缀构造）。"""

from datetime import datetime

from core.reply_context import apply_reply_prefix, build_reply_prefix, format_relative_time


class TestFormatRelativeTime:
    def test_today(self):
        now = datetime(2026, 7, 19, 15, 0, 0).timestamp()
        ts = datetime(2026, 7, 19, 9, 30, 0).timestamp()
        assert format_relative_time(ts, now) == "今天 09:30"

    def test_one_to_six_days_ago(self):
        now = datetime(2026, 7, 19, 12, 0, 0).timestamp()
        ts = datetime(2026, 7, 16, 12, 0, 0).timestamp()
        assert format_relative_time(ts, now) == "3天前"

    def test_seven_days_or_more_uses_date(self):
        now = datetime(2026, 7, 19, 12, 0, 0).timestamp()
        ts = datetime(2026, 7, 12, 8, 0, 0).timestamp()  # 恰好 7 天前，边界值
        assert format_relative_time(ts, now) == "7月12日"


class TestBuildReplyPrefix:
    def test_valid_reply_to_builds_prefix(self):
        now = datetime(2026, 7, 19, 12, 0, 0).timestamp()
        ts = datetime(2026, 7, 19, 9, 0, 0).timestamp()
        prefix = build_reply_prefix({"text": "早上好呀", "ts": ts}, now)
        assert '作者未经核验' in prefix and '2026-07-19T09:00:00' in prefix

    def test_missing_reply_to(self):
        assert build_reply_prefix(None) is None

    def test_non_dict_reply_to(self):
        assert build_reply_prefix("not a dict") is None

    def test_empty_text_downgrades(self):
        assert build_reply_prefix({"text": "  ", "ts": time_now()}) is None

    def test_missing_text_downgrades(self):
        assert build_reply_prefix({"ts": time_now()}) is None

    def test_negative_ts_downgrades(self):
        assert build_reply_prefix({"text": "hi", "ts": -5}) is None

    def test_future_ts_downgrades(self):
        now = time_now()
        assert build_reply_prefix({"text": "hi", "ts": now + 3600}, now) is None

    def test_non_numeric_ts_downgrades(self):
        assert build_reply_prefix({"text": "hi", "ts": "not-a-number"}) is None

    def test_bool_ts_downgrades(self):
        # isinstance(True, int) is True in Python; must be explicitly excluded.
        assert build_reply_prefix({"text": "hi", "ts": True}) is None

    def test_long_text_is_truncated(self):
        now = time_now()
        long_text = "a" * 500
        prefix = build_reply_prefix({"text": long_text, "ts": now - 10}, now)
        assert prefix is not None
        # 前缀里被截断部分应恰好 200 字
        quoted = prefix.split("「", 1)[1].rsplit("」", 1)[0]
        assert len(quoted) == 200


class TestApplyReplyPrefix:
    def test_valid_reply_to_prepends(self):
        now = datetime(2026, 7, 19, 12, 0, 0).timestamp()
        ts = datetime(2026, 7, 19, 9, 0, 0).timestamp()
        result = apply_reply_prefix("我也是", {"text": "早上好呀", "ts": ts}, now)
        assert '2026-07-19T09:00:00' in result and result.endswith('：我也是')

    def test_missing_reply_to_passthrough(self):
        assert apply_reply_prefix("我也是", None) == "我也是"

    def test_invalid_reply_to_passthrough(self):
        assert apply_reply_prefix("我也是", {"text": "", "ts": 1.0}) == "我也是"


def time_now() -> float:
    import time

    return time.time()
