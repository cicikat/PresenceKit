"""工单 E：在场统一口径 / 键鼠短历史 / 层 3.9 定性表述 / gating 键鼠融合。"""
import time

import pytest

from core.memory import realtime_state as rs
from core.scheduler import gating


def _payload(keys=0, clicks=0, idle=0, window=30, edit_hint=None):
    inp = {"keystrokes": keys, "mouse_clicks": clicks, "mouse_distance_px": 0, "idle_seconds": idle}
    if edit_hint:
        inp["edit_hint"] = edit_hint
    return {
        "window_seconds": window,
        "ts": time.time(),
        "sensor_version": "t",
        "input": inp,
        "focus": {"app": "code.exe", "title_hint": "", "switch_count": 0},
    }


@pytest.fixture(autouse=True)
def _clean():
    rs._reset_for_tests()
    yield
    rs._reset_for_tests()


def test_presence_unknown_without_data_or_when_stale():
    assert rs.get_presence() == "unknown"
    rs.update(_payload(idle=0))
    assert rs.get_presence() == "active"
    assert rs.get_presence(time.time() + rs.PRESENCE_FRESHNESS_SECONDS + 5) == "unknown"


def test_thresholds_shared_single_source():
    from core.scheduler import rhythm
    assert rhythm.PRESENCE_FRESHNESS_SECONDS == rs.PRESENCE_FRESHNESS_SECONDS
    assert rhythm.PRESENCE_IDLE_THRESHOLD_SECONDS == rs.IDLE_LEFT_THRESHOLD


def test_input_history_bounded_in_memory():
    for _ in range(rs.INPUT_HISTORY_MAX_ENTRIES * 3):
        rs.update(_payload(keys=10))
    assert len(rs._input_history) <= rs.INPUT_HISTORY_MAX_ENTRIES


def test_describe_input_activity_qualitative_and_needs_coverage():
    rs.update(_payload(keys=100))
    assert rs.describe_input_activity() == []  # 覆盖仅 30s，不下结论
    for _ in range(3):
        rs.update(_payload(keys=100, clicks=1))
    phrases = rs.describe_input_activity()
    assert any("快速打字" in p for p in phrases)
    assert not any(ch.isdigit() for p in phrases for ch in p)


def test_describe_input_activity_none_when_stale():
    for _ in range(3):
        rs.update(_payload(keys=100))
    assert rs.describe_input_activity(time.time() + 600) == []


def test_prompt_layer_uses_derived_phrases():
    from core import prompt_builder
    for _ in range(4):
        rs.update(_payload(keys=40, idle=0))
    text = prompt_builder._format_realtime_awareness({"query.what_doing"})
    assert "认真打字" in text or "快速打字" in text


def test_desk_busy_requires_fresh_data_and_recent_chat(monkeypatch):
    from core.scheduler import loop
    # 无数据 -> 不 busy，presence unknown
    sig = gating._desk_busy_signal()
    assert sig["presence"] == "unknown" and sig["busy"] is False

    rs.update(_payload(idle=0))
    # 5 分钟前聊过 + 正在键鼠操作 -> 在场但在忙
    monkeypatch.setattr(loop, "_last_user_message_time", time.time() - 300)
    sig = gating._desk_busy_signal()
    assert sig["attribution"] == "PRESENT_IDLE" and sig["busy"] is True
    # 聊天静默 >=30 分钟 -> FOCUSED_SILENT，不拦截
    monkeypatch.setattr(loop, "_last_user_message_time", time.time() - 3600)
    sig = gating._desk_busy_signal()
    assert sig["attribution"] == "FOCUSED_SILENT" and sig["busy"] is False
    # 键鼠停了 -> 不 busy
    rs.update(_payload(idle=400))
    monkeypatch.setattr(loop, "_last_user_message_time", time.time() - 300)
    assert gating._desk_busy_signal()["busy"] is False
