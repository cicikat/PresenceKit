"""
tests/test_identity_counter_decay.py — Brief 47 §2 验收（known-issues identity-1）

_decay_counter_evidence()：counter_evidence_count 按 last_conflict_at 半衰期（30 天）衰减，
避免历史反证把某个 identity 维度永久压死。

覆盖：
- counter=4，last_conflict_at=60 天前 → 衰减后 1
- counter=4，last_conflict_at=30 天前 → 衰减后 2
- last_conflict_at 缺失（旧数据）→ 不衰减，保持 4（兼容层）
- 衰减未产生变化（同值）→ 不出现在 events 里
"""

import time

from core.memory.fixation_pipeline import _decay_counter_evidence

_NOW = 1_700_000_000.0
_DAY = 86400


def _identity(counter, last_conflict_at):
    dim = {
        "text": "示例判断",
        "confidence": 0.8,
        "evidence_count": 10,
        "counter_evidence_count": counter,
        "last_updated": _NOW,
    }
    if last_conflict_at is not None:
        dim["last_conflict_at"] = last_conflict_at
    return {"trust_pattern": dim}


def test_counter_decays_60_days_to_1():
    old_identity = _identity(4, _NOW - 60 * _DAY)
    decayed, events = _decay_counter_evidence(old_identity, _NOW)

    assert decayed["trust_pattern"]["counter_evidence_count"] == 1
    assert events == [{"key": "trust_pattern", "before": 4, "after": 1}]


def test_counter_decays_30_days_to_2():
    old_identity = _identity(4, _NOW - 30 * _DAY)
    decayed, events = _decay_counter_evidence(old_identity, _NOW)

    assert decayed["trust_pattern"]["counter_evidence_count"] == 2
    assert events == [{"key": "trust_pattern", "before": 4, "after": 2}]


def test_missing_last_conflict_at_is_compat_noop():
    old_identity = _identity(4, None)
    decayed, events = _decay_counter_evidence(old_identity, _NOW)

    assert decayed["trust_pattern"]["counter_evidence_count"] == 4
    assert events == []


def test_no_change_produces_no_event():
    # last_conflict_at 就是当下（刚发生的冲突）→ days_since=0 → 衰减因子=1，无变化
    old_identity = _identity(4, _NOW)
    decayed, events = _decay_counter_evidence(old_identity, _NOW)

    assert decayed["trust_pattern"]["counter_evidence_count"] == 4
    assert events == []


def test_decay_below_1_clears_last_conflict_at():
    old_identity = _identity(1, _NOW - 60 * _DAY)
    decayed, events = _decay_counter_evidence(old_identity, _NOW)

    assert decayed["trust_pattern"]["counter_evidence_count"] == 0
    assert decayed["trust_pattern"]["last_conflict_at"] is None
    assert events == [{"key": "trust_pattern", "before": 1, "after": 0}]
