"""
tests/test_presence_model.py — P1 Presence Model unit tests.

Covers the 7 assertions mandated by the P1 spec:
  1. present + at_desk=240min + gap=238min → FOCUSED_SILENT, no forbidden phrases
  2. away 7min → BRIEFLY_AWAY, no "7分钟没回"
  3. away 45min → GENUINELY_ABSENT (absent semantics allowed)
  4. sleep window + idle>=300 → SLEEPING, empty summary
  5. chat gap < 2min → ACTIVE_CHATTING
  6. sensor_judge template: no bare minute fields, has {presence_summary}
"""

import pytest

from core.scheduler.presence_model import (
    Attribution,
    PhysicalPresence,
    derive_presence_state,
)

# Forbidden phrases that must never appear in non-GENUINELY_ABSENT summaries
FORBIDDEN = frozenset(["没理我", "不理我", "冷落", "未回复"])

BASE_NOW = 1_705_892_400.0  # 2024-01-22 13:00:00 UTC+8 (safe daytime)


def _assert_no_forbidden(text: str) -> None:
    for phrase in FORBIDDEN:
        assert phrase not in text, f"Forbidden phrase {phrase!r} in: {text!r}"


@pytest.fixture()
def daytime(monkeypatch):
    """Force is_quiet_sleep_time → False."""
    monkeypatch.setattr(
        "core.scheduler.presence_model.is_quiet_sleep_time",
        lambda *a, **kw: False,
    )


@pytest.fixture()
def nighttime(monkeypatch):
    """Force is_quiet_sleep_time → True (sleep window)."""
    monkeypatch.setattr(
        "core.scheduler.presence_model.is_quiet_sleep_time",
        lambda *a, **kw: True,
    )


# ── 1. present + at_desk=240min + conversational_gap=238min ──────────────────

class TestFocusedSilent:
    def test_attribution(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=10,
            continuous_at_desk_seconds=240 * 60,
            last_chat_at=now - 238 * 60,
            last_proactive_at=None,
            now=now,
        )
        assert ps.attribution == Attribution.FOCUSED_SILENT

    def test_physical_present(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=10,
            continuous_at_desk_seconds=240 * 60,
            last_chat_at=now - 238 * 60,
            last_proactive_at=None,
            now=now,
        )
        assert ps.physical == PhysicalPresence.PRESENT

    def test_gap_recorded(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=10,
            continuous_at_desk_seconds=240 * 60,
            last_chat_at=now - 238 * 60,
            last_proactive_at=None,
            now=now,
        )
        assert ps.conversational_gap_min == 238

    def test_no_forbidden_phrases(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=10,
            continuous_at_desk_seconds=240 * 60,
            last_chat_at=now - 238 * 60,
            last_proactive_at=None,
            now=now,
        )
        _assert_no_forbidden(ps.state_summary)

    def test_raw_gap_not_in_summary(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=10,
            continuous_at_desk_seconds=240 * 60,
            last_chat_at=now - 238 * 60,
            last_proactive_at=None,
            now=now,
        )
        assert "238" not in ps.state_summary
        assert "分钟没" not in ps.state_summary


# ── 2. away 7 min → BRIEFLY_AWAY ────────────────────────────────────────────

class TestBrieflyAway:
    def test_attribution(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=420,
            continuous_at_desk_seconds=0,
            last_chat_at=now - 30 * 60,
            last_proactive_at=None,
            now=now,
            away_since=now - 7 * 60,
        )
        assert ps.attribution == Attribution.BRIEFLY_AWAY

    def test_no_specific_minutes_in_summary(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=420,
            continuous_at_desk_seconds=0,
            last_chat_at=now - 30 * 60,
            last_proactive_at=None,
            now=now,
            away_since=now - 7 * 60,
        )
        assert "7" not in ps.state_summary
        assert "分钟没" not in ps.state_summary

    def test_no_forbidden_phrases(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=420,
            continuous_at_desk_seconds=0,
            last_chat_at=now - 30 * 60,
            last_proactive_at=None,
            now=now,
            away_since=now - 7 * 60,
        )
        _assert_no_forbidden(ps.state_summary)


# ── 3. away 45 min → GENUINELY_ABSENT ───────────────────────────────────────

class TestGenuinelyAbsent:
    def test_attribution(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=2700,
            continuous_at_desk_seconds=0,
            last_chat_at=now - 45 * 60,
            last_proactive_at=None,
            now=now,
            away_since=now - 45 * 60,
        )
        assert ps.attribution == Attribution.GENUINELY_ABSENT

    def test_summary_non_empty(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=2700,
            continuous_at_desk_seconds=0,
            last_chat_at=now - 45 * 60,
            last_proactive_at=None,
            now=now,
            away_since=now - 45 * 60,
        )
        # Absent semantics are allowed — just verify we get a non-empty summary
        assert ps.state_summary


# ── 4. sleep window + idle >= 300 → SLEEPING ─────────────────────────────────

class TestSleepWindow:
    def test_attribution(self, nighttime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=600,
            continuous_at_desk_seconds=0,
            last_chat_at=now - 200 * 60,
            last_proactive_at=None,
            now=now,
        )
        assert ps.attribution == Attribution.SLEEPING

    def test_sleep_window_flag(self, nighttime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=600,
            continuous_at_desk_seconds=0,
            last_chat_at=now - 200 * 60,
            last_proactive_at=None,
            now=now,
        )
        assert ps.is_sleep_window is True

    def test_summary_empty(self, nighttime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=600,
            continuous_at_desk_seconds=0,
            last_chat_at=now - 200 * 60,
            last_proactive_at=None,
            now=now,
        )
        assert ps.state_summary == ""

    def test_no_forbidden_phrases(self, nighttime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=600,
            continuous_at_desk_seconds=0,
            last_chat_at=now - 200 * 60,
            last_proactive_at=None,
            now=now,
        )
        _assert_no_forbidden(ps.state_summary)

    def test_below_idle_threshold_not_sleeping(self, nighttime):
        # idle < 300 → sleep guard doesn't fire even in sleep window
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=10,
            continuous_at_desk_seconds=10 * 60,
            last_chat_at=now - 60,
            last_proactive_at=None,
            now=now,
        )
        assert ps.attribution != Attribution.SLEEPING


# ── 5. chat gap < 2 min → ACTIVE_CHATTING ───────────────────────────────────

class TestActiveChatting:
    def test_attribution_recent_chat(self, daytime):
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=5,
            continuous_at_desk_seconds=30 * 60,
            last_chat_at=now - 60,  # 1 min ago
            last_proactive_at=None,
            now=now,
        )
        assert ps.attribution == Attribution.ACTIVE_CHATTING
        assert ps.conversational_gap_min == 1

    def test_attribution_no_chat_yet(self, daytime):
        # last_chat_at=None → conversational_gap_min=None → ACTIVE_CHATTING
        now = BASE_NOW
        ps = derive_presence_state(
            idle_seconds=5,
            continuous_at_desk_seconds=30 * 60,
            last_chat_at=None,
            last_proactive_at=None,
            now=now,
        )
        assert ps.attribution == Attribution.ACTIVE_CHATTING


# ── 6. sensor_judge template: no bare minute fields ─────────────────────────

class TestSensorJudgeTemplate:
    def test_no_bare_minute_fields(self):
        import core.scheduler.sensor_judge as sj
        tmpl = sj._USER_TEMPLATE
        assert "minutes_since_last_chat" not in tmpl
        assert "minutes_since_last_proactive" not in tmpl
        assert "continuous_at_desk_human" not in tmpl

    def test_presence_summary_field_present(self):
        import core.scheduler.sensor_judge as sj
        assert "{presence_summary}" in sj._USER_TEMPLATE
