"""
tests/test_episodic_time_anchor.py — P0-3 验收

断言覆盖：
- occurred_at=两个月前 + temporal_ref=past → 渲染"N个月前"，绝不出现"刚刚/几小时前/今天"
- occurred_at=现在 + temporal_ref=none → 可渲染"刚刚"（实时型保留细粒度）
- occurred_at=刚才 + temporal_ref=past → 渲染"之前"，不出现"刚刚"
- 旧数据无 occurred_at → 回退 timestamp，行为与改前一致
- _derive_occurred_at 正确取最早 turn 时刻
- _parse_turn_ms 解析 turn_id 毫秒
"""

import time as time_module

import pytest

from core.memory.episodic_memory import format_for_prompt
from core.memory.fixation_pipeline import _derive_occurred_at, _parse_turn_ms


# ─── fixtures ────────────────────────────────────────────────────────────────

_BASE_NOW = 1_700_000_000.0   # 固定参考时刻，避免跨秒抖动
_TWO_MONTHS_AGO = _BASE_NOW - 70 * 86400
_ONE_HOUR_AGO = _BASE_NOW - 3600


@pytest.fixture()
def frozen_time(monkeypatch):
    """将 episodic_memory.time.time 固定为 _BASE_NOW。"""
    import core.memory.episodic_memory as em
    monkeypatch.setattr(em.time, "time", lambda: _BASE_NOW)


# ─── helper ──────────────────────────────────────────────────────────────────

def _ep(occurred_at=None, temporal_ref="none", timestamp=None, summary="记忆片段", **kw):
    ts = timestamp if timestamp is not None else _BASE_NOW
    ep = {
        "id": "ep_test",
        "timestamp": ts,
        "occurred_at": occurred_at if occurred_at is not None else ts,
        "narrative_summary": summary,
        "strength": 0.8,
        "temporal_ref": temporal_ref,
        "emotion_peak": "sad",
    }
    ep.update(kw)
    return ep


# ─── P0-3A: occurred_at 用于时间锚 ───────────────────────────────────────────

def test_past_two_months_ago_not_recent(frozen_time):
    """occurred_at=两个月前 + temporal_ref=past → 渲染"N个月前"，不出现近期锚点。"""
    ep = _ep(occurred_at=_TWO_MONTHS_AGO, temporal_ref="past", timestamp=_BASE_NOW)
    result = format_for_prompt([ep], char_name="叶瑄")
    assert "个月前" in result or "天前" in result, f"应有月份/天数锚点: {result}"
    assert "刚刚" not in result, "不得出现'刚刚'"
    assert "几小时前" not in result, "不得出现'几小时前'"
    assert "今天" not in result, "不得出现'今天'"


def test_realtime_event_can_use_recent_anchor(frozen_time):
    """occurred_at=刚才 + temporal_ref=none → 可渲染"刚刚"（实时型保留细粒度）。"""
    ep = _ep(occurred_at=_BASE_NOW - 30, temporal_ref="none", timestamp=_BASE_NOW - 30)
    result = format_for_prompt([ep], char_name="叶瑄")
    assert "刚刚" in result, f"实时型应渲染'刚刚': {result}"


def test_past_ref_within_day_renders_ziqian(frozen_time):
    """occurred_at=刚才 + temporal_ref=past → 渲染'之前'，不出现'刚刚'/'几小时前'。"""
    ep = _ep(occurred_at=_ONE_HOUR_AGO, temporal_ref="past", timestamp=_BASE_NOW)
    result = format_for_prompt([ep], char_name="叶瑄")
    assert "之前" in result, f"past+<1天应渲染'之前': {result}"
    assert "刚刚" not in result
    assert "几小时前" not in result


def test_past_ref_2_days_renders_qianji(frozen_time):
    """occurred_at=2天前 + temporal_ref=past → 渲染'前几天'（days<3 分支）。"""
    ep = _ep(occurred_at=_BASE_NOW - 2 * 86400, temporal_ref="past", timestamp=_BASE_NOW)
    result = format_for_prompt([ep], char_name="叶瑄")
    assert "前几天" in result, f"past+2天应渲染'前几天': {result}"


def test_past_ref_many_months_renders_months(frozen_time):
    """occurred_at=两个月前 + temporal_ref=past → '2个月前'。"""
    ep = _ep(occurred_at=_TWO_MONTHS_AGO, temporal_ref="past", timestamp=_BASE_NOW)
    result = format_for_prompt([ep], char_name="叶瑄")
    assert "2个月前" in result, f"应渲染'2个月前': {result}"


def test_old_data_without_occurred_at_falls_back_to_timestamp(frozen_time):
    """旧数据无 occurred_at → 回退 timestamp，行为与改前一致。"""
    ep = {
        "id": "old_ep",
        "timestamp": _BASE_NOW - 30,   # 刚才
        "narrative_summary": "旧数据事件",
        "strength": 0.7,
        "temporal_ref": "none",
        "emotion_peak": "neutral",
        # 故意不设 occurred_at
    }
    result = format_for_prompt([ep], char_name="叶瑄")
    assert "刚刚" in result, f"旧数据应回退 timestamp 渲染'刚刚': {result}"


def test_none_occurred_at_falls_back_gracefully(frozen_time):
    """occurred_at=None（明确写 None）→ 回退 timestamp。"""
    ep = _ep(occurred_at=None, temporal_ref="none", timestamp=_BASE_NOW - 30)
    # 手动把 occurred_at 设为 None（write_episode 默认填充，这里测 format_for_prompt 防御）
    ep["occurred_at"] = None
    result = format_for_prompt([ep], char_name="叶瑄")
    assert result != "", "不应返回空"
    assert "刚刚" in result


# ─── P0-3B: _parse_turn_ms & _derive_occurred_at ─────────────────────────────

def test_parse_turn_ms_valid():
    """`_parse_turn_ms` 从 turn_id 提取毫秒→秒。"""
    ts = _parse_turn_ms("uid123_1700000000000")
    assert ts == pytest.approx(1_700_000_000.0)


def test_parse_turn_ms_invalid():
    """`_parse_turn_ms` 解析失败返回 None。"""
    assert _parse_turn_ms("no_ms_here") is None
    assert _parse_turn_ms("") is None
    assert _parse_turn_ms(None) is None


def test_derive_occurred_at_picks_earliest():
    """`_derive_occurred_at` 取批次中最早的 turn 时刻。"""
    ts_early = 1_699_990_000.0
    ts_late = 1_700_000_000.0
    entries = [
        {"source_turn_id": f"u_{int(ts_early * 1000)}", "ts": ts_late},
        {"source_turn_id": f"u_{int(ts_late * 1000)}", "ts": ts_late},
    ]
    result = _derive_occurred_at(entries, fallback=ts_late + 9999)
    assert result == pytest.approx(ts_early)


def test_derive_occurred_at_falls_back_to_ts_when_no_turn_id():
    """`source_turn_id` 缺失时，回退到 entry.ts。"""
    ts = 1_699_000_000.0
    entries = [{"ts": ts}]
    result = _derive_occurred_at(entries, fallback=ts + 9999)
    assert result == pytest.approx(ts)


def test_derive_occurred_at_falls_back_to_fallback_when_no_data():
    """所有来源都没有时间信息时回退 fallback。"""
    entries = [{"summary": "无时间信息"}]
    fallback = 1_700_000_000.0
    result = _derive_occurred_at(entries, fallback=fallback)
    assert result == pytest.approx(fallback)
