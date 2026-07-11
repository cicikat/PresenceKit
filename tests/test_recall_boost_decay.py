"""
tests/test_recall_boost_decay.py — Brief 47 §1 验收

召回增强收益递减：boost = 0.15 / (1 + retrieval_count_before)，替代原来每次固定 +0.15。

覆盖：
- 同一条记忆连续召回 10 次：strength 增量总和 < 0.5（对照现行为 1.5），clamp 生效
- 召回 0 次的记忆行为与现行为一致（回归保护）
"""

import pytest

import core.memory.episodic_memory as em
from core.memory.episodic_memory import retrieve, write_episode, _load_memories

_UID = "boost_decay_uid"
_CHAR = "yexuan"
_START = 1_700_000_000.0


def _ep(ep_id, **kw):
    ep = {
        "id": ep_id,
        "timestamp": _START,
        "occurred_at": _START,
        "narrative_summary": f"记忆 {ep_id}",
        "summary": f"记忆 {ep_id}",
        "strength": 0.3,
        "status": "open",
        "is_core": True,
        "temporal_ref": "none",
        "emotion_peak": "neutral",
        "tags": ["测试"],
        "topic_keywords": ["测试"],
        "raw_facts": [],
        "retrieval_count": 0,
        "last_retrieved": None,
        "resolved_at": None,
        "resolved_by": None,
        "event_time": None,
        "expires_at": None,
        "source_mid_ids": [],
    }
    ep.update(kw)
    return ep


def test_repeated_retrieve_boost_diminishes(sandbox, monkeypatch):
    """同一条记忆连续召回 10 次：strength 增量总和 < 0.5（对照现行为 1.5），clamp 生效。"""
    clock = {"t": _START}
    monkeypatch.setattr(em.time, "time", lambda: clock["t"])

    write_episode(_UID, _ep("ep_boost"), char_id=_CHAR)

    strength_before = 0.3
    for _ in range(10):
        clock["t"] += 21601  # 越过 6h 冷却窗口
        retrieve(_UID, topic="测试", top_k=1, char_id=_CHAR, allow_strengthen=True)

    memories = _load_memories(_UID, char_id=_CHAR)
    mem = next(m for m in memories if m["id"] == "ep_boost")

    total_gain = mem["strength"] - strength_before
    assert total_gain < 0.5, f"10 次召回增量总和应 < 0.5（对照现行为 1.5），实际 {total_gain}"
    assert mem["retrieval_count"] == 10
    assert mem["strength"] <= 1.0


def test_never_retrieved_memory_unaffected(sandbox, monkeypatch):
    """未被选中召回的记忆行为与现行为一致：strength / retrieval_count 保持不变（回归保护）。"""
    monkeypatch.setattr(em.time, "time", lambda: _START)

    write_episode(
        _UID,
        _ep("ep_untouched", topic_keywords=["无关话题"], tags=["无关话题"]),
        char_id=_CHAR,
    )

    retrieve(_UID, topic="测试", top_k=1, char_id=_CHAR, allow_strengthen=True)

    memories = _load_memories(_UID, char_id=_CHAR)
    mem = next(m for m in memories if m["id"] == "ep_untouched")
    assert mem["strength"] == 0.3
    assert mem["retrieval_count"] == 0
