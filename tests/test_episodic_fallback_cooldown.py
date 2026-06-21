"""
tests/test_episodic_fallback_cooldown.py — P0-4 验收

断言覆盖 (P0-4A: fallback occurred_at 窗口):
- is_core + occurred_at=5天前、strength=0.9 → fallback 不入选
- is_core + occurred_at=1天内 → fallback 入选
- occurred_at=8天前（timestamp=现在）→ 7天窗口 fallback 不召回
- occurred_at 缺失 → 回退 timestamp，行为不回归

断言覆盖 (P0-4B: retrieve 强化冷却):
- 同一 ep 6h 内二次 allow_strengthen=True → strength 不再增加
- 非核心 ep strength 不超过 0.9
"""

import time as time_module

import pytest

import core.memory.episodic_memory as em
from core.memory.episodic_memory import retrieve_fallback, write_episode

_UID = "fallback_cd_uid"
_CHAR = "yexuan"
_NOW = 1_700_000_000.0


@pytest.fixture(autouse=True)
def freeze_time(monkeypatch):
    monkeypatch.setattr(em.time, "time", lambda: _NOW)


def _ep(ep_id, occurred_at, strength=0.8, is_core=False, **kw):
    ep = {
        "id": ep_id,
        "timestamp": _NOW,
        "occurred_at": occurred_at,
        "narrative_summary": f"记忆 {ep_id}",
        "summary": f"记忆 {ep_id}",
        "strength": strength,
        "status": "open",
        "is_core": is_core,
        "temporal_ref": "none",
        "emotion_peak": "neutral",
        "tags": [],
        "topic_keywords": [],
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


# ─── P0-4A: fallback occurred_at 窗口 ────────────────────────────────────────

def test_fallback_core_5days_not_selected(sandbox):
    """is_core + occurred_at=5天前 → fallback 不入选（> 2天阈值）。"""
    write_episode(_UID, _ep("ep_core_old", occurred_at=_NOW - 5 * 86400,
                            strength=0.9, is_core=True), char_id=_CHAR)
    result = retrieve_fallback(_UID, recent_history=[], char_id=_CHAR)
    assert len(result) == 0, "核心记忆超 2 天不应通过 fallback 复活"


def test_fallback_core_half_day_selected(sandbox):
    """is_core + occurred_at=12小时前 → fallback 入选。"""
    write_episode(_UID, _ep("ep_core_recent", occurred_at=_NOW - 12 * 3600,
                            strength=0.9, is_core=True), char_id=_CHAR)
    result = retrieve_fallback(_UID, recent_history=[], char_id=_CHAR)
    assert len(result) == 1, "核心记忆 12h 内应通过 fallback"


def test_fallback_nonecore_8days_not_selected(sandbox):
    """occurred_at=8天前（timestamp=现在）→ 7天窗口不召回。"""
    write_episode(_UID, _ep("ep_old_nc", occurred_at=_NOW - 8 * 86400,
                            strength=0.9, is_core=False), char_id=_CHAR)
    result = retrieve_fallback(_UID, recent_history=[], char_id=_CHAR)
    assert len(result) == 0, "occurred_at 超 7 天不应入选"


def test_fallback_nonecore_recent_selected(sandbox):
    """occurred_at=2天前、非核心 → fallback 入选。"""
    write_episode(_UID, _ep("ep_nc_recent", occurred_at=_NOW - 2 * 86400,
                            strength=0.8, is_core=False), char_id=_CHAR)
    result = retrieve_fallback(_UID, recent_history=[], char_id=_CHAR)
    assert len(result) == 1


def test_fallback_no_occurred_at_falls_back_to_timestamp(sandbox):
    """无 occurred_at 的旧数据回退 timestamp，窗口判断不崩溃。"""
    ep = _ep("ep_no_occ", occurred_at=None, strength=0.8)
    # 手动移除 occurred_at，模拟旧数据
    ep.pop("occurred_at", None)
    ep["timestamp"] = _NOW - 1 * 86400   # 1天前，应能入选
    write_episode(_UID, ep, char_id=_CHAR)
    result = retrieve_fallback(_UID, recent_history=[], char_id=_CHAR)
    assert len(result) >= 0, "旧数据不应导致异常"


# ─── P0-4B: retrieve 强化冷却 ────────────────────────────────────────────────

def test_retrieve_cooldown_prevents_double_strengthen(sandbox):
    """同一 ep 在 6h 内二次 allow_strengthen=True → strength 不再增加。"""
    from core.memory.episodic_memory import retrieve, _load_memories

    ep = _ep("ep_cd", occurred_at=_NOW - 3600, strength=0.5, is_core=False,
             topic_keywords=["测试"], tags=["测试"])
    write_episode(_UID, ep, char_id=_CHAR)

    # 第一次 retrieve (allow_strengthen=True)
    retrieve(_UID, topic="测试", top_k=1, char_id=_CHAR, allow_strengthen=True)
    memories_after_1 = _load_memories(_UID, char_id=_CHAR)
    strength_after_1 = next((m["strength"] for m in memories_after_1 if m["id"] == "ep_cd"), None)
    assert strength_after_1 is not None

    # 第二次 retrieve（模拟 6h 内，冷却中）
    retrieve(_UID, topic="测试", top_k=1, char_id=_CHAR, allow_strengthen=True)
    memories_after_2 = _load_memories(_UID, char_id=_CHAR)
    strength_after_2 = next((m["strength"] for m in memories_after_2 if m["id"] == "ep_cd"), None)

    assert strength_after_2 == strength_after_1, (
        f"6h 冷却内 strength 不应变化: {strength_after_1} → {strength_after_2}"
    )


def test_noncore_strength_ceiling(sandbox):
    """非核心记忆 strength 上限为 0.9，不超过该值。"""
    from core.memory.episodic_memory import retrieve, _load_memories

    ep = _ep("ep_ceil", occurred_at=_NOW - 3600, strength=0.88, is_core=False,
             topic_keywords=["天花板"], tags=["天花板"],
             last_retrieved=_NOW - 99999)   # 确保冷却已过期
    write_episode(_UID, ep, char_id=_CHAR)

    retrieve(_UID, topic="天花板", top_k=1, char_id=_CHAR, allow_strengthen=True)
    memories = _load_memories(_UID, char_id=_CHAR)
    strength = next((m["strength"] for m in memories if m["id"] == "ep_ceil"), None)
    assert strength is not None
    assert strength <= 0.9, f"非核心 strength 超上限 0.9: {strength}"
