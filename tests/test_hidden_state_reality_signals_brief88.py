"""
tests/test_hidden_state_reality_signals_brief88.py
===================================================
Brief 88 — user_hidden_state 现实侧接线：全量信号映射验收测试。

Covers:
  A. integrate_event 新分支：BODY_TOPIC / AFFECTION_EXPRESSED（中期层 delta + 长期层零写入）
  B. process_reality_turn（§1 对话侧判定 + §3 body_memory cue 接线）
     - SEEK_COMPANIONSHIP (a) 6h gap 边界：5h59m 不触发 / 6h00m 触发
     - SEEK_COMPANIONSHIP (b) 词表命中
     - RECEIVED_COMFORT tags ∩ assistant_emotion 联合判定
     - BODY_TOPIC tags 判定
     - AFFECTION_EXPRESSED 词表判定
     - trigger 轮零参与
     - 同轮 BODY_TOPIC + AFFECTION 并发，各自 capped，总变化 ≤ 2×MAX_NUDGE_PER_EVENT
     - envelope.can_write_memory=False 时 §3 跳过但 §1 中期层照常
     - §3 body_memory cue 来自命中词（query.body_state 命中不产生 cue）
  C. NO_INTERACTION 调度侧（hidden_state_decay._check_hidden_state_decay）
     - presence gap ≥ 24h → accrue
     - 同一逻辑日重复 tick 只 accrue 一次
     - gap < 24h → 不触发
  D. trigger_counts 观测（get_trigger_counts）
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from core.memory.user_hidden_state import MAX_NUDGE_PER_EVENT, default_hidden_state
from core.memory.user_hidden_state_integrator import (
    AFFECTION_DEFICIT_DISCHARGE_AMOUNT,
    AFFECTION_SENS_NUDGE,
    BODY_TOPIC_SENS_NUDGE,
    RealityEventType,
    get_trigger_counts,
    integrate_event,
)
from core.memory.user_hidden_state_reality_signals import process_reality_turn
from core.memory.user_hidden_state_store import load_hidden_state, save_hidden_state
from core.write_envelope import WriteEnvelope, stamp_user_chat

NOW = "2026-06-02T00:00:00Z"


def _open_envelope() -> WriteEnvelope:
    return stamp_user_chat()


# ═══════════════════════════════════════════════════════════════════════════════
# A. integrate_event — BODY_TOPIC / AFFECTION_EXPRESSED
# ═══════════════════════════════════════════════════════════════════════════════

class TestBodyTopicEvent:
    def test_sensitivity_current_increases(self):
        state = default_hidden_state()
        original = state.sensitivity.current.value
        state, result = integrate_event(RealityEventType.BODY_TOPIC, state, _open_envelope(), NOW)
        assert result.accepted
        assert state.sensitivity.current.value == pytest.approx(original + BODY_TOPIC_SENS_NUDGE)

    def test_touch_deficit_untouched(self):
        state = default_hidden_state()
        state.touch_need.deficit.value = 33.0
        state, _ = integrate_event(RealityEventType.BODY_TOPIC, state, _open_envelope(), NOW)
        assert state.touch_need.deficit.value == 33.0

    def test_long_term_fields_untouched(self):
        state = default_hidden_state()
        baseline = state.sensitivity.baseline.value
        ease = state.embodied_ease.value
        state, _ = integrate_event(RealityEventType.BODY_TOPIC, state, _open_envelope(), NOW)
        assert state.sensitivity.baseline.value == baseline
        assert state.embodied_ease.value == ease
        assert state.body_memory.entries == []


class TestAffectionExpressedEvent:
    def test_deficit_decreases_and_sensitivity_increases(self):
        state = default_hidden_state()
        state.touch_need.deficit.value = 20.0
        original_sens = state.sensitivity.current.value
        state, result = integrate_event(RealityEventType.AFFECTION_EXPRESSED, state, _open_envelope(), NOW)
        assert result.accepted
        assert state.touch_need.deficit.value == pytest.approx(20.0 - AFFECTION_DEFICIT_DISCHARGE_AMOUNT)
        assert state.sensitivity.current.value == pytest.approx(original_sens + AFFECTION_SENS_NUDGE)
        assert len(result.touched_fields) == 2

    def test_deltas_within_max_nudge_cap(self):
        assert BODY_TOPIC_SENS_NUDGE <= MAX_NUDGE_PER_EVENT
        assert AFFECTION_SENS_NUDGE <= MAX_NUDGE_PER_EVENT

    def test_long_term_fields_untouched(self):
        state = default_hidden_state()
        baseline = state.touch_need.baseline.value
        state, _ = integrate_event(RealityEventType.AFFECTION_EXPRESSED, state, _open_envelope(), NOW)
        assert state.touch_need.baseline.value == baseline
        assert state.body_memory.entries == []


# ═══════════════════════════════════════════════════════════════════════════════
# B. process_reality_turn — §1 对话侧 + §3 body_memory
# ═══════════════════════════════════════════════════════════════════════════════

UID = "u_reality_88"
CHAR = "yexuan"


def _load(uid=UID, char_id=CHAR):
    return load_hidden_state(uid, char_id=char_id)


class TestSeekCompanionshipGapBoundary:
    def test_5h59m_does_not_trigger(self, sandbox):
        triggered = process_reality_turn(
            uid=UID, content="今天天气不错", tags=set(), assistant_emotion="neutral",
            trigger_name="", envelope=_open_envelope(),
            prior_gap_seconds=5 * 3600 + 59 * 60, char_id=CHAR,
        )
        assert RealityEventType.SEEK_COMPANIONSHIP.value not in triggered

    def test_6h00m_triggers(self, sandbox):
        triggered = process_reality_turn(
            uid=UID, content="今天天气不错", tags=set(), assistant_emotion="neutral",
            trigger_name="", envelope=_open_envelope(),
            prior_gap_seconds=6 * 3600, char_id=CHAR,
        )
        assert RealityEventType.SEEK_COMPANIONSHIP.value in triggered

    def test_keyword_triggers_regardless_of_gap(self, sandbox):
        triggered = process_reality_turn(
            uid=UID, content="在吗", tags=set(), assistant_emotion="neutral",
            trigger_name="", envelope=_open_envelope(),
            prior_gap_seconds=None, char_id=CHAR,
        )
        assert RealityEventType.SEEK_COMPANIONSHIP.value in triggered


class TestReceivedComfort:
    def test_tags_and_emotion_both_required(self, sandbox):
        triggered = process_reality_turn(
            uid=UID, content="有点难过", tags={"emotion.down"}, assistant_emotion="gentle",
            trigger_name="", envelope=_open_envelope(), prior_gap_seconds=None, char_id=CHAR,
        )
        assert RealityEventType.RECEIVED_COMFORT.value in triggered

    def test_tag_hit_without_matching_emotion_does_not_trigger(self, sandbox):
        triggered = process_reality_turn(
            uid=UID, content="有点难过", tags={"emotion.down"}, assistant_emotion="happy",
            trigger_name="", envelope=_open_envelope(), prior_gap_seconds=None, char_id=CHAR,
        )
        assert RealityEventType.RECEIVED_COMFORT.value not in triggered


class TestBodyTopicTagJudgment:
    def test_body_intimate_tag_triggers(self, sandbox):
        triggered = process_reality_turn(
            uid=UID, content="做爱吧", tags={"body_intimate"}, assistant_emotion="neutral",
            trigger_name="", envelope=_open_envelope(), prior_gap_seconds=None, char_id=CHAR,
        )
        assert RealityEventType.BODY_TOPIC.value in triggered


class TestAffectionWordJudgment:
    def test_affection_word_triggers(self, sandbox):
        triggered = process_reality_turn(
            uid=UID, content="抱抱我好不好", tags=set(), assistant_emotion="neutral",
            trigger_name="", envelope=_open_envelope(), prior_gap_seconds=None, char_id=CHAR,
        )
        assert RealityEventType.AFFECTION_EXPRESSED.value in triggered


class TestTriggerTurnZeroParticipation:
    def test_trigger_round_produces_no_events_and_no_writes(self, sandbox):
        before = _load()
        triggered = process_reality_turn(
            uid=UID, content="在吗 抱抱", tags={"body_intimate"}, assistant_emotion="gentle",
            trigger_name="some_scheduler_trigger", envelope=_open_envelope(),
            prior_gap_seconds=100000, char_id=CHAR,
        )
        assert triggered == []
        after = _load()
        assert after.touch_need.deficit.value == before.touch_need.deficit.value
        assert after.sensitivity.current.value == before.sensitivity.current.value
        assert after.body_memory.entries == before.body_memory.entries


class TestConcurrentBodyTopicAndAffectionCapped:
    def test_both_fire_and_total_delta_within_cap(self, sandbox):
        before = _load()
        original_sens = before.sensitivity.current.value
        triggered = process_reality_turn(
            uid=UID, content="抱抱我，我们做爱吧", tags={"body_intimate"}, assistant_emotion="neutral",
            trigger_name="", envelope=_open_envelope(), prior_gap_seconds=None, char_id=CHAR,
        )
        assert RealityEventType.BODY_TOPIC.value in triggered
        assert RealityEventType.AFFECTION_EXPRESSED.value in triggered

        after = _load()
        total_sens_delta = after.sensitivity.current.value - original_sens
        assert 0 < total_sens_delta <= 2 * MAX_NUDGE_PER_EVENT
        # 具体值：BODY_TOPIC(+2.0) + AFFECTION_EXPRESSED(+1.0) = 3.0，各自独立 capped。
        assert total_sens_delta == pytest.approx(BODY_TOPIC_SENS_NUDGE + AFFECTION_SENS_NUDGE)


class TestEnvelopeGateBoundary:
    def test_can_write_memory_false_skips_body_memory_but_midterm_proceeds(self, sandbox):
        uid = "u_envelope_gate_88"
        before = load_hidden_state(uid, char_id=CHAR)
        original_sens = before.sensitivity.current.value

        closed_envelope = WriteEnvelope(can_write_memory=False)
        triggered = process_reality_turn(
            uid=uid, content="抱抱我，我们做爱吧", tags={"body_intimate"}, assistant_emotion="neutral",
            trigger_name="", envelope=closed_envelope, prior_gap_seconds=None, char_id=CHAR,
        )
        # §1 中期层：即便调用方 envelope.can_write_memory=False，本模块内部固定用
        # stamp_user_chat()，只看 trigger_name，因此依然照常触发。
        assert RealityEventType.BODY_TOPIC.value in triggered
        assert RealityEventType.AFFECTION_EXPRESSED.value in triggered

        after = load_hidden_state(uid, char_id=CHAR)
        assert after.sensitivity.current.value != original_sens, "§1 中期层应照常写入"
        # §3 长期层：调用方 envelope.can_write_memory=False → body_memory 不落盘
        assert after.body_memory.entries == [], "§3 body_memory 必须在 envelope 关闭时跳过"


class TestBodyMemoryCueWiring:
    def test_affection_word_becomes_cue(self, sandbox):
        uid = "u_cue_affection_88"
        process_reality_turn(
            uid=uid, content="贴贴一下嘛", tags=set(), assistant_emotion="gentle",
            trigger_name="", envelope=_open_envelope(), prior_gap_seconds=None, char_id=CHAR,
        )
        state = load_hidden_state(uid, char_id=CHAR)
        cues = [e.cue for e in state.body_memory.entries]
        assert "贴贴" in cues

    def test_body_intimate_tag_word_becomes_cue(self, sandbox):
        uid = "u_cue_body_topic_88"
        process_reality_turn(
            uid=uid, content="我们上床吧", tags={"body_intimate"}, assistant_emotion="gentle",
            trigger_name="", envelope=_open_envelope(), prior_gap_seconds=None, char_id=CHAR,
        )
        state = load_hidden_state(uid, char_id=CHAR)
        cues = [e.cue for e in state.body_memory.entries]
        assert "上床" in cues

    def test_query_body_state_only_produces_no_cue(self, sandbox):
        uid = "u_cue_query_only_88"
        triggered = process_reality_turn(
            uid=uid, content="你今天状态怎么样", tags={"query.body_state"}, assistant_emotion="gentle",
            trigger_name="", envelope=_open_envelope(), prior_gap_seconds=None, char_id=CHAR,
        )
        assert RealityEventType.BODY_TOPIC.value in triggered
        state = load_hidden_state(uid, char_id=CHAR)
        assert state.body_memory.entries == [], "query.body_state 不是可条件化线索，不应产生 body_memory cue"


# ═══════════════════════════════════════════════════════════════════════════════
# C. NO_INTERACTION — 调度侧（hidden_state_decay._check_hidden_state_decay）
# ═══════════════════════════════════════════════════════════════════════════════

def _make_registry(*char_ids: str) -> MagicMock:
    reg = MagicMock()
    entries = []
    for cid in char_ids:
        e = MagicMock()
        e.id = cid
        entries.append(e)
    reg.list_all.return_value = entries
    return reg


class TestNoInteractionScheduling:
    def _seed_hidden_state_and_presence(self, sandbox, uid: str, char_id: str, gap_seconds: float):
        state = default_hidden_state()
        state.touch_need.deficit.value = 10.0
        save_hidden_state(uid, state, char_id=char_id)

        presence_path = sandbox.presence(char_id=char_id)
        presence_path.parent.mkdir(parents=True, exist_ok=True)
        presence_path.write_text(
            json.dumps({uid: {"last_message_at": time.time() - gap_seconds}}), encoding="utf-8"
        )

    def test_gap_over_24h_accrues_once(self, sandbox):
        from core.scheduler.triggers import hidden_state_decay as _hsd

        uid = "u_no_interaction_88"
        self._seed_hidden_state_and_presence(sandbox, uid, CHAR, gap_seconds=25 * 3600)

        with patch("core.scheduler.loop._is_ready", return_value=True), \
             patch("core.scheduler.loop._mark"), \
             patch("core.asset_registry.get_registry", return_value=_make_registry(CHAR)):
            asyncio.run(_hsd._check_hidden_state_decay())

        state = load_hidden_state(uid, char_id=CHAR)
        assert state.touch_need.deficit.value > 10.0, "gap ≥ 24h 必须 accrue NO_INTERACTION"

    def test_gap_under_24h_does_not_accrue(self, sandbox):
        from core.scheduler.triggers import hidden_state_decay as _hsd

        uid = "u_no_interaction_short_88"
        self._seed_hidden_state_and_presence(sandbox, uid, CHAR, gap_seconds=3 * 3600)

        with patch("core.scheduler.loop._is_ready", return_value=True), \
             patch("core.scheduler.loop._mark"), \
             patch("core.asset_registry.get_registry", return_value=_make_registry(CHAR)):
            asyncio.run(_hsd._check_hidden_state_decay())

        state = load_hidden_state(uid, char_id=CHAR)
        assert state.touch_need.deficit.value == pytest.approx(10.0), "gap < 24h 不应触发"

    def test_same_logical_day_repeat_tick_accrues_only_once(self, sandbox):
        from core.scheduler.triggers import hidden_state_decay as _hsd

        uid = "u_no_interaction_dedup_88"
        self._seed_hidden_state_and_presence(sandbox, uid, CHAR, gap_seconds=25 * 3600)

        with patch("core.scheduler.loop._is_ready", return_value=True), \
             patch("core.scheduler.loop._mark"), \
             patch("core.asset_registry.get_registry", return_value=_make_registry(CHAR)):
            asyncio.run(_hsd._check_hidden_state_decay())
            state_after_first = load_hidden_state(uid, char_id=CHAR)

            # 模拟"重启"：本函数不依赖任何进程内状态，第二次调用即等价于重启后重跑。
            asyncio.run(_hsd._check_hidden_state_decay())
            state_after_second = load_hidden_state(uid, char_id=CHAR)

        assert state_after_second.touch_need.deficit.value == pytest.approx(
            state_after_first.touch_need.deficit.value
        ), "同一逻辑日重复 tick 不应二次 accrue（重启后也不应重复）"


# ═══════════════════════════════════════════════════════════════════════════════
# D. trigger_counts 观测
# ═══════════════════════════════════════════════════════════════════════════════

class TestTriggerCounts:
    def test_accepted_event_bumps_counter(self):
        before = get_trigger_counts().get(RealityEventType.BODY_TOPIC.value, 0)
        state = default_hidden_state()
        integrate_event(RealityEventType.BODY_TOPIC, state, _open_envelope(), NOW)
        after = get_trigger_counts().get(RealityEventType.BODY_TOPIC.value, 0)
        assert after == before + 1

    def test_rejected_event_does_not_bump_counter(self):
        before = get_trigger_counts().get(RealityEventType.AFFECTION_EXPRESSED.value, 0)
        state = default_hidden_state()
        integrate_event(
            RealityEventType.AFFECTION_EXPRESSED, state, WriteEnvelope(can_write_memory=False), NOW
        )
        after = get_trigger_counts().get(RealityEventType.AFFECTION_EXPRESSED.value, 0)
        assert after == before
